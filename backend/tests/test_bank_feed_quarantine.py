"""Quarantine review and safe release: /bank/feed/{id}/quarantine (Phase 6).

Release is a RE-TRY through the real placement rules, so these tests build a
GENUINE refusal (a loan with no contract in force) rather than a row with a
hand-written status. What matters is that:

  * the rules still decide — a row whose reason is unfixed is refused again,
  * a row that never created a loan (UNKNOWN_BRANCH) is declined, not invented,
  * a release happens at most once, under concurrency,
  * one bank can neither see nor release another's rows.

Fixture shape follows tests/test_bank_feed_upload.py (two banks, one session
factory, rate limiter off) so the cross-tenant assertions are real.
"""
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.audit_log import AuditAction, AuditLog
from app.models.case import Case
from app.models.lending import BankFeedBatch, BankFeedRow
from app.models.placement import Placement
from app.models.tenancy import Agency, AgencyContract, Bank, Branch
from app.models.user import User, UserRole
from app.services.bank import feed_quarantine_service as svc
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id
from tests._placement import cover, make_loan

BANK2 = test_id("bank:girivan-quar")
AGENCY2 = test_id("agency:almora-quar")
DAY = date(2026, 10, 15)
# The seeded agency of TEST_BANK_ID (tests/_db.create_schema). A row names its
# agency by CODE, the way the feed does, so this must be the real one.
AGENCY_CODE = "AGENCY-TIQ-001"


def _user(db, key, role, *, bank=TEST_BANK_ID, phone):
    u = User(id=test_id(f"u:{key}"), email=f"{key}@example.in", phone=phone, full_name=key.title(),
             hashed_password=hash_password("Harbour-Lights-2026"), role=role, bank_id=bank,
             agency_id=None, is_active=True)
    db.add(u)
    return u


@pytest.fixture()
def world():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine, info={})      # no default tenant
    db = Session()
    db.add(Bank(id=BANK2, code="GFLQUAR", legal_name="Girivan Finance Ltd.",
                display_name="Girivan Finance", timezone="Asia/Kolkata", brand={},
                status="ACTIVE", is_demo=True))
    db.flush()
    db.add_all([
        Agency(id=AGENCY2, bank_id=BANK2, code="AGY-ALM", legal_name="Almora Recovery Desk LLP",
               status="ACTIVE", contacts=[], is_demo=True),
        Branch(id=test_id("branch:gflq"), bank_id=BANK2, branch_code="GFL001",
               name="Girivan Pune", is_active=True),
    ])
    w = {
        "db": db, "Session": Session,
        "admin": _user(db, "quaradmin", UserRole.BANK_ADMIN, phone="9833300001"),
        "techops": _user(db, "quartechops", UserRole.BANK_TECHOPS, phone="9833300002"),
        "analyst": _user(db, "quaranalyst", UserRole.BANK_ANALYST, phone="9833300003"),
        "bank2_admin": _user(db, "quarbank2", UserRole.BANK_ADMIN, bank=BANK2, phone="9833300004"),
    }
    db.commit()

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
    try:
        yield w
    finally:
        limiter.enabled = was
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _hdr(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value, 'dev-device-01')}"}


def _batch(db, *, bank_id=TEST_BANK_ID, status="PARTIAL", sha=None):
    b = BankFeedBatch(bank_id=bank_id, feed_type="DAILY_BOOK", business_date=DAY,
                      file_name="book.csv", file_sha256=(sha or "a" * 64),
                      received_via="UPLOAD", status=status, rows_total=3, rows_accepted=1,
                      rows_quarantined=2, received_at=datetime.now(timezone.utc))
    db.add(b)
    db.flush()
    return b


def _held_row(db, batch, *, row_no=1, loan=None, reason="NO_CONTRACT_IN_FORCE",
              agency_code=None, status="QUARANTINED"):
    """A row held the way the engine holds one: the reason in dq_errors, and
    `loan_id` set only when a loan was actually upserted first."""
    raw = {"loan_account_number": getattr(loan, "loan_account_number", None) or "LN00000099",
           "customer_ref": "C-00001", "case_number": f"CASE{row_no:04d}",
           "branch_code": "GGN044"}
    if agency_code:
        raw["agency_code"] = agency_code
    r = BankFeedRow(bank_id=batch.bank_id, batch_id=batch.id, row_no=row_no,
                    loan_account_number=raw["loan_account_number"],
                    customer_ref=raw["customer_ref"], case_number=raw["case_number"],
                    raw=raw, status=status,
                    dq_errors=[{"reason": reason, "detail": f"{reason}: held for review"}],
                    loan_id=getattr(loan, "id", None),
                    processed_at=datetime.now(timezone.utc))
    db.add(r)
    db.flush()
    return r


def _contract(db, *, agency_id=TEST_AGENCY_ID, bank_id=TEST_BANK_ID, cap=None):
    """A contract in force, so a re-try can actually succeed."""
    c = AgencyContract(bank_id=bank_id, agency_id=agency_id, contract_no=f"C/{agency_id[:6]}",
                       status="ACTIVE", start_date=date(2026, 4, 1), end_date=date(2027, 3, 31),
                       max_placed_cases=cap, sla_first_visit_days=5)
    db.add(c)
    db.flush()
    cover(db, c)
    db.flush()
    return c


def _ctx(user):
    """The authenticated context, for calling the service directly. Carries only
    what the service reads: never a bank id supplied by a client."""
    from app.core.permissions import role_capabilities
    from app.core.request_context import RequestContext
    return RequestContext(user_id=user.id, role=user.role, bank_id=user.bank_id,
                          agency_id=user.agency_id, sid=None,
                          perms=role_capabilities(user.role))


def _release(client, user, batch_id, row_ids, **body):
    return client.post(f"/api/v1/bank/feed/{batch_id}/quarantine/release",
                       headers=_hdr(user), json={"row_ids": row_ids, **body})


# ── authorisation ───────────────────────────────────────────────────────────

def test_an_authorised_bank_user_can_review_quarantined_rows(world):
    db = world["db"]
    b = _batch(db)
    _held_row(db, b)
    db.commit()
    c = TestClient(app)
    r = c.get(f"/api/v1/bank/feed/{b.id}/quarantine", headers=_hdr(world["admin"]))
    assert r.status_code == 200, r.text
    assert r.json()["total_quarantined"] == 1


def test_tech_ops_may_also_review_and_release(world):
    db = world["db"]
    b = _batch(db)
    _held_row(db, b)
    db.commit()
    c = TestClient(app)
    assert c.get(f"/api/v1/bank/feed/{b.id}/quarantine",
                 headers=_hdr(world["techops"])).status_code == 200


def test_a_role_without_the_capability_cannot_review(world):
    db = world["db"]
    b = _batch(db)
    _held_row(db, b)
    db.commit()
    c = TestClient(app)
    assert c.get(f"/api/v1/bank/feed/{b.id}/quarantine",
                 headers=_hdr(world["analyst"])).status_code == 403


def test_a_role_without_the_capability_cannot_release(world):
    db = world["db"]
    b = _batch(db)
    row = _held_row(db, b)
    db.commit()
    c = TestClient(app)
    assert _release(c, world["analyst"], b.id, [row.id]).status_code == 403
    assert _batch_row(db, row.id).status == "QUARANTINED"       # untouched


def test_an_anonymous_request_is_refused(world):
    db = world["db"]
    b = _batch(db)
    db.commit()
    c = TestClient(app)
    r = c.get(f"/api/v1/bank/feed/{b.id}/quarantine")
    assert r.status_code in (401, 403)


def _batch_row(db, row_id):
    db.expire_all()
    return db.query(BankFeedRow).filter(BankFeedRow.id == row_id).first()


# ── cross-bank isolation ────────────────────────────────────────────────────

def test_another_banks_batch_is_the_uniform_404(world):
    db = world["db"]
    theirs = _batch(db, bank_id=BANK2, sha="b" * 64)
    _held_row(db, theirs)
    db.commit()
    c = TestClient(app)
    foreign = c.get(f"/api/v1/bank/feed/{theirs.id}/quarantine", headers=_hdr(world["admin"]))
    missing = c.get(f"/api/v1/bank/feed/{test_id('batch:nope')}/quarantine",
                    headers=_hdr(world["admin"]))
    assert foreign.status_code == 404
    # Indistinguishable from a nonexistent id, so existence cannot be probed.
    assert foreign.status_code == missing.status_code and foreign.json() == missing.json()


def test_another_banks_row_is_the_uniform_404(world):
    db = world["db"]
    theirs = _batch(db, bank_id=BANK2, sha="b" * 64)
    row = _held_row(db, theirs)
    db.commit()
    c = TestClient(app)
    foreign = c.get(f"/api/v1/bank/feed/quarantine/rows/{row.id}", headers=_hdr(world["admin"]))
    missing = c.get(f"/api/v1/bank/feed/quarantine/rows/{test_id('row:nope')}",
                    headers=_hdr(world["admin"]))
    assert foreign.status_code == 404
    assert foreign.json() == missing.json()


def test_one_bank_cannot_release_anothers_row(world):
    db = world["db"]
    _contract(db)
    loan = make_loan(db, 1)
    mine = _batch(db)
    row = _held_row(db, mine, loan=loan)
    db.commit()
    c = TestClient(app)
    # Bank 2 names OUR batch and OUR row: the batch lookup alone is a 404.
    r = _release(c, world["bank2_admin"], mine.id, [row.id])
    assert r.status_code == 404
    assert _batch_row(db, row.id).status == "QUARANTINED"


def test_a_row_from_a_different_batch_of_the_same_bank_is_not_released(world):
    """The row is ours, but it does not belong to the batch named in the path."""
    db = world["db"]
    _contract(db)
    loan = make_loan(db, 1)
    b1 = _batch(db, sha="c" * 64)
    b2 = _batch(db, sha="d" * 64)
    row = _held_row(db, b2, loan=loan)
    db.commit()
    c = TestClient(app)
    r = _release(c, world["admin"], b1.id, [row.id])
    assert r.status_code == 200
    assert r.json()["verdicts"][0]["outcome"] == "NOT_FOUND"
    assert _batch_row(db, row.id).status == "QUARANTINED"


# ── listing and reasons ─────────────────────────────────────────────────────

def test_the_recorded_reason_and_attempt_count_are_reported(world):
    db = world["db"]
    b = _batch(db)
    row = _held_row(db, b, reason="CONTRACT_FULL")
    row.dq_errors = [*row.dq_errors, {"reason": "CONTRACT_FULL", "detail": "second attempt"}]
    db.commit()
    c = TestClient(app)
    item = c.get(f"/api/v1/bank/feed/{b.id}/quarantine", headers=_hdr(world["admin"])).json()["items"][0]
    assert item["reason"] == "CONTRACT_FULL"
    assert item["attempts"] == 2                 # the whole history, not just the latest


def test_borrower_pii_is_not_sent_unless_asked_for(world):
    db = world["db"]
    b = _batch(db)
    _held_row(db, b)
    db.commit()
    c = TestClient(app)
    plain = c.get(f"/api/v1/bank/feed/{b.id}/quarantine", headers=_hdr(world["admin"])).json()
    assert "raw" not in plain["items"][0]
    withraw = c.get(f"/api/v1/bank/feed/{b.id}/quarantine?include_raw=true",
                    headers=_hdr(world["admin"])).json()
    assert withraw["items"][0]["raw"]["loan_account_number"]


def test_counts_cover_the_whole_batch_not_the_page(world):
    """A reviewer paging 1 row at a time must still be told there are 3."""
    db = world["db"]
    _contract(db)
    b = _batch(db)
    for n in (1, 2, 3):
        _held_row(db, b, row_no=n, loan=make_loan(db, n))
    db.commit()
    c = TestClient(app)
    page = c.get(f"/api/v1/bank/feed/{b.id}/quarantine?limit=1",
                 headers=_hdr(world["admin"])).json()
    assert len(page["items"]) == 1
    assert page["total_quarantined"] == 3
    assert page["total_releasable"] == 3


def test_an_empty_quarantine_is_an_empty_list_not_an_error(world):
    db = world["db"]
    b = _batch(db, status="LOADED")
    db.commit()
    c = TestClient(app)
    out = c.get(f"/api/v1/bank/feed/{b.id}/quarantine", headers=_hdr(world["admin"])).json()
    assert out["total_quarantined"] == 0 and out["items"] == []


# ── invalid state ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["RECEIVED", "VALIDATING", "REJECTED"])
def test_a_batch_not_yet_processed_cannot_have_rows_released(world, status):
    db = world["db"]
    _contract(db)
    loan = make_loan(db, 1)
    b = _batch(db, status=status)
    row = _held_row(db, b, loan=loan)
    db.commit()
    c = TestClient(app)
    r = _release(c, world["admin"], b.id, [row.id])
    assert r.status_code == 409
    assert _batch_row(db, row.id).status == "QUARANTINED"
    assert db.query(Placement).count() == 0


def test_a_rejected_batch_is_never_turned_into_a_successful_one(world):
    db = world["db"]
    b = _batch(db, status="REJECTED")
    row = _held_row(db, b)
    db.commit()
    c = TestClient(app)
    _release(c, world["admin"], b.id, [row.id])
    db.expire_all()
    assert db.query(BankFeedBatch).filter(BankFeedBatch.id == b.id).first().status == "REJECTED"


def test_releasing_no_rows_is_refused(world):
    db = world["db"]
    b = _batch(db)
    db.commit()
    c = TestClient(app)
    assert _release(c, world["admin"], b.id, []).status_code == 422


def test_a_malformed_row_id_is_the_uniform_404(world):
    db = world["db"]
    b = _batch(db)
    db.commit()
    c = TestClient(app)
    assert _release(c, world["admin"], b.id, ["not-a-uuid"]).status_code == 404


# ── ineligible rows: no loan to place ───────────────────────────────────────

def test_a_row_that_never_created_a_loan_is_declined_not_invented(world):
    """UNKNOWN_BRANCH. The engine held it before any write, so there is no loan
    to place. Release must say so rather than fabricate a borrower."""
    db = world["db"]
    b = _batch(db)
    row = _held_row(db, b, reason="UNKNOWN_BRANCH", loan=None)
    db.commit()
    c = TestClient(app)
    out = _release(c, world["admin"], b.id, [row.id]).json()

    v = out["verdicts"][0]
    assert v["outcome"] == "NOT_RELEASABLE"
    assert v["blocked_because"] == svc.NO_LOAN
    assert out["released"] == 0
    assert _batch_row(db, row.id).status == "QUARANTINED"      # left exactly as it was
    assert db.query(Placement).count() == 0


def test_the_listing_marks_which_rows_cannot_be_released(world):
    db = world["db"]
    _contract(db)
    b = _batch(db)
    _held_row(db, b, row_no=1, loan=make_loan(db, 1))
    _held_row(db, b, row_no=2, loan=None, reason="UNKNOWN_BRANCH")
    db.commit()
    c = TestClient(app)
    out = c.get(f"/api/v1/bank/feed/{b.id}/quarantine", headers=_hdr(world["admin"])).json()
    assert out["total_quarantined"] == 2
    assert out["total_releasable"] == 1
    assert out["total_blocked"] == 1
    by_no = {i["row_no"]: i for i in out["items"]}
    assert by_no[1]["releasable"] is True and by_no[1]["blocked_because"] is None
    assert by_no[2]["releasable"] is False and by_no[2]["blocked_because"] == svc.NO_LOAN


# ── successful release, through the existing pipeline ───────────────────────

def test_a_release_places_the_loan_and_opens_a_case_through_the_one_service(world):
    db = world["db"]
    _contract(db)
    loan = make_loan(db, 1)
    b = _batch(db)
    row = _held_row(db, b, loan=loan, agency_code=AGENCY_CODE)
    db.commit()

    c = TestClient(app)
    out = _release(c, world["admin"], b.id, [row.id], on="2026-10-15").json()

    v = out["verdicts"][0]
    assert v["outcome"] == "RELEASED", out
    assert out["released"] == 1
    # The placement and case are real rows, made by placement_service.
    placement = db.query(Placement).filter(Placement.id == v["placement_id"]).first()
    assert placement is not None and placement.source == "FEED"
    assert placement.loan_id == loan.id
    case = db.query(Case).filter(Case.id == v["case_id"]).first()
    assert case is not None and case.placement_id == placement.id


def test_the_row_records_who_released_it_and_when(world):
    db = world["db"]
    _contract(db)
    loan = make_loan(db, 1)
    b = _batch(db)
    row = _held_row(db, b, loan=loan, agency_code=AGENCY_CODE)
    db.commit()

    c = TestClient(app)
    _release(c, world["admin"], b.id, [row.id])

    r = _batch_row(db, row.id)
    assert r.status == "RELEASED"
    assert r.released_by == world["admin"].id
    assert r.released_at is not None


def test_a_release_is_audited_with_the_existing_action(world):
    """PLACEMENT_CREATED, the action the engine and manual placement already
    write — no new audit system and no new enum value."""
    db = world["db"]
    _contract(db)
    loan = make_loan(db, 1)
    b = _batch(db)
    row = _held_row(db, b, loan=loan, agency_code=AGENCY_CODE)
    db.commit()

    c = TestClient(app)
    out = _release(c, world["admin"], b.id, [row.id]).json()

    logs = db.query(AuditLog).filter(AuditLog.action == AuditAction.PLACEMENT_CREATED).all()
    assert len(logs) == 1
    log = logs[0]
    assert log.user_id == world["admin"].id
    assert log.details["source"] == "FEED_RELEASE"
    assert log.details["feed_row_id"] == row.id
    assert log.details["batch_id"] == b.id
    assert log.entity_id == out["verdicts"][0]["placement_id"]


def test_the_batchs_quarantine_count_follows_reality_but_its_status_does_not_change(world):
    db = world["db"]
    _contract(db)
    b = _batch(db)
    r1 = _held_row(db, b, row_no=1, loan=make_loan(db, 1), agency_code=AGENCY_CODE)
    _held_row(db, b, row_no=2, loan=None, reason="UNKNOWN_BRANCH")
    db.commit()

    c = TestClient(app)
    out = _release(c, world["admin"], b.id, [r1.id]).json()

    assert out["remaining_quarantined"] == 1
    db.expire_all()
    fresh = db.query(BankFeedBatch).filter(BankFeedBatch.id == b.id).first()
    assert fresh.rows_quarantined == 1
    # PARTIAL is a fact about that file; a release does not rewrite history.
    assert fresh.status == "PARTIAL"


# ── the reason is still not fixed ───────────────────────────────────────────

def test_a_row_whose_reason_is_unfixed_is_refused_again_and_stays_held(world):
    """No contract in force, so the rules refuse. Validation is not bypassed to
    make the release succeed."""
    db = world["db"]
    loan = make_loan(db, 1)                       # deliberately NO contract
    b = _batch(db)
    row = _held_row(db, b, loan=loan, agency_code=AGENCY_CODE)
    db.commit()

    c = TestClient(app)
    out = _release(c, world["admin"], b.id, [row.id]).json()

    v = out["verdicts"][0]
    assert v["outcome"] == "STILL_REFUSED"
    assert out["released"] == 0
    r = _batch_row(db, row.id)
    assert r.status == "QUARANTINED"
    assert r.released_at is None and r.released_by is None
    # The new refusal is appended, so the reviewer sees it was re-tried.
    assert len(r.dq_errors) == 2
    assert db.query(Placement).count() == 0


def test_a_row_naming_no_agency_is_refused_rather_than_placed_with_a_guess(world):
    db = world["db"]
    _contract(db)
    loan = make_loan(db, 1)
    b = _batch(db)
    row = _held_row(db, b, loan=loan, agency_code=None)      # no agency on the row
    db.commit()

    c = TestClient(app)
    out = _release(c, world["admin"], b.id, [row.id]).json()

    assert out["verdicts"][0]["outcome"] == "STILL_REFUSED"
    assert out["verdicts"][0]["reason"] == "NO_AGENCY"
    assert db.query(Placement).count() == 0


# ── partial release: mixed rows in one call ─────────────────────────────────

def test_a_mixed_selection_reports_a_verdict_per_row(world):
    """One releasable, one with no loan, one whose reason is unfixed. The call
    succeeds and each row says what happened to it."""
    db = world["db"]
    _contract(db)
    ok_loan = make_loan(db, 1)
    b = _batch(db)
    good = _held_row(db, b, row_no=1, loan=ok_loan, agency_code=AGENCY_CODE)
    noloan = _held_row(db, b, row_no=2, loan=None, reason="UNKNOWN_BRANCH")
    # A loan of the OTHER bank's book is not placeable under our contract.
    other = _held_row(db, b, row_no=3, loan=make_loan(db, 9, bank_id=BANK2, branch_code="GFL001"),
                      agency_code=AGENCY_CODE)
    db.commit()

    c = TestClient(app)
    out = _release(c, world["admin"], b.id, [good.id, noloan.id, other.id]).json()

    by_id = {v["row_id"]: v for v in out["verdicts"]}
    assert by_id[good.id]["outcome"] == "RELEASED"
    assert by_id[noloan.id]["outcome"] == "NOT_RELEASABLE"
    # The row points at another bank's loan, so it is not releasable here.
    assert by_id[other.id]["outcome"] == "NOT_RELEASABLE"
    assert out["released"] == 1
    assert _batch_row(db, good.id).status == "RELEASED"
    assert _batch_row(db, noloan.id).status == "QUARANTINED"


# ── idempotency and concurrency ─────────────────────────────────────────────

def test_releasing_the_same_row_twice_does_not_place_it_twice(world):
    db = world["db"]
    _contract(db)
    loan = make_loan(db, 1)
    b = _batch(db)
    row = _held_row(db, b, loan=loan, agency_code=AGENCY_CODE)
    db.commit()

    c = TestClient(app)
    first = _release(c, world["admin"], b.id, [row.id]).json()
    second = _release(c, world["admin"], b.id, [row.id]).json()

    assert first["verdicts"][0]["outcome"] == "RELEASED"
    assert second["verdicts"][0]["outcome"] == "ALREADY_RELEASED"
    assert second["released"] == 0
    assert db.query(Placement).count() == 1            # placed once
    assert db.query(Case).count() == 1


def test_the_same_row_named_twice_in_one_call_is_released_once(world):
    db = world["db"]
    _contract(db)
    loan = make_loan(db, 1)
    b = _batch(db)
    row = _held_row(db, b, loan=loan, agency_code=AGENCY_CODE)
    db.commit()

    c = TestClient(app)
    out = _release(c, world["admin"], b.id, [row.id, row.id, row.id]).json()

    assert out["released"] == 1
    assert len(out["verdicts"]) == 1                   # de-duplicated
    assert db.query(Placement).count() == 1


def test_an_already_processed_row_is_not_ingested_again(world):
    """A row the engine ACCEPTED is not quarantined, so release declines it."""
    db = world["db"]
    _contract(db)
    loan = make_loan(db, 1)
    b = _batch(db)
    row = _held_row(db, b, loan=loan, status="ACCEPTED", agency_code=AGENCY_CODE)
    db.commit()

    c = TestClient(app)
    out = _release(c, world["admin"], b.id, [row.id]).json()

    assert out["verdicts"][0]["outcome"] == "ALREADY_RELEASED"
    assert out["released"] == 0
    assert db.query(Placement).count() == 0
    assert _batch_row(db, row.id).status == "ACCEPTED"   # unchanged


def test_a_concurrent_release_of_the_same_row_places_it_once(world):
    """The second request arrives while the first is mid-release. The claim is
    the status check under the row lock, so only one can win."""
    db = world["db"]
    _contract(db)
    loan = make_loan(db, 1)
    b = _batch(db)
    row = _held_row(db, b, loan=loan, agency_code=AGENCY_CODE)
    db.commit()

    c = TestClient(app)
    second: dict = {}

    # Re-enter the release from inside open_case: the first call holds the row
    # and has not committed yet.
    from app.services.placement_service import PlacementService
    real_open_case = PlacementService.open_case

    def reentrant(self, placement, loan_, **kw):
        case = real_open_case(self, placement, loan_, **kw)
        if not second:
            second["out"] = _release(c, world["admin"], b.id, [row.id]).json()
        return case

    PlacementService.open_case = reentrant
    try:
        first = _release(c, world["admin"], b.id, [row.id]).json()
    finally:
        PlacementService.open_case = real_open_case

    assert first["verdicts"][0]["outcome"] == "RELEASED"
    # The intruder did not place it a second time.
    assert second["out"]["released"] == 0
    assert db.query(Placement).count() == 1
    assert db.query(Case).count() == 1


# ── failure during release ──────────────────────────────────────────────────

def test_a_failure_mid_release_leaves_nothing_half_done(world):
    """One transaction for the call: if open_case raises on the second row, the
    first row's release is rolled back too, rather than leaving a released row
    with no case."""
    db = world["db"]
    _contract(db)
    b = _batch(db)
    r1 = _held_row(db, b, row_no=1, loan=make_loan(db, 1), agency_code=AGENCY_CODE)
    r2 = _held_row(db, b, row_no=2, loan=make_loan(db, 2), agency_code=AGENCY_CODE)
    db.commit()

    from app.services.placement_service import PlacementService
    real = PlacementService.open_case
    calls = {"n": 0}

    def flaky(self, placement, loan_, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("database went away")
        return real(self, placement, loan_, **kw)

    # Called at the service level: TestClient re-raises an unhandled error
    # rather than turning it into a 500, and what matters here is the ROLLBACK,
    # not the status code (the no-stack-trace test covers the response body).
    PlacementService.open_case = flaky
    try:
        with pytest.raises(RuntimeError):
            svc.release_rows(db, _ctx(world["admin"]), b.id, [r1.id, r2.id])
    finally:
        PlacementService.open_case = real

    db.expire_all()
    assert _batch_row(db, r1.id).status == "QUARANTINED"
    assert _batch_row(db, r2.id).status == "QUARANTINED"
    assert db.query(Placement).count() == 0
    assert db.query(Case).count() == 0


def test_the_error_body_carries_no_stack_trace(world):
    db = world["db"]
    b = _batch(db, status="RECEIVED")
    row = _held_row(db, b)
    db.commit()
    c = TestClient(app)
    r = _release(c, world["admin"], b.id, [row.id])
    assert r.status_code == 409
    assert "Traceback" not in r.text and 'File "' not in r.text


# ── the engine's own statuses are not widened ──────────────────────────────

def test_release_uses_only_statuses_the_database_allows(world):
    from app.models.lending import FEED_ROW_STATUSES
    import inspect
    src = inspect.getsource(svc)
    assert "RELEASED" in FEED_ROW_STATUSES and "QUARANTINED" in FEED_ROW_STATUSES
    for invented in ("RELEASING", "REVIEWED", "FIXED"):
        assert f'"{invented}"' not in src


# ── skipped rows: the reason must survive the run ───────────────────────────
# Measured on a 500-row feed: the batch reported PARTIAL with rows_skipped=500,
# rows_quarantined=0 and NO row anywhere saying why — the reason went to
# log.debug, which is off in production. record_skipped writes it as a SKIPPED
# row: visible to the operator, and NOT releasable (that is quarantine's job).

def test_a_skipped_row_records_why_it_was_skipped(world):
    from app.services.placement_service import PlacementService
    db = world["db"]
    batch = _batch(db, sha="c" * 64)
    raw = {"loan_account_number": "LN-SKIP-1", "customer_ref": "C-SKIP-1",
           "case_number": "CASE-SKIP-1", "branch_code": "GGN044"}

    PlacementService(db).record_skipped(
        batch, row_no=7, raw=raw, detail="missing lat/lon for new customer C-SKIP-1")
    db.commit()

    row = (db.query(BankFeedRow)
           .filter(BankFeedRow.batch_id == batch.id, BankFeedRow.row_no == 7).first())
    assert row is not None, "a skipped row left no record of itself"
    assert row.status == "SKIPPED"
    assert "missing lat/lon" in row.dq_errors[0]["detail"]
    assert row.loan_account_number == "LN-SKIP-1"


def test_a_skipped_row_is_not_quarantined_and_so_cannot_be_released(world):
    """SKIPPED is weaker than QUARANTINED on purpose: release re-tries rows a
    person has fixed, and an engine-level skip is not that."""
    from app.services.placement_service import PlacementService
    db = world["db"]
    batch = _batch(db, sha="d" * 64)
    PlacementService(db).record_skipped(batch, row_no=1, raw={"loan_account_number": "LN-S"},
                                        detail="no coordinates")
    db.commit()

    held = (db.query(BankFeedRow)
            .filter(BankFeedRow.batch_id == batch.id, BankFeedRow.status == "QUARANTINED").all())
    assert held == [], "a skipped row was exposed as releasable quarantine"


def test_replaying_a_skipped_row_does_not_duplicate_it(world):
    """A redelivered batch re-walks every row; the record must converge."""
    from app.services.placement_service import PlacementService
    db = world["db"]
    batch = _batch(db, sha="e" * 64)
    svc_p = PlacementService(db)
    for _ in range(3):
        svc_p.record_skipped(batch, row_no=2, raw={"loan_account_number": "LN-R"},
                             detail="no coordinates")
    db.commit()

    rows = (db.query(BankFeedRow)
            .filter(BankFeedRow.batch_id == batch.id, BankFeedRow.row_no == 2).all())
    assert len(rows) == 1
    assert len(rows[0].dq_errors) == 1, "the reason was appended once per replay"


def test_a_quarantined_row_is_not_downgraded_to_skipped(world):
    """Quarantine is the stronger state: a later skip must not erase it."""
    from app.services.placement_service import PlacementService
    db = world["db"]
    batch = _batch(db, sha="f" * 64)
    svc_p = PlacementService(db)
    svc_p.quarantine(batch, row_no=3, raw={"loan_account_number": "LN-Q"},
                     reason="UNKNOWN_BRANCH", detail="branch not this bank's")
    svc_p.record_skipped(batch, row_no=3, raw={"loan_account_number": "LN-Q"},
                         detail="no coordinates")
    db.commit()

    row = (db.query(BankFeedRow)
           .filter(BankFeedRow.batch_id == batch.id, BankFeedRow.row_no == 3).first())
    assert row.status == "QUARANTINED"
