# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-27 — New file. The only database-aware part of the visit-priority
#   score; ml/visit_priority.py itself is pure and knows nothing about SQL.
#
#   WHY A SEPARATE LAYER. The score needs three facts the Case row does not
#   carry: the loan's latest recovery rate, the loan's live balance, and whether
#   a promise is about to fall due. Fetching those per case would be three
#   queries per row — 1,600 round trips on a 545-case book. This does it in at
#   most three bounded queries for the whole set, whatever the case count, and
#   hands the pure scorer plain values.
#
#   QUERY BUDGET: AT MOST THREE, regardless of how many cases are passed in —
#   latest recovery rate, promises due soon, and the loans themselves when the
#   caller has not already loaded them. All three are IN-bounded.
#   tests/test_visit_priority_service.py counts them with a SQLAlchemy event
#   listener, because "it is bulk" is the sort of claim that quietly stops being
#   true the first time somebody adds a convenience lookup inside the loop.
# ─────────────────────────────────────────────────────────────────────────────
"""Score a set of cases for visit priority. Two bulk reads, then pure scoring."""
from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Iterable

from sqlalchemy import and_, func

from app.ml.visit_priority import PTP_PROTECTION_DAYS, score as _score_one
from app.models.case import RESOLVED_STATUSES
from app.models.loan import Loan
from app.models.ptp import PTP, PTPStatus
from app.models.repayment_snapshot import RepaymentSnapshot

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.case import Case

# Resolved cases get NO score. "Visit priority 64/100" on a case marked PAID is
# the product contradicting itself on one screen — there is no next visit to
# rank. On the live book 198 of 743 cases (26.6%) are resolved, so this is the
# common path, not an edge case.
#
# Imported, not restated: models/case.py owns the one definition, shared with
# case_service, which withholds the repayment score on the same grounds.
# Re-exported under the private name because manager.py imports it from here.
_RESOLVED_STATUSES = RESOLVED_STATUSES


def _latest_rate_by_loan(db: "Session", loan_ids: list[str]) -> dict[str, tuple]:
    """(rate_90, as_of_date) for each loan's most recent scored snapshot.

    The shape is lifted deliberately from manager.py's _latest_recovery_by_loan:
    a group-by-max(as_of_date) subquery self-joined back on
    (loan_id, as_of_date). That pair is the table's unique constraint
    (uq_repayment_snapshot_grain), so the join is index-served and returns one
    row per loan.

    NOT the shape used by repayment_service._existing(), which selects every
    snapshot ever written for those loans and dedupes in Python. Correct, but
    O(all history) — wrong for a read path that runs on every page load.
    """
    if not loan_ids:
        return {}
    newest = (
        db.query(RepaymentSnapshot.loan_id,
                 func.max(RepaymentSnapshot.as_of_date).label("as_of_date"))
        .filter(RepaymentSnapshot.loan_id.in_(loan_ids),
                RepaymentSnapshot.recovery_rate_90.is_not(None))
        .group_by(RepaymentSnapshot.loan_id)
        .subquery()
    )
    rows = (
        db.query(RepaymentSnapshot.loan_id,
                 RepaymentSnapshot.recovery_rate_90,
                 RepaymentSnapshot.as_of_date)
        .join(newest, and_(RepaymentSnapshot.loan_id == newest.c.loan_id,
                           RepaymentSnapshot.as_of_date == newest.c.as_of_date))
        .all()
    )
    return {r[0]: (r[1], r[2]) for r in rows}


def _soonest_ptp_days(db: "Session", case_ids: list[str],
                      today: date) -> dict[str, float]:
    """Days until each case's soonest ACTIVE promise, for promises due soon.

    One query. Necessary as a query at all because Case.ptps is lazy="noload"
    (models/case.py:93) — walking case.ptps returns empty rather than loading,
    so there is no cheaper path.
    """
    if not case_ids:
        return {}
    horizon = today + timedelta(days=PTP_PROTECTION_DAYS)
    rows = (
        db.query(PTP.case_id, func.min(PTP.committed_date))
        .filter(PTP.case_id.in_(case_ids),
                PTP.status == PTPStatus.ACTIVE,
                PTP.committed_date >= today,
                PTP.committed_date <= horizon)
        .group_by(PTP.case_id)
        .all()
    )
    return {r[0]: float((r[1] - today).days) for r in rows if r[1] is not None}


def score_cases(db: "Session", cases: Iterable["Case"], *,
                today: date | None = None,
                loans: dict[str, Loan] | None = None) -> dict[str, dict]:
    """{case_id: score dict} for every case handed in.

    `loans` lets a caller that has ALREADY bulk-loaded them (the allocator's
    _load_case_context does) pass them in rather than have them fetched twice.

    WHEN IT IS NOT PASSED, THE LOANS ARE FETCHED IN ONE QUERY HERE — deliberately,
    rather than read off `case.loan`. Relying on the caller to have joinedload-ed
    is an invisible contract: a caller that forgets turns this into 1 + N queries
    and nothing complains. Measured: reading case.loan for 2 cases cost 4 queries
    instead of 2. The budget is now the service's own guarantee, not the caller's
    responsibility.

    A missing loan degrades that case's value component to its floor rather than
    raising, because a case list must still render.

    RESOLVED CASES ARE OMITTED from the result entirely (PAID / CLOSED /
    WRITTEN_OFF) — there is no next visit to rank, so callers see no score and
    render nothing rather than a number that contradicts the status beside it.
    """
    cases = list(cases)
    if not cases:
        return {}
    today = today or date.today()

    loan_ids = [c.loan_id for c in cases if c.loan_id]
    rates = _latest_rate_by_loan(db, loan_ids)
    ptp_days = _soonest_ptp_days(db, [c.id for c in cases], today)

    by_loan = dict(loans or {})
    missing = [lid for lid in loan_ids if lid not in by_loan]
    if missing:
        by_loan.update({
            l.id: l for l in db.query(Loan).filter(Loan.id.in_(missing)).all()
        })

    out: dict[str, dict] = {}
    for case in cases:
        if case.status in _RESOLVED_STATUSES:
            continue
        loan = by_loan.get(case.loan_id) if case.loan_id else None
        rate_90, as_of = rates.get(case.loan_id, (None, None))
        result = _score_one({
            "recovery_rate_90": rate_90,
            "total_outstanding": getattr(loan, "total_outstanding", 0.0) or 0.0,
            "dpd": getattr(loan, "dpd", None),
            "visit_count": case.visit_count,
            "max_visits_allowed": case.max_visits_allowed,
            "ptp_due_in_days": ptp_days.get(case.id),
        })
        # Stamped so a screen can say how fresh the recovery input was. The score
        # itself is computed now; the rate behind it is as old as the last
        # scoring run, and conflating the two would overstate its currency.
        result["rate_as_of"] = as_of.isoformat() if as_of else None
        out[case.id] = result
    return out


def sort_key(scored: dict[str, dict]):
    """Highest score first, case_number as the deterministic tie-break.

    Returned as a key factory rather than applied here so both the allocator and
    the agent case list sort through ONE definition — two call sites that sorted
    "descending by score" independently would drift the moment one of them added
    a secondary term.
    """
    def key(case):
        entry = scored.get(case.id) or {}
        return (-float(entry.get("score") or 0.0), case.case_number or "")
    return key
