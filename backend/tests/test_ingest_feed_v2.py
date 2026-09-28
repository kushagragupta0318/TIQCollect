"""scripts/ingest_daily.py in v2 — one bank's file, placements, quarantine.

2026-09-24 (coordinator audit items 2 and 12). Driven through the real
`process_row` with a real `FeedContext`, on a session with NO default tenant.
"""
from __future__ import annotations

import hashlib
from datetime import date

import pytest

from app.models.case import Case, ClosureReason
from app.models.customer import Customer
from app.models.lending import BankFeedBatch, BankFeedRow
from app.models.loan import Loan
from app.models.placement import Placement
from app.models.tenancy import AgencyContract
from scripts.ingest_daily import _close_case_recall, feed_context, process_row
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory

TODAY = date(2026, 9, 24)


@pytest.fixture()
def db():
    engine = make_engine()
    create_schema(engine)
    s = make_session_factory(engine, info={})()
    yield s
    s.close()


def _ctx(db, *, contract=True):
    if contract:
        db.add(AgencyContract(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, contract_no="MTB/FCA/2026-27/014",
                              start_date=date(2026, 4, 1), end_date=date(2027, 3, 31), status="ACTIVE"))
    batch = BankFeedBatch(bank_id=TEST_BANK_ID, feed_type="DAILY_BOOK", business_date=TODAY,
                          file_sha256=hashlib.sha256(b"feed").hexdigest(), received_via="UPLOAD")
    db.add(batch)
    db.commit()
    return feed_context(db, bank_code="MTB", agency_code="AGENCY-TIQ-001", batch=batch)


def _row(n=1, **over):
    row = {
        "customer_ref": f"MTB-C-{n:04d}", "customer_name": "Sunita Rawat", "dob": "1979-11-02",
        "gender": "FEMALE", "phone_primary": "9899000211", "address_line1": "B-17, Palam Vihar",
        "city": "Gurugram", "state": "Haryana", "pincode": "122017", "latitude": "28.508",
        "longitude": "77.034", "cibil_score": "641",
        "loan_account_number": f"MTB{n:07d}", "loan_type": "PERSONAL", "branch_code": "GGN044",
        "sanctioned_amount": "350000", "total_outstanding": "212000", "overdue_amount": "21800",
        "emi_amount": "10900", "disbursement_date": "2024-03-05", "maturity_date": "2027-03-05",
        "last_payment_date": "2026-06-05", "next_due_date": "2026-10-05",
        "dpd": "58", "bank_action": "ACTIVE", "case_number": f"MTB-CASE-{n:04d}",
        # Ignored in v2 — the bank is the FILE's. Present to prove it is.
        "bank_name": "Some Other Bank",
    }
    row.update(over)
    return row


def test_a_new_account_becomes_a_case_on_a_placement(db):
    ctx = _ctx(db)
    res = process_row(_row(), db, False, TODAY, ctx=ctx, row_no=1)
    db.commit()
    assert res["action_case"] == "inserted" and res["quarantined"] is None
    loan = db.query(Loan).one()
    assert loan.bank_id == TEST_BANK_ID and loan.bank_name == "Meridian Trust Bank"
    assert (loan.disbursement_date, loan.next_due_date, loan.dpd_as_of) == (date(2024, 3, 5), date(2026, 10, 5), TODAY)
    assert db.query(Customer).one().date_of_birth == date(1979, 11, 2)
    case = db.query(Case).one()
    placement = db.query(Placement).one()
    assert (case.agency_id, case.placement_id, placement.source) == (TEST_AGENCY_ID, placement.id, "FEED")


def test_an_unknown_branch_is_quarantined_before_anything_is_written(db):
    ctx = _ctx(db)
    res = process_row(_row(branch_code="MUM001"), db, False, TODAY, ctx=ctx, row_no=7)
    db.commit()
    assert res["quarantined"] == "UNKNOWN_BRANCH"
    assert db.query(Customer).count() == 0 and db.query(Loan).count() == 0 and db.query(Case).count() == 0
    held = db.query(BankFeedRow).one()
    assert (held.row_no, held.status, held.loan_account_number) == (7, "QUARANTINED", "MTB0000001")
    assert held.dq_errors[0]["reason"] == "UNKNOWN_BRANCH"


def test_without_a_contract_the_loan_lands_and_the_case_is_quarantined(db):
    ctx = _ctx(db, contract=False)
    res = process_row(_row(), db, False, TODAY, ctx=ctx, row_no=3)
    db.commit()
    assert res["action_case"] == "quarantined" and res["quarantined"] == "NO_CONTRACT_IN_FORCE"
    assert db.query(Loan).count() == 1                  # the bank's fact is recorded
    assert db.query(Case).count() == 0                  # nobody's case is not opened
    held = db.query(BankFeedRow).one()
    assert held.loan_id == db.query(Loan).one().id
    assert ctx.quarantined == 1


def test_an_update_row_needs_no_context_and_touches_only_the_existing_account(db):
    ctx = _ctx(db)
    process_row(_row(), db, False, TODAY, ctx=ctx, row_no=1)
    db.commit()
    res = process_row(_row(dpd="89", overdue_amount="32700", next_due_date="2026-11-05"), db, False, TODAY)
    db.commit()
    assert res["action_loan"].startswith("updated")
    loan = db.query(Loan).one()
    assert (loan.dpd, loan.next_due_date) == (89, date(2026, 11, 5))


def test_a_brand_new_account_without_context_is_skipped_not_guessed(db):
    res = process_row(_row(), db, False, TODAY)
    assert res["skipped_reason"] and db.query(Loan).count() == 0


def test_a_recall_closure_records_the_typed_reason(db):
    ctx = _ctx(db)
    process_row(_row(), db, False, TODAY, ctx=ctx, row_no=1)
    db.commit()
    case = db.query(Case).one()
    assert _close_case_recall(case, "LEGAL_PROCEEDINGS", "court order") == "auto_closed_recall"
    assert case.closure_reason == ClosureReason.RECALLED.value
    # The free-text marker ml/pipeline/outcomes.py matches is still written.
    assert case.resolution_notes.startswith("RECALLED by bank")
