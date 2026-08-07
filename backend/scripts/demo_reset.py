"""Rewind the showcase demo case so the same story can be told again.

    python -m scripts.demo_reset --save      # capture the clean state, once
    python -m scripts.demo_reset             # rewind to it
    python -m scripts.demo_reset --dry-run   # show what a rewind would change

With DEMO_REHEARSAL_MODE=true the agent's check-in does the rewind on its own,
so this is only needed to take the first snapshot or to rewind by hand.

The logic lives in app/services/demo_service.py — the same code the check-in
path calls, so the two can never drift apart.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import argparse

from app.core.database import SessionLocal
from app.core.config import settings
from app.services import demo_service


def main() -> None:
    p = argparse.ArgumentParser(description="Rewind the showcase demo case")
    p.add_argument("--ref", default=settings.DEMO_CONTACT_REF,
                   help=f"customer_ref of the demo case (default: {settings.DEMO_CONTACT_REF})")
    p.add_argument("--save", action="store_true", help="Capture the current state as the baseline")
    p.add_argument("--dry-run", action="store_true", help="Show what a rewind would change, write nothing")
    a = p.parse_args()

    db = SessionLocal()
    try:
        if a.save:
            r = demo_service.save_baseline(db, a.ref)
            print(f"Baseline saved for {r['ref']} ({r['case_number']}) at {r['taken_at'].isoformat()}")
            print(f"  status={r['status']}  visits={r['visit_count']}  collected={r['collected_amount']}")
            print("\nRewind with:  python -m scripts.demo_reset")
            return

        r = demo_service.rewind(db, a.ref, dry_run=a.dry_run)
        head = "WOULD REWIND" if r["dry_run"] else "REWOUND"
        print(f"{head} {r['ref']} ({r['case_number']}) to the baseline of {r['taken_at'].isoformat()}")
        print(f"  deleted rows : {r['deleted'] or 'none'}")
        for field, (was, now) in r["changed"].items():
            print(f"  {field:34} {was!r} -> {now!r}")
        if r["clean"]:
            print("  already clean — nothing to undo.")
    except (demo_service.DemoBaselineMissing, demo_service.DemoCaseNotFound) as e:
        raise SystemExit(str(e))
    finally:
        db.close()


if __name__ == "__main__":
    main()
