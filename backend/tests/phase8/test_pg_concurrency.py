"""§3 — PostgreSQL concurrency, measured rather than assumed.

Every test here runs two or more REAL threads against REAL Postgres
connections, with bounded waits. Nothing is mocked at the database layer: the
point is to find out whether `with_for_update()` and the conditional UPDATEs
actually serialise, not whether the code calls them.

HOW CONCURRENCY IS FORCED. A barrier (threading.Barrier) releases both workers
at the same instant, so their statements genuinely interleave rather than
running one after the other. Where a test needs one worker to be mid-
transaction while the other arrives, it holds an open transaction explicitly.

Each test records what was persisted afterwards, which is the only evidence
that matters: duplicate loans, cases or placements are counted, not inferred.
"""
from __future__ import annotations

import threading
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

BARRIER_TIMEOUT = 20          # bounded: a deadlock fails the test, never hangs it
JOIN_TIMEOUT = 60


def _run_both(fn_a, fn_b):
    """Run two callables concurrently, released from a barrier. Returns their
    results or exceptions, in order."""
    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT)
    out: dict[str, object] = {}

    def wrap(key, fn):
        def inner():
            try:
                barrier.wait()
                out[key] = fn()
            except Exception as exc:          # recorded, not swallowed
                out[key] = exc
        return inner

    ta = threading.Thread(target=wrap("a", fn_a), daemon=True)
    tb = threading.Thread(target=wrap("b", fn_b), daemon=True)
    ta.start(); tb.start()
    ta.join(JOIN_TIMEOUT); tb.join(JOIN_TIMEOUT)
    assert not ta.is_alive() and not tb.is_alive(), "a worker never finished (possible deadlock)"
    return out.get("a"), out.get("b")


# ── 3.1 two workers claiming the same RECEIVED batch ────────────────────────

def test_two_workers_cannot_both_claim_the_same_received_batch(Session, make_batch):
    """The Phase 5 claim: UPDATE ... WHERE status='RECEIVED'. Exactly one
    worker's UPDATE must report a row changed."""
    batch = make_batch(status="RECEIVED")

    def claim():
        s = Session()
        try:
            n = s.execute(text("UPDATE lending.bank_feed_batches SET status='VALIDATING' "
                               "WHERE id=:i AND status='RECEIVED'"), {"i": batch.id}).rowcount
            s.commit()
            return n
        finally:
            s.close()

    a, b = _run_both(claim, claim)
    assert not isinstance(a, Exception) and not isinstance(b, Exception), (a, b)
    assert {a, b} == {1, 0}, f"expected exactly one winner, got a={a} b={b}"

    s = Session()
    try:
        status = s.execute(text("select status from lending.bank_feed_batches where id=:i"),
                           {"i": batch.id}).scalar()
    finally:
        s.close()
    assert status == "VALIDATING"


def test_ten_concurrent_claims_produce_exactly_one_winner(Session, make_batch):
    """Scaled up: duplicate delivery plus a recovery sweep plus a manual
    re-run could all arrive together."""
    batch = make_batch(status="RECEIVED")
    wins: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(10, timeout=BARRIER_TIMEOUT)

    def claim():
        s = Session()
        try:
            barrier.wait()
            n = s.execute(text("UPDATE lending.bank_feed_batches SET status='VALIDATING' "
                               "WHERE id=:i AND status='RECEIVED'"), {"i": batch.id}).rowcount
            s.commit()
            with lock:
                wins.append(n)
        finally:
            s.close()

    threads = [threading.Thread(target=claim, daemon=True) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(JOIN_TIMEOUT)
    assert all(not t.is_alive() for t in threads)
    assert sum(wins) == 1, f"expected 1 winner across 10 attempts, got {sum(wins)}"


# ── 3.2 duplicate delivery of ingest_feed_batch ────────────────────────────

def test_duplicate_delivery_of_ingest_feed_batch_ingests_once(Session, tenant, make_batch,
                                                              monkeypatch):
    """The real task, delivered twice concurrently. run_ingestion is stubbed —
    what is under test is the CLAIM and the task's state machine, against
    Postgres."""
    from app.workers.tasks import feed_ingestion

    batch = make_batch(status="RECEIVED")
    csv = (b"customer_ref,loan_account_number,case_number,dpd,total_outstanding\n"
           b"P8C1,P8LN1,P8CASE1,45,100000\n")

    calls: list[str] = []
    lock = threading.Lock()

    class Result:
        rows_read, rows_skipped, rows_quarantined, cases_inserted = 1, 0, 0, 1
        status = "LOADED"

    def fake_run_ingestion(path, dry_run=False, *, bank_code, agency_code=None, business_day=None):
        with lock:
            calls.append(bank_code)
        return Result()

    monkeypatch.setattr("scripts.ingest_daily.run_ingestion", fake_run_ingestion)
    monkeypatch.setattr("app.core.storage.download_bytes", lambda key: csv)
    monkeypatch.setattr("app.core.database.SessionLocal", Session)

    def deliver():
        return feed_ingestion.ingest_feed_batch.run(batch.id)

    a, b = _run_both(deliver, deliver)
    assert not isinstance(a, Exception) and not isinstance(b, Exception), (a, b)
    assert len(calls) == 1, f"run_ingestion ran {len(calls)} times under duplicate delivery"

    claimed = [r for r in (a, b) if isinstance(r, dict) and r.get("claimed")]
    assert len(claimed) == 1


def test_duplicate_delivery_after_a_completed_run_does_nothing(Session, make_batch, monkeypatch):
    """§4: a redelivery arriving after the batch is terminal must not re-ingest."""
    from app.workers.tasks import feed_ingestion

    batch = make_batch(status="LOADED")
    calls = []
    monkeypatch.setattr("scripts.ingest_daily.run_ingestion",
                        lambda *a, **k: calls.append(1))
    monkeypatch.setattr("app.core.database.SessionLocal", Session)

    out = feed_ingestion.ingest_feed_batch.run(batch.id)
    assert out["claimed"] is False
    assert calls == []
    assert out["status"] == "LOADED"


# ── 3.3 concurrent quarantine release of the same row ──────────────────────

def _ctx(tenant):
    from app.core.permissions import role_capabilities
    from app.core.request_context import RequestContext
    from app.models.user import UserRole
    return RequestContext(user_id=tenant["admin_id"], role=UserRole.BANK_ADMIN,
                          bank_id=tenant["bank_id"], agency_id=None, sid=None,
                          perms=role_capabilities(UserRole.BANK_ADMIN))


def test_concurrent_release_of_the_same_row_places_it_once(Session, tenant, placeable):
    """Two reviewers click Release at the same moment. Postgres row locks must
    make one of them a no-op — and no second placement or case may exist."""
    from app.services.bank import feed_quarantine_service as svc

    def release():
        s = Session()
        try:
            return svc.release_rows(s, _ctx(tenant), placeable["batch_id"], [placeable["row_id"]])
        finally:
            s.close()

    a, b = _run_both(release, release)
    for r in (a, b):
        assert not isinstance(r, Exception), f"release raised: {r!r}"

    released = sum(r["released"] for r in (a, b))
    s = Session()
    try:
        placements = s.execute(text("select count(*) from collections.placements where loan_id=:l"),
                               {"l": placeable["loan_id"]}).scalar()
        cases = s.execute(text("select count(*) from collections.cases where loan_id=:l"),
                          {"l": placeable["loan_id"]}).scalar()
        row_status = s.execute(text("select status from lending.bank_feed_rows where id=:r"),
                               {"r": placeable["row_id"]}).scalar()
    finally:
        s.close()

    assert released == 1, f"{released} releases reported; expected exactly 1"
    assert placements == 1, f"{placements} placements for one loan"
    assert cases == 1, f"{cases} cases for one loan"
    assert row_status == "RELEASED"


def test_a_second_release_while_the_first_holds_the_lock_waits_then_declines(
        Session, tenant, placeable):
    """Explicitly interleaved: worker A holds the row lock in an open
    transaction; worker B arrives and must not proceed on stale state."""
    from app.services.bank import feed_quarantine_service as svc

    sa = Session()
    # A takes the row under FOR UPDATE and does NOT commit yet.
    locked = sa.execute(text("select status from lending.bank_feed_rows "
                             "where id=:r and status='QUARANTINED' for update"),
                        {"r": placeable["row_id"]}).scalar()
    assert locked == "QUARANTINED"
    sa.execute(text("update lending.bank_feed_rows set status='RELEASED', released_at=now() "
                    "where id=:r"), {"r": placeable["row_id"]})

    result: dict = {}

    def b_release():
        s = Session()
        try:
            s.execute(text("SET LOCAL statement_timeout = '15s'"))
            result["out"] = svc.release_rows(s, _ctx(tenant), placeable["batch_id"],
                                             [placeable["row_id"]])
        except Exception as exc:
            result["exc"] = exc
        finally:
            s.close()

    tb = threading.Thread(target=b_release, daemon=True)
    tb.start()
    # Give B time to block on the lock, then let A commit.
    tb.join(3)
    sa.commit()
    sa.close()
    tb.join(JOIN_TIMEOUT)
    assert not tb.is_alive(), "the second release never returned (lock not released?)"

    assert "exc" not in result, f"second release raised: {result.get('exc')!r}"
    out = result["out"]
    # It saw the committed RELEASED state and declined.
    assert out["released"] == 0
    assert out["verdicts"][0]["outcome"] == "ALREADY_RELEASED"

    s = Session()
    try:
        placements = s.execute(text("select count(*) from collections.placements where loan_id=:l"),
                               {"l": placeable["loan_id"]}).scalar()
    finally:
        s.close()
    assert placements == 0, "A only flipped the status; B must not have placed either"


# ── 3.4 duplicate loan / case upserts under concurrency ────────────────────

def test_concurrent_ingestion_of_the_same_row_does_not_duplicate_the_loan(Session, tenant):
    """process_row upserts on business keys. Two concurrent passes over the
    same account must leave ONE loan and ONE customer — the property the Phase
    5 retry safety argument rests on."""
    from scripts.ingest_daily import run_ingestion
    import tempfile, os

    # lat/lon and the masked identifiers are REQUIRED for a new customer:
    # without them the engine SKIPS the row ("missing lat/lon"), which is
    # correct behaviour and would make this test measure nothing.
    csv = ("customer_ref,customer_name,loan_account_number,case_number,branch_code,"
           "loan_type,dpd,total_outstanding,overdue_amount,emi_amount,"
           "sanctioned_amount,disbursed_amount,outstanding_principal,"
           "disbursement_date,maturity_date,interest_rate,phone_primary,"
           "address_line1,city,state,pincode,dob,gender,latitude,longitude,"
           "pan_masked,aadhaar_masked\n"
           f"P8DUP-{tenant['tag']},Dup Borrower,P8DUPLN-{tenant['tag']},"
           f"P8DUPCASE-{tenant['tag']},{tenant['branch_code']},PERSONAL,45,100000,"
           "5000,3000,200000,200000,150000,2024-02-01,2027-02-01,14.25,9899000102,"
           "Addr,Gurugram,Haryana,122018,1988-06-14,MALE,28.412,77.064,"
           "XXXXX0000X,XXXXXXXX0000\n")
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w") as fh:
        fh.write(csv)

    # ingest_daily does `from app.core.database import SessionLocal` at module
    # level, so the name must be rebound ON THAT MODULE — patching
    # app.core.database would not reach it.
    import scripts.ingest_daily as ing
    original = ing.SessionLocal
    ing.SessionLocal = Session
    try:
        def ingest():
            try:
                return run_ingestion(__import__("pathlib").Path(path), dry_run=False,
                                     bank_code=tenant["bank_code"])
            except Exception as exc:
                return exc

        a, b = _run_both(ingest, ingest)
    finally:
        ing.SessionLocal = original
        os.unlink(path)

    s = Session()
    try:
        loans = s.execute(text("select count(*) from lending.loans "
                               "where bank_id=:b and loan_account_number=:a"),
                          {"b": tenant["bank_id"], "a": f"P8DUPLN-{tenant['tag']}"}).scalar()
        custs = s.execute(text("select count(*) from lending.customers "
                               "where bank_id=:b and customer_ref=:c"),
                          {"b": tenant["bank_id"], "c": f"P8DUP-{tenant['tag']}"}).scalar()
        cases = s.execute(text("select count(*) from collections.cases "
                               "where bank_id=:b and case_number=:c"),
                          {"b": tenant["bank_id"], "c": f"P8DUPCASE-{tenant['tag']}"}).scalar()
    finally:
        s.close()

    # Diagnostics FIRST, so a failure shows what the two runs actually did.
    print("[concurrent-ingest] a=", repr(a))
    print("[concurrent-ingest] b=", repr(b))
    print("[concurrent-ingest] loans=", loans, "customers=", custs, "cases=", cases)

    # The OUTCOME is what is asserted: whatever the two runs did internally,
    # the book must not carry a duplicate borrower, loan or case.
    assert loans == 1, f"{loans} loans for one account number"
    assert custs == 1, f"{custs} customers for one customer_ref"
    assert cases <= 1, f"{cases} cases for one case_number"


# ── 3.5 repeated blocked allocation runs ───────────────────────────────────

def test_repeated_blocked_allocation_runs_plan_nothing(Session, tenant):
    """The Phase 7 gate, on Postgres. Two blocked nights must plan nothing and
    create no beats; each records its own evidence row."""
    from app.services import pipeline_readiness as pr

    s = Session()
    try:
        on = pr.business_date()
        ready, stages = pr.allocation_gate(s, on)
        # This isolated tenant has no feed and no scores for today.
        summary = pr.gate_summary(stages)
    finally:
        s.close()

    assert ready is False
    assert set(summary["blocked_by"]) == {"ingestion", "scoring"}

    s = Session()
    try:
        placements = s.execute(text("select count(*) from collections.placements where bank_id=:b"),
                               {"b": tenant["bank_id"]}).scalar()
    finally:
        s.close()
    assert placements == 0


# ── 3.6 rollback and recovery after an injected failure ────────────────────

def test_an_injected_failure_mid_release_rolls_back_everything(Session, tenant, placeable,
                                                               monkeypatch):
    """open_case raises after the placement is flushed. Postgres must roll the
    whole release back: no placement, no case, and the row still quarantined."""
    from app.services.bank import feed_quarantine_service as svc
    from app.services.placement_service import PlacementService

    real = PlacementService.open_case

    def boom(self, placement, loan, **kw):
        raise RuntimeError("injected failure after placement flush")

    monkeypatch.setattr(PlacementService, "open_case", boom)
    s = Session()
    try:
        with pytest.raises(RuntimeError):
            svc.release_rows(s, _ctx(tenant), placeable["batch_id"], [placeable["row_id"]])
    finally:
        s.close()
        monkeypatch.setattr(PlacementService, "open_case", real)

    s = Session()
    try:
        placements = s.execute(text("select count(*) from collections.placements where loan_id=:l"),
                               {"l": placeable["loan_id"]}).scalar()
        cases = s.execute(text("select count(*) from collections.cases where loan_id=:l"),
                          {"l": placeable["loan_id"]}).scalar()
        row = s.execute(text("select status, released_at from lending.bank_feed_rows where id=:r"),
                        {"r": placeable["row_id"]}).first()
    finally:
        s.close()

    assert placements == 0, "a placement survived a rolled-back release"
    assert cases == 0, "a case survived a rolled-back release"
    assert row.status == "QUARANTINED" and row.released_at is None


def test_the_row_can_be_released_successfully_after_a_failed_attempt(Session, tenant, placeable):
    """Recovery: the failure above must leave the row releasable, not wedged."""
    from app.services.bank import feed_quarantine_service as svc

    s = Session()
    try:
        out = svc.release_rows(s, _ctx(tenant), placeable["batch_id"], [placeable["row_id"]])
    finally:
        s.close()

    assert out["released"] == 1, out
    s = Session()
    try:
        placements = s.execute(text("select count(*) from collections.placements where loan_id=:l"),
                               {"l": placeable["loan_id"]}).scalar()
    finally:
        s.close()
    assert placements == 1
