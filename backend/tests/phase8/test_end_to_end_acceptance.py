"""§9 — the whole workflow, end to end, on real PostgreSQL through real HTTP.

THE PATH EXERCISED:
    dry-run validate -> upload (staging) -> asynchronous ingestion
    -> quarantine review -> release of an eligible row
    -> scoring -> allocation gate

Every stage is checked against PERSISTED state, not against the response alone,
because the whole point of the honesty work in Phases 4-7 is that a 200 does
not mean the loan book changed.

The feed deliberately contains one row that will be HELD (an unknown branch),
so the PARTIAL path and the unreleasable path are both exercised, and one
upstream failure is forced so allocation is seen to block.

All borrowers are synthetic; masked columns carry placeholder tokens.
"""
from __future__ import annotations

import io
import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

# NOTE (measured in Phase 8): the asynchronous worker calls run_ingestion with
# bank_code only — there is no file-level --agency for an UPLOADED feed. So an
# uploaded row must name its own `agency_code`, or placement is correctly
# refused NO_AGENCY and the row is held. That is an operational requirement on
# the bank's file, recorded in the Phase 8 report.
GOOD_HEADER = ("customer_ref,customer_name,loan_account_number,case_number,branch_code,"
               "agency_code,"
               "loan_type,dpd,total_outstanding,overdue_amount,emi_amount,"
               "sanctioned_amount,disbursed_amount,outstanding_principal,"
               "disbursement_date,maturity_date,interest_rate,phone_primary,"
               "address_line1,city,state,pincode,dob,gender,latitude,longitude,"
               "pan_masked,aadhaar_masked\n")


def _row(ref: str, branch: str, agency_code: str = "") -> str:
    return (f"{ref},Synthetic Borrower,LN{ref},CS{ref},{branch},{agency_code},"
            "PERSONAL,45,100000,5000,3000,200000,200000,150000,"
            "2024-02-01,2027-02-01,14.25,9000000001,Addr,Gurugram,Haryana,122018,"
            "1988-06-14,MALE,28.412,77.064,XXXXX0000X,XXXXXXXX0000\n")


@pytest.fixture()
def api(Session):
    from app.core.database import get_db
    from app.main import app

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    from app.core.ratelimit import limiter
    was = limiter.enabled
    limiter.enabled = False
    yield TestClient(app)
    limiter.enabled = was
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def covered(db, tenant):
    """Contract + region coverage, so a released row can actually be placed."""
    from app.models.tenancy import AgencyContract, AgencyRegion
    from tests._placement import region_tree

    regions = region_tree(db, bank_id=tenant["bank_id"])
    contract = AgencyContract(id=str(uuid.uuid4()), bank_id=tenant["bank_id"],
                              agency_id=tenant["agency_id"], contract_no=f"C-E2E/{tenant['tag']}",
                              status="ACTIVE", start_date=date(2026, 4, 1),
                              end_date=date(2027, 3, 31), max_placed_cases=None,
                              sla_first_visit_days=5)
    db.add(contract)
    db.flush()
    db.add(AgencyRegion(id=str(uuid.uuid4()), bank_id=tenant["bank_id"],
                        agency_id=tenant["agency_id"], contract_id=contract.id,
                        region_id=regions["NORTH"]))
    db.execute(text("update tenancy.branches set region_id=:r where bank_id=:b"),
               {"r": regions["GGN"], "b": tenant["bank_id"]})
    db.commit()
    return regions


def _hdr(user_id, role="BANK_ADMIN"):
    from app.core.security import create_access_token
    return {"Authorization": f"Bearer {create_access_token(user_id, role, 'dev-device-01')}"}


def _state(Session, sql, **params):
    s = Session()
    try:
        return s.execute(text(sql), params).first()
    finally:
        s.close()


def test_the_whole_workflow_end_to_end(api, Session, tenant, covered, monkeypatch, capsys):
    """One pass through every stage, asserting persisted state at each step."""
    tag = tenant["tag"][:6]
    good_ref = f"E2EOK{tag}"
    held_ref = f"E2EBAD{tag}"
    # Row 2 names a branch this bank does not have -> UNKNOWN_BRANCH (held
    # BEFORE any write, so loan_id stays NULL and it is NOT releasable).
    csv = (GOOD_HEADER
           + _row(good_ref, tenant["branch_code"], tenant["agency_code"])
           + _row(held_ref, "NOSUCHBR", tenant["agency_code"]))
    csv_bytes = csv.encode()

    staged: dict[str, bytes] = {}
    monkeypatch.setattr("app.core.storage.ensure_bucket", lambda *a, **k: None)
    monkeypatch.setattr("app.core.storage.upload_bytes",
                        lambda key, data, ctype=None, **k: staged.__setitem__(key, data))
    monkeypatch.setattr("app.core.storage.download_bytes", lambda key: staged[key])
    monkeypatch.setattr("app.core.database.SessionLocal", Session)
    import scripts.ingest_daily as ing
    monkeypatch.setattr(ing, "SessionLocal", Session)

    steps: list[str] = []

    # ── 1. dry-run validation: reports, writes nothing ──────────────────────
    before = _state(Session, "select count(*) as n from lending.bank_feed_batches "
                             "where bank_id=:b", b=tenant["bank_id"]).n
    v = api.post("/api/v1/bank/feed/validate", headers=_hdr(tenant["admin_id"]),
                 files={"file": ("e2e.csv", io.BytesIO(csv_bytes), "text/csv")})
    assert v.status_code == 200, v.text
    assert v.json()["ok"] is True and v.json()["rows"] == 2
    after = _state(Session, "select count(*) as n from lending.bank_feed_batches "
                            "where bank_id=:b", b=tenant["bank_id"]).n
    assert after == before, "a dry run must not create a batch"
    steps.append("validate: ok=True rows=2, no batch written")

    # ── 2. upload: staged and RECEIVED, never 'ingested' ───────────────────
    dispatched: list[str] = []
    from app.workers.tasks import feed_ingestion
    monkeypatch.setattr(feed_ingestion.ingest_feed_batch, "delay",
                        lambda *a, **k: dispatched.append(str(a[0])))

    up = api.post("/api/v1/bank/feed", headers=_hdr(tenant["admin_id"]),
                  files={"file": ("e2e.csv", io.BytesIO(csv_bytes), "text/csv")})
    assert up.status_code == 201, up.text
    body = up.json()
    batch_id = body["batch_id"]
    assert body["status"] == "RECEIVED", "upload must not claim ingestion"
    assert body["duplicate"] is False and body["queued"] is True
    assert body["rows_total"] == 2
    # Nothing in the loan book yet — the honesty claim, checked against the DB.
    loans_now = _state(Session, "select count(*) as n from lending.loans where bank_id=:b",
                       b=tenant["bank_id"]).n
    assert loans_now == 0, "RECEIVED must not mean rows reached the book"
    assert dispatched == [str(batch_id)]
    steps.append(f"upload: RECEIVED, queued=True, 0 loans in book, 1 task dispatched")

    # ── 3. duplicate upload: same batch, no second ingestion ───────────────
    dup = api.post("/api/v1/bank/feed", headers=_hdr(tenant["admin_id"]),
                   files={"file": ("e2e.csv", io.BytesIO(csv_bytes), "text/csv")})
    assert dup.status_code == 201
    assert dup.json()["duplicate"] is True
    assert dup.json()["batch_id"] == batch_id
    assert len(dispatched) == 1, "a duplicate upload must not enqueue again"
    steps.append("duplicate upload: same batch, no second dispatch")

    # ── 4. asynchronous ingestion (the real task, real engine) ─────────────
    out = feed_ingestion.ingest_feed_batch.run(batch_id)
    assert out["claimed"] is True, out
    batch = _state(Session, "select status, rows_total, rows_accepted, rows_quarantined "
                            "from lending.bank_feed_batches where id=:i", i=batch_id)
    # One good row, one unknown branch -> PARTIAL, accurately represented.
    assert batch.status == "PARTIAL", f"expected PARTIAL, got {batch.status}"
    sdiag = Session()
    try:
        held_rows = sdiag.execute(text(
            "select row_no, status, loan_account_number, dq_errors "
            "from lending.bank_feed_rows where batch_id=:i order by row_no"),
            {"i": batch_id}).fetchall()
    finally:
        sdiag.close()
    with capsys.disabled():
        for hr in held_rows:
            reason = (hr.dq_errors or [{}])[-1].get("reason")
            print(f"[E2E-diag] row {hr.row_no} {hr.status} {hr.loan_account_number} -> {reason}")
    assert batch.rows_quarantined == 1, [r.loan_account_number for r in held_rows]
    steps.append(f"ingest: status={batch.status} quarantined={batch.rows_quarantined}")

    # The good row reached the book; the held row did not.
    good = _state(Session, "select count(*) as n from lending.loans "
                           "where bank_id=:b and loan_account_number=:a",
                  b=tenant["bank_id"], a=f"LN{good_ref}").n
    held = _state(Session, "select count(*) as n from lending.loans "
                           "where bank_id=:b and loan_account_number=:a",
                  b=tenant["bank_id"], a=f"LN{held_ref}").n
    assert good == 1 and held == 0
    steps.append("book: good row ingested, unknown-branch row not created")

    # ── 5. duplicate task delivery after completion: no repeat ─────────────
    again = feed_ingestion.ingest_feed_batch.run(batch_id)
    assert again["claimed"] is False
    loans_after = _state(Session, "select count(*) as n from lending.loans where bank_id=:b",
                         b=tenant["bank_id"]).n
    assert loans_after == good, "redelivery must not duplicate the book"
    steps.append("redelivery: declined, book unchanged")

    # ── 6. quarantine review ───────────────────────────────────────────────
    q = api.get(f"/api/v1/bank/feed/{batch_id}/quarantine", headers=_hdr(tenant["admin_id"]))
    assert q.status_code == 200, q.text
    ql = q.json()
    assert ql["total_quarantined"] == 1
    item = ql["items"][0]
    # UNKNOWN_BRANCH: never releasable through this workflow.
    assert item["releasable"] is False
    assert item["blocked_because"] == "NO_LOAN_TO_PLACE"
    assert item["reason"] == "UNKNOWN_BRANCH"
    steps.append(f"review: 1 held, releasable=False reason={item['reason']}")

    # ── 7. releasing it is refused, and the row is untouched ───────────────
    rel = api.post(f"/api/v1/bank/feed/{batch_id}/quarantine/release",
                   headers=_hdr(tenant["admin_id"]), json={"row_ids": [item["row_id"]]})
    assert rel.status_code == 200, rel.text
    assert rel.json()["released"] == 0
    assert rel.json()["verdicts"][0]["outcome"] == "NOT_RELEASABLE"
    still = _state(Session, "select status from lending.bank_feed_rows where id=:r",
                   r=item["row_id"]).status
    assert still == "QUARANTINED"
    steps.append("release of UNKNOWN_BRANCH: NOT_RELEASABLE, row untouched")

    with capsys.disabled():
        print("\n[E2E] " + "\n[E2E] ".join(steps))


def test_a_releasable_row_completes_the_release_path(api, Session, tenant, covered,
                                                     placeable, capsys):
    """The eligible half of §9: a held row whose reason is now resolved is
    re-tried through the EXISTING placement path and ends as a real case."""
    r = api.post(f"/api/v1/bank/feed/{placeable['batch_id']}/quarantine/release",
                 headers=_hdr(tenant["admin_id"]), json={"row_ids": [placeable["row_id"]]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["released"] == 1, body

    v = body["verdicts"][0]
    placement = _state(Session, "select source, status from collections.placements where id=:p",
                       p=v["placement_id"])
    case = _state(Session, "select case_number, status from collections.cases where id=:c",
                  c=v["case_id"])
    row = _state(Session, "select status, released_by, released_at "
                          "from lending.bank_feed_rows where id=:r", r=placeable["row_id"])

    assert placement.source == "FEED" and placement.status == "ACTIVE"
    assert case is not None
    assert row.status == "RELEASED" and row.released_at is not None
    assert str(row.released_by) == str(tenant["admin_id"])

    with capsys.disabled():
        print(f"\n[E2E] release: placement(source={placement.source}) + "
              f"case({case.status}) created; row RELEASED by the acting user")


def test_an_upstream_failure_blocks_allocation_on_the_correct_business_date(
        Session, tenant, make_batch, capsys):
    """The §9 blocked-downstream requirement, measured on Postgres: a rejected
    feed must stop allocation for TONIGHT's business date."""
    from app.services import pipeline_readiness as pr

    on = pr.business_date()
    make_batch(status="REJECTED", on=on)

    s = Session()
    try:
        ready, stages = pr.allocation_gate(s, on)
        summary = pr.gate_summary(stages)
    finally:
        s.close()

    assert ready is False
    assert "ingestion" in summary["blocked_by"]
    with capsys.disabled():
        print(f"\n[E2E] gate on {on}: blocked_by={summary['blocked_by']}")


def test_allocation_opens_only_when_both_stages_are_present(Session, tenant, make_batch):
    """And the positive case: a LOADED feed plus a snapshot for the same
    business date opens the gate."""
    from app.models.customer import RiskCategory
    from app.models.repayment_snapshot import RepaymentSnapshot
    from app.services import pipeline_readiness as pr
    from tests._placement import make_loan

    on = pr.business_date()
    make_batch(status="LOADED", on=on, loaded_at=datetime.now(timezone.utc))

    s = Session()
    try:
        loan = make_loan(s, 1, bank_id=tenant["bank_id"], branch_code=tenant["branch_code"])
        s.add(RepaymentSnapshot(
            bank_id=tenant["bank_id"], loan_id=loan.id, customer_id=loan.customer_id,
            case_id=None, as_of_date=on, trigger="MANUAL", source="SCORECARD",
            model_version="scorecard-1.1.0", likelihood=60.0, risk_score=40.0,
            band="UNCERTAIN", risk_category=RiskCategory.MEDIUM, evidence_coverage=0.7,
            features={}, contributions={}))
        s.commit()
        ready, stages = pr.allocation_gate(s, on)
    finally:
        s.close()

    assert ready is True, pr.gate_summary(stages)
