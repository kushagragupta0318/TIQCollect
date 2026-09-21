# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-21 — New file. `cases.priority` was stamped once at creation and
#   never again, so it aged out of step with the loan's DPD (248 of ~1,270
#   open cases wrong on the live book). This re-derives it for every OPEN
#   case from `models/case.priority_for(loan.dpd)`; closed cases keep their
#   last value as history. Called nightly from the 19:45 repayment-scoring
#   task, after the 19:30 ingest has moved DPD, and by the one-off backfill.
# ───────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.case import Case, CaseStatus, RESOLVED_STATUSES, priority_for
from app.models.loan import Loan


def restamp(db: Session, *, dry_run: bool = False) -> dict:
    """Set priority = priority_for(loan.dpd) on every open case whose stored
    value differs. Returns counts and the (case_id, before, after) list."""
    rows = (
        db.query(Case, Loan.dpd)
        .join(Loan, Loan.id == Case.loan_id)
        .filter(Case.status.notin_(list(RESOLVED_STATUSES)))
        .all()
    )
    changes: list[tuple[str, str, str]] = []
    for case, dpd in rows:
        want = priority_for(dpd)
        have = case.priority
        if have != want:
            changes.append((case.id, have.value if hasattr(have, "value") else str(have), want.value))
            if not dry_run:
                case.priority = want
    if not dry_run and changes:
        db.commit()
    return {"open_cases": len(rows), "changed": len(changes), "dry_run": dry_run, "changes": changes}
