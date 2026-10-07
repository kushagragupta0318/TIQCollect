"""Price every existing placement's Recovery vs Expected denominator (one-shot).

The generator inserts placements directly (migrate_v1_to_v2 / generate_demo_v2),
bypassing PlacementService.attach_expectation -- the one place that prices a
NEW placement from its loan's newest recovery_risk prediction as of the
placement date. Measured 2026-10-07 on the restored demo book: 9,764
placements bank-wide (Girivan), 0 with expected_recovery_prob/expected_recovery_inr
set, even though 38k+ ml.model_predictions rows exist -- the predictions were
never attached to the placement rows that are collections.placements'
column and the Recovery/Placements tabs both read.

This script closes that gap for EXISTING placements only, by calling
attach_expectation(placement, loan) directly -- no new arithmetic, no new
model call, the same point-in-time rule (as_of_date <= placed_on, ADR 0005
abstain-rather-than-impute) `attach_expectation` already enforces. A
placement whose loan never got a modelled prediction before it was placed
stays unpriced -- that is the honest answer, not something this script
papers over.

    python -m scripts.backfill_placement_recovery --dry-run      # count, change nothing
    python -m scripts.backfill_placement_recovery                # every bank
    python -m scripts.backfill_placement_recovery --bank GFL     # one bank, by code

Idempotent: only touches placements where model_prediction_id IS NULL, so a
second run prices nothing already priced and re-prices nothing.
"""
from __future__ import annotations

import argparse
import json

from app.core.database import SessionLocal
from app.models.loan import Loan
from app.models.placement import Placement
from app.models.tenancy import Bank
from app.services.placement_service import PlacementService


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bank", help="bank code (default: every bank)")
    ap.add_argument("--dry-run", action="store_true", help="count only; write nothing")
    args = ap.parse_args(argv)
    db = SessionLocal()
    try:
        q = db.query(Bank)
        if args.bank:
            q = q.filter(Bank.code == args.bank)
        banks = q.order_by(Bank.code).all()
        if not banks:
            print(json.dumps({"error": f"no bank {args.bank!r}"}))
            return 1
        svc = PlacementService(db)
        for bank in banks:
            placements = (db.query(Placement)
                         .filter(Placement.bank_id == bank.id, Placement.model_prediction_id.is_(None))
                         .all())
            priced = 0
            for placement in placements:
                loan = db.get(Loan, placement.loan_id)
                if loan is None:
                    continue
                before = placement.expected_recovery_prob
                svc.attach_expectation(placement, loan)
                if placement.expected_recovery_prob is not None and before is None:
                    priced += 1
            print(json.dumps({"bank": bank.code, "candidates": len(placements), "priced": priced,
                              "unpriced": len(placements) - priced}))
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
