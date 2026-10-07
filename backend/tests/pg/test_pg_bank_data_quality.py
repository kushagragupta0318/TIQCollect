"""Bank Data Quality (plan §5.4, task F10): lending.bank_feed_batches /
bank_feed_rows (scripts/ingest_daily.py's own staging and quarantine) plus
two structural sanity checks over the live book, read-only and
tenant-scoped.

The committed demo book never runs through ingest_daily.py (it is built by
generate_demo_v2.py, the ledger simulator) and so carries zero feed
batches -- measured, not assumed. The mechanism is proven here on a
hand-built fixture instead; the real book's own test only pins the honest
empty shape, not a count that does not exist in it.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.pg.test_pg_analytics_b import B1, B2, L1, _insert, _refresh, book  # noqa: F401 (fixture)
from tests.pg.test_pg_demo_fixture import db, sql_text  # noqa: F401 — the restored fixture

pytestmark = pytest.mark.filterwarnings("ignore")


def _session(engine, bank_id: str) -> Session:
    s = Session(bind=engine)
    s.execute(text("SELECT set_config('app.bank_id', :b, false), set_config('app.scope', 'BANK', false)"),
              {"b": bank_id})
    return s


def test_data_quality_is_the_honest_empty_shape_on_the_real_demo_book(db):  # noqa: F811
    """The committed book never ran through ingest_daily.py, so every feed
    and quarantine figure is genuinely empty — this pins that it is reported
    as empty, not fabricated, and that the route does not crash on a bank
    with no feed history at all."""
    from app.demo.roster import BANK
    from app.services.bank.data_quality import compute_data_quality
    with _session(db, BANK["id"]) as s:
        out = compute_data_quality(s, s, BANK["id"])
    assert out["feed_freshness"] == []
    assert out["quarantined_by_reason"] == []
    assert out["quarantined_sample"] == []
    assert out["duplicate_customer_phones"] == {"count": 0, "sample": []}
    assert out["out_of_range_loans"] == {"count": 0, "sample": []}


@pytest.fixture(scope="module")
def dq_book(book):  # noqa: F811
    """A feed batch with one quarantined row, two customers sharing a phone
    number, and an out-of-range loan, on top of test_pg_analytics_b's book.

    loan_account_number and case_number are each UNIQUE per bank already
    (uq_loans_bank_id_loan_account_number, uq_cases_bank_id_case_number) --
    measured by trying a duplicate loan_account_number first and watching
    Postgres refuse it. phone_primary carries no such constraint, which is
    exactly why it is the one this fixture (and data_quality.py) uses."""
    batch_id = str(uuid.uuid4())
    with book.begin() as conn:
        # loans(bank_id, branch_code) has a real FK to branches; "NONE" is
        # not a real branch — the base fixture's own loans (L1-L3) only get
        # away with it because test_pg_analytics_b.book() disables FK/trigger
        # checking for its own insert block, which does not carry over here.
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        _insert(conn, "lending.bank_feed_batches", id=batch_id, bank_id=B1, feed_type="DAILY_BOOK",
                business_date=date(2025, 2, 15), file_sha256="a" * 64, received_via="UPLOAD",
                status="PARTIAL", rows_total=3, rows_accepted=1, rows_quarantined=1, rows_skipped=1,
                received_at=datetime(2025, 2, 15, 9, 0, tzinfo=timezone.utc))
        _insert(conn, "lending.bank_feed_rows", bank_id=B1, batch_id=batch_id, row_no=1,
                loan_account_number="UNMAPPED-001", customer_ref="C-UNMAPPED", case_number="CASE-UNMAPPED",
                raw={"branch_code": "ZZZZZ"}, status="QUARANTINED",
                dq_errors=[{"reason": "UNKNOWN_BRANCH", "detail": "branch ZZZZZ is not a branch of this bank"}])
        # Two customers, same phone — a real duplicate-record signal (no
        # UNIQUE constraint on phone_primary).
        for _ in range(2):
            _insert(conn, "lending.customers", bank_id=B1, phone_primary="9999900000")
        # Out of range: overdue exceeds the whole outstanding balance.
        l1_customer = conn.execute(text("SELECT customer_id FROM lending.loans WHERE id = :id"),
                                   {"id": L1}).scalar()
        _insert(conn, "lending.loans", id=str(uuid.uuid4()), bank_id=B1, customer_id=l1_customer,
               loan_account_number="OOR-001", loan_type="PERSONAL", branch_code="NONE", npa_since=None,
               overdue_amount=50_000, total_outstanding=40_000)
    _refresh(book)
    return book


def test_quarantined_rows_and_structural_checks_fire_on_the_fixture(dq_book):
    from app.services.bank.data_quality import compute_data_quality
    with _session(dq_book, B1) as s:
        out = compute_data_quality(s, s, B1)
    assert out["feed_freshness"] and out["feed_freshness"][0]["feed_type"] == "DAILY_BOOK"
    assert out["feed_freshness"][0]["rows_quarantined"] == 1
    reasons = {r["reason"]: r["rows"] for r in out["quarantined_by_reason"]}
    assert reasons.get("UNKNOWN_BRANCH") == 1
    sample = out["quarantined_sample"]
    assert sample and sample[0]["loan_account_number"] == "UNMAPPED-001"
    assert out["duplicate_customer_phones"]["count"] == 1
    dup = out["duplicate_customer_phones"]["sample"][0]
    assert dup["phone_primary"] == "9999900000" and dup["customers"] == 2
    assert out["out_of_range_loans"]["count"] == 1
    oor = out["out_of_range_loans"]["sample"][0]
    assert oor["loan_account_number"] == "OOR-001" and oor["overdue_amount"] > oor["total_outstanding"]


def test_another_bank_sees_none_of_it(dq_book):
    from app.services.bank.data_quality import compute_data_quality
    with _session(dq_book, B2) as s:
        out = compute_data_quality(s, s, B2)
    assert out["feed_freshness"] == []
    assert out["quarantined_by_reason"] == []
    assert out["duplicate_customer_phones"] == {"count": 0, "sample": []}
    assert out["out_of_range_loans"] == {"count": 0, "sample": []}
