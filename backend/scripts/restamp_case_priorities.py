"""One-off: re-derive `cases.priority` from each loan's current DPD, using the
same `services/case_priority_service.restamp` the 19:45 task runs nightly.
2026-09-21. Dry run by default; --apply writes and prints the undo recipe.

    python -m scripts.restamp_case_priorities            # report only
    python -m scripts.restamp_case_priorities --apply
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal                         # noqa: E402
from app.services.case_priority_service import restamp             # noqa: E402

ROLLBACK_DIR = pathlib.Path(__file__).resolve().parents[1] / "docs" / "rollback"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db = SessionLocal()
    try:
        out = restamp(db, dry_run=not args.apply)
        moves = Counter((b, a) for _, b, a in out["changes"])
        print(f"{'APPLIED' if args.apply else 'DRY RUN'}: open cases {out['open_cases']}, changed {out['changed']}")
        for (b, a), n in sorted(moves.items(), key=lambda x: -x[1]):
            print(f"  {b:>9} -> {a:<9} {n}")
        ROLLBACK_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = ROLLBACK_DIR / f"2026-09-21-case-priority-{'applied' if args.apply else 'dryrun'}-{stamp}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["case_id", "priority_before", "priority_after"]); w.writerows(out["changes"])
        print(f"before/after -> {path}")
        if args.apply:
            print("undo: UPDATE cases SET priority = priority_before FROM the CSV, per case_id (see docs/rollback/.../MANIFEST.txt)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
