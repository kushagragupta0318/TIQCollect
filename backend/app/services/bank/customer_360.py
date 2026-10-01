"""One borrower, as the bank sees them (C08).

`analytics.v_case_360` is a CASE view: one row per case, so a borrower with two
loans has two rows, possibly at two agencies. This page is per CUSTOMER and
**sums nothing across cases** — each case keeps its own target, collection, DPD
and model score, with its own provenance. A borrower-level recovery figure or
risk score would be a statistic nobody has defined, so none is produced here
(tiqcollect-f8, 2026-10-01).

Scoping: the view is `security_invoker`, so it is read through the tenant-bound
analytics session and RLS applies to the caller. A region-limited caller sees
only the loans inside their region, and the payload says so rather than letting
a partial borrower read as a whole one. Missing, another tenant's, and outside
the region are the same 404.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import AppException, ErrorCode
from app.models.customer import Customer
from app.models.loan import Loan
from app.services.placement_read_service import apply_region_limit
from app.services.placement_read_service import REGION_LIMIT_UNRESOLVED

#: A borrower with more loans than this is not a borrower, it is a data fault;
#: the page would be unreadable either way. Bounded because nothing unbounded
#: runs inside a request.
MAX_LOANS = 50
#: One case's history. Older entries exist; the page says when it has truncated.
MAX_TIMELINE = 200

_CASE_COLUMNS = """
    case_id, case_number, status, agency_id, agency_name, placed_on,
    agent_id, agent_name, loan_id, loan_type, dpd, dpd_bucket,
    total_outstanding, overdue_amount, target_amount, collected_verified,
    last_visit_at, last_visit_outcome, last_call_at, last_call_outcome, last_contact_at,
    active_ptp_date, active_ptp_amount, has_open_dispute, is_escalated,
    latest_probability, latest_band, latest_model_version, latest_disposition
"""


def _bank_id(ctx) -> str:
    if getattr(ctx, "scope", None) != "BANK" or not getattr(ctx, "bank_id", None):
        raise AppException(403, ErrorCode.FORBIDDEN, "A bank user is required")
    return ctx.bank_id


def _not_found() -> AppException:
    # The same body for missing, another tenant's, and outside the region: the
    # existence of a borrower is itself the thing not to disclose.
    return AppException(404, ErrorCode.NOT_FOUND, "Not found")


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (datetime, date)) else value


def _visible_loans(db: Session, bank_id: str, customer_id: str, region_limit) -> list[Loan]:
    """This customer's loans the caller may see: their bank, and inside their
    region limit where one is set."""
    q = db.query(Loan).filter(Loan.bank_id == bank_id, Loan.customer_id == customer_id)
    return apply_region_limit(q, region_limit).order_by(Loan.loan_account_number).limit(MAX_LOANS).all()


def customer_for_loan(db: Session, ctx, loan_id: str, *, region_limit=None) -> str:
    """The borrower a loan belongs to, for the entry point from Placements (which
    lists loans, not customers). Same 404 as everywhere else, so this cannot be
    used to probe which loan ids exist."""
    bank_id = _bank_id(ctx)
    q = db.query(Loan.customer_id).filter(Loan.id == loan_id, Loan.bank_id == bank_id)
    row = apply_region_limit(q, region_limit).first()
    if row is None:
        raise _not_found()
    return row[0]


def customer_360(db: Session, adb: Session, ctx, customer_id: str, *, region_limit=None) -> dict:
    bank_id = _bank_id(ctx)
    customer = (db.query(Customer)
                .filter(Customer.id == customer_id, Customer.bank_id == bank_id).first())
    if customer is None:
        raise _not_found()
    loans = _visible_loans(db, bank_id, customer_id, region_limit)
    if not loans:
        # The borrower exists in this bank but no loan of theirs is inside the
        # caller's region: indistinguishable from not existing, deliberately.
        raise _not_found()

    loan_ids = [loan.id for loan in loans]
    rows = adb.execute(
        text(f"SELECT {_CASE_COLUMNS} FROM analytics.v_case_360 "
             # CAST both sides: psycopg2 sends a Python list as text[], and
             # loan_id/customer_id are uuid — without the casts Postgres refuses
             # the comparison outright ("operator does not exist: uuid = text").
             "WHERE customer_id = CAST(:cid AS uuid) AND loan_id = ANY(CAST(:loan_ids AS uuid[])) "
             "ORDER BY placed_on DESC NULLS LAST, case_number"),
        {"cid": customer_id, "loan_ids": loan_ids},
    ).mappings().all()

    cases = [{k: _iso(v) for k, v in row.items()} for row in rows]
    with_cases = {str(c["loan_id"]) for c in cases}
    return {
        "customer": {
            # Masked at the source (lending.customers) and never unmasked here.
            "customer_id": customer.id,
            "full_name": customer.full_name,
            "phone_primary": customer.phone_primary,
            "address_line1": customer.address_line1,
            "city": customer.city,
            "state": customer.state,
            "pincode": customer.pincode,
            "pan_masked": customer.pan_masked,
            "aadhaar_masked": customer.aadhaar_masked,
            "language_preference": customer.language_preference,
            "is_hostile": bool(customer.is_hostile),
            "do_not_contact": bool(customer.do_not_contact),
            "tags": list(customer.tags or []),
        },
        "cases": cases,
        # A loan that has never been placed has no case, so the case view has no
        # row for it. It is shown as itself rather than silently missing.
        "loans_without_cases": [
            {"loan_id": loan.id, "loan_account_number": loan.loan_account_number,
             "loan_type": getattr(loan.loan_type, "value", loan.loan_type),
             "dpd": int(loan.dpd or 0),
             "total_outstanding": float(loan.total_outstanding or 0.0),
             "overdue_amount": float(loan.overdue_amount or 0.0)}
            for loan in loans if str(loan.id) not in with_cases
        ],
        # True when a region limit narrowed what is shown: a partial borrower
        # must not read as a whole one.
        "region_limited": region_limit is not None and region_limit is not REGION_LIMIT_UNRESOLVED,
        "loans_truncated": len(loans) == MAX_LOANS,
    }


def _case_in_scope(db: Session, bank_id: str, case_id: str, region_limit) -> Any:
    """The case, if the caller may see it; otherwise the uniform 404. Scoped by
    bank and by the region limit through the case's own loan."""
    from app.models.case import Case
    case = db.query(Case).filter(Case.id == case_id, Case.bank_id == bank_id).first()
    if case is None:
        raise _not_found()
    q = db.query(Loan.id).filter(Loan.id == case.loan_id, Loan.bank_id == bank_id)
    if apply_region_limit(q, region_limit).first() is None:
        raise _not_found()
    return case


def case_timeline(db: Session, ctx, case_id: str, *, region_limit=None) -> dict:
    """One case's history, newest first: visits, calls, payments and promises in
    one column. Bounded at MAX_TIMELINE and the payload says when it truncated.

    C09 (audit trails) adds its own entries to this column later; every entry
    already carries {kind, at, actor} so they interleave without a reshape.
    """
    from app.models.call_log import CallLog
    from app.models.payment import Payment
    from app.models.ptp import PTP
    from app.models.visit import Visit

    bank_id = _bank_id(ctx)
    case = _case_in_scope(db, bank_id, case_id, region_limit)
    cap = MAX_TIMELINE + 1                     # one extra: did anything fall off?
    entries: list[dict] = []

    for v in (db.query(Visit).filter(Visit.case_id == case.id)
              .order_by(Visit.check_in_time.desc()).limit(cap).all()):
        entries.append({
            "kind": "VISIT", "at": _iso(v.check_in_time), "actor_type": "AGENT", "actor_id": v.agent_id,
            "entity_id": v.id,
            "detail": {"outcome": getattr(v.outcome, "value", v.outcome),
                       "customer_met": bool(v.customer_met),
                       "person_met": getattr(v.person_met, "value", v.person_met),
                       "borrower_disposition": getattr(v.borrower_disposition, "value", v.borrower_disposition),
                       "notes": v.notes,
                       "geo_verified": bool(v.geo_verified),
                       "distance_metres": v.distance_from_customer_metres,
                       # Evidence is NOT linked here: a link is a view, and a view
                       # is audited. The page asks for it explicitly.
                       "has_evidence": bool(v.agent_photo_key or v.borrower_photo_key or v.object_photo_key
                                            or v.signature_key or v.agent_recording_key or v.borrower_recording_key)},
        })
    for c in (db.query(CallLog).filter(CallLog.case_id == case.id)
              .order_by(CallLog.called_at.desc()).limit(cap).all()):
        entries.append({
            "kind": "CALL", "at": _iso(c.called_at), "actor_type": "AGENT", "actor_id": c.agent_id,
            "entity_id": c.id,
            "detail": {"outcome": getattr(c.outcome, "value", c.outcome),
                       "duration_seconds": c.duration_seconds,
                       "borrower_disposition": getattr(c.borrower_disposition, "value", c.borrower_disposition),
                       "notes": c.customer_response_notes},
        })
    for p in (db.query(Payment).filter(Payment.case_id == case.id)
              .order_by(Payment.payment_date.desc()).limit(cap).all()):
        entries.append({
            "kind": "PAYMENT", "at": _iso(p.payment_date), "actor_type": "AGENT", "actor_id": p.agent_id,
            "entity_id": p.id,
            "detail": {"amount": float(p.amount or 0.0),
                       "mode": getattr(p.mode, "value", p.mode),
                       "status": getattr(p.status, "value", p.status),
                       "receipt_number": p.receipt_number,
                       "verified_at": _iso(p.verified_at)},
        })
    for t in (db.query(PTP).filter(PTP.case_id == case.id)
              .order_by(PTP.created_at.desc()).limit(cap).all()):
        entries.append({
            "kind": "PTP", "at": _iso(t.created_at), "actor_type": "AGENT", "actor_id": t.agent_id,
            "entity_id": t.id,
            "detail": {"committed_amount": float(t.committed_amount or 0.0),
                       "committed_date": _iso(t.committed_date),
                       "status": getattr(t.status, "value", t.status),
                       "actual_paid_amount": float(t.actual_paid_amount or 0.0)},
        })

    entries.sort(key=lambda e: (e["at"] or ""), reverse=True)
    return {
        "case_id": case.id,
        "entries": entries[:MAX_TIMELINE],
        "truncated": len(entries) > MAX_TIMELINE,
        "limit": MAX_TIMELINE,
    }
