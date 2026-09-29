"""End ACTIVE placements whose loan the bank has already closed (one-shot).

Before 2026-09-29 a feed PAID_DIRECT / SETTLED / WRITTEN_OFF / DECEASED /
RECALL closed the case but left its placement ACTIVE, so a book built before
then (the v2 demo fixture: 215 such placements) shows loans still "placed"
with an agency after the bank closed them. Run once after upgrading to
v2_0016 or later (docs/DEPLOY-style step; see PlacementService.reconcile_orphans):

    python -m scripts.reconcile_placements --dry-run      # count, change nothing
    python -m scripts.reconcile_placements                # every bank
    python -m scripts.reconcile_placements --bank GFL     # one bank, by code

Idempotent: a second run ends nothing. Each ended placement gets end_reason
FEED_RECONCILE and a PLACEMENT_ENDED audit row with the system as actor.
"""
from __future__ import annotations

import argparse
import json

from app.core.database import SessionLocal
from app.models.tenancy import Bank
from app.services.placement_service import PlacementService
from app.services.scope import access_day


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
        day = access_day()
        for bank in banks:
            out = svc.reconcile_orphans(bank.id, on=day, dry_run=args.dry_run)
            print(json.dumps({"bank": bank.code, **out}))
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
