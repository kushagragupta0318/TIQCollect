"""Attach outcomes to the synthetic snapshots using the real labeller.

This is a thin wrapper around RepaymentService.attach_outcomes(). It exists so
the orchestrator has something to call, and so the label distribution is printed
— NOT to reimplement labelling. `_infer_outcome`, the 30-day horizon, the
REPAYMENT_FULL_RATIO threshold and the censoring rule are all the production
ones, untouched.

REPAYMENT_OUTCOME_HORIZON_DAYS is deliberately NOT overridden. The synthetic
history is generated far enough in the past that the normal 30-day window is
already complete for every snapshot, which is the whole reason for simulating
backwards in time rather than shortening the horizon.

Refuses to run against anything but the synthetic database, because labelling
is a write and the demo book's snapshots are not ready for it.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_URL = os.environ.get("DATABASE_URL", "")
if "synth" not in _URL:
    raise SystemExit(
        "refusing to run: DATABASE_URL does not name a synthetic database.\n"
        f"  got: {_URL or '(unset)'}\n"
        "Run this through scripts/run_synthetic_experiment.py."
    )
os.environ.setdefault("SECRET_KEY", "synthetic-labeller")
os.environ.setdefault("COMMAND_CENTRE_API_KEY", "synthetic-labeller")

from sqlalchemy import func                                       # noqa: E402

from app.core.config import settings                              # noqa: E402
from app.core.database import SessionLocal                        # noqa: E402
from app.models.repayment_snapshot import RepaymentSnapshot       # noqa: E402
from app.services.repayment_service import RepaymentService       # noqa: E402


def main() -> None:
    db = SessionLocal()
    total = db.query(func.count(RepaymentSnapshot.id)).scalar()
    print(f"horizon (unchanged) : {settings.REPAYMENT_OUTCOME_HORIZON_DAYS} days")
    print(f"snapshots           : {total}")

    result = RepaymentService(db).attach_outcomes()
    db.commit()

    print(f"examined            : {result['examined']}")
    print(f"labelled            : {result['labelled']}")
    for outcome, n in sorted(result["by_outcome"].items(), key=lambda kv: -kv[1]):
        print(f"    {outcome:<14} {n:>7}  ({n / max(1, result['labelled']):.1%})")

    rng = db.query(func.min(RepaymentSnapshot.as_of_date),
                   func.max(RepaymentSnapshot.as_of_date)).one()
    print(f"snapshot date range : {rng[0]} .. {rng[1]}")
    unlabelled = db.query(func.count(RepaymentSnapshot.id)).filter(
        RepaymentSnapshot.outcome.is_(None)).scalar()
    if unlabelled:
        print(f"still unlabelled    : {unlabelled}  (inside the horizon, not yet mature)")
    db.close()


if __name__ == "__main__":
    main()
