"""Bank Data Quality (plan §5.4 / Tech Ops "Data Quality", task F10): the
bank-feed staging and quarantine tables (lending.bank_feed_batches,
lending.bank_feed_rows -- scripts/ingest_daily.py's own validation shape),
surfaced read-only, plus a short list of structural sanity checks over the
live book that the feed's own gates do not cover.

What this reuses, not restates:
  - Quarantine reasons are PlacementService.REFUSAL_REASONS (UNKNOWN_BRANCH,
    LOAN_NOT_OPEN, NO_AGENCY, ...) -- the exact set ingest_daily.py's one
    quarantine() call site writes. No new reason vocabulary.
  - Feed freshness anchors on kpi_catalog.latest_reading() (the book's own
    last real reading), never wall-clock now() -- the bug class fixed twice
    already today (agency_effect.latest_month, bank Alerts' fraud rule).

What is NOT tracked by the feed and is said so rather than invented:
  - "Missing required key fields" (customer_ref/loan_account_number/
    case_number blank) is SKIPPED by process_row() before a BankFeedRow is
    ever written -- there is nothing in the database to read for it. The
    skipped COUNT is real (BankFeedBatch.rows_skipped); the reason breakdown
    is not persisted anywhere, so this page does not claim one.

New, because nothing upstream defines them: two structural checks straight
off the live book. Measured before writing either, not assumed:
  - Customers sharing a phone_primary. lending.customers has no UNIQUE
    constraint on it (only a plain index, bank_id + phone_primary) -- real
    duplicate customer records are structurally possible. loans and cases
    were the first candidates for a "duplicate records" check and both
    turned out to be schema-enforced unique per bank (uq_loans_bank_id_
    loan_account_number, uq_cases_bank_id_case_number) -- checking either
    would be a test for something the database already refuses.
  - overdue_amount, or outstanding_principal, exceeding total_outstanding:
    logical impossibilities, not modelled judgements, and neither has a
    CHECK constraint (unlike loans.dpd >= 0, which does -- dpd cannot go
    negative here and so is not one of these checks either).
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.bank.kpi_catalog import latest_reading


def _rows(db: Session, sql: str, params: dict) -> list[dict]:
    # psycopg2 hands back a native uuid.UUID for a uuid column via this raw
    # text() path; the response model's ids are plain str (same recurring
    # fix as analytics_catalog._read_rows).
    return [{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in r.items()}
           for r in db.execute(text(sql), params).mappings().all()]


def _feed_freshness(db: Session, adb: Session, bank_id: str) -> list[dict]:
    as_of = latest_reading(adb, bank_id)
    rows = _rows(db, """
        SELECT feed_type, max(business_date) AS last_business_date, max(received_at) AS last_received_at,
               sum(rows_total) AS rows_total, sum(rows_accepted) AS rows_accepted,
               sum(rows_quarantined) AS rows_quarantined, sum(rows_skipped) AS rows_skipped
        FROM lending.bank_feed_batches WHERE bank_id = :bank GROUP BY feed_type ORDER BY feed_type""",
        {"bank": bank_id})
    for row in rows:
        # SUM() over this driver path can come back Decimal even for an
        # integer column (measured earlier today on the scorecard view) --
        # cast once, here, rather than let one reach the response model.
        for col in ("rows_total", "rows_accepted", "rows_quarantined", "rows_skipped"):
            row[col] = None if row[col] is None else int(row[col])
        last = row["last_business_date"]
        row["days_stale"] = (as_of - last).days if as_of and last else None
    return rows


def _quarantined_by_reason(db: Session, bank_id: str) -> list[dict]:
    # dq_errors is a JSON list of {"reason", "detail"}; a row holds every
    # reason it was ever quarantined for, not only the latest -- counted
    # once per (row, reason), same as a person scanning the queue would.
    return _rows(db, """
        SELECT e->>'reason' AS reason, count(*) AS rows
        FROM lending.bank_feed_rows r, jsonb_array_elements(r.dq_errors) e
        WHERE r.bank_id = :bank AND r.status = 'QUARANTINED'
        GROUP BY 1 ORDER BY 2 DESC""", {"bank": bank_id})


def _quarantined_sample(db: Session, bank_id: str, limit: int = 20) -> list[dict]:
    return _rows(db, """
        SELECT r.row_no, r.loan_account_number, r.customer_ref, r.case_number,
               b.feed_type, b.business_date, r.dq_errors, r.created_at
        FROM lending.bank_feed_rows r JOIN lending.bank_feed_batches b ON b.id = r.batch_id
        WHERE r.bank_id = :bank AND r.status = 'QUARANTINED'
        ORDER BY r.created_at DESC LIMIT :limit""", {"bank": bank_id, "limit": limit})


_OUT_OF_RANGE = "overdue_amount > total_outstanding OR outstanding_principal > total_outstanding"


def _duplicate_customer_phones(db: Session, bank_id: str, limit: int = 20) -> tuple[int, list[dict]]:
    groups = _rows(db, """
        SELECT phone_primary, count(*) AS customers
        FROM lending.customers WHERE bank_id = :bank
        GROUP BY phone_primary HAVING count(*) > 1
        ORDER BY count(*) DESC""", {"bank": bank_id})
    return len(groups), groups[:limit]


def _out_of_range(db: Session, bank_id: str, limit: int = 20) -> tuple[int, list[dict]]:
    total = db.execute(text(f"SELECT count(*) FROM lending.loans WHERE bank_id = :bank AND ({_OUT_OF_RANGE})"),
                       {"bank": bank_id}).scalar() or 0
    sample = _rows(db, f"""
        SELECT id AS loan_id, loan_account_number, overdue_amount, total_outstanding, outstanding_principal
        FROM lending.loans WHERE bank_id = :bank AND ({_OUT_OF_RANGE})
        ORDER BY overdue_amount DESC LIMIT :limit""", {"bank": bank_id, "limit": limit})
    return int(total), sample


def compute_data_quality(db: Session, adb: Session, bank_id: str) -> dict:
    dup_count, dup_sample = _duplicate_customer_phones(db, bank_id)
    oor_count, oor_sample = _out_of_range(db, bank_id)
    return {
        "feed_freshness": _feed_freshness(db, adb, bank_id),
        "quarantined_by_reason": _quarantined_by_reason(db, bank_id),
        "quarantined_sample": _quarantined_sample(db, bank_id),
        "duplicate_customer_phones": {"count": dup_count, "sample": dup_sample},
        "out_of_range_loans": {"count": oor_count, "sample": oor_sample},
    }
