"""One-off: move the demo pool's borrowers out of the Gurugram box and across
every agent's territory. 2026-09-21.

WHY. Until 2026-09-21 the demo daily feed placed every new borrower inside one
Gurugram bounding box, so the open pool (608 cases / Rs 63.2L that morning)
was reachable only by the Gurugram agents and the Noida / Greater Noida / east
Delhi agents were planned onto their own Rs 1-3K leftovers, 80-125 km a day.
The feed is fixed for future batches; this moves the batch that already
exists, so tomorrow's plan can use it rather than next week's.

WHAT IT TOUCHES. Only borrowers of DAILY* demo cases that are UNASSIGNED, open,
and have NEVER been visited — nobody has stood at that address, no evidence
references it, no beat routes to it. It rewrites latitude / longitude / city /
state / pincode using the feed's own `_point_near` / `_territory_anchors` /
`_city_for` (one placement rule, imported, not restated). Everything else —
loan, balances, DPD, scores — is untouched, and cases that are assigned,
visited, or not from the demo feed are never selected.

    python -m scripts.respread_pool_cases            # dry run: counts + manifest
    python -m scripts.respread_pool_cases --apply
    python -m scripts.respread_pool_cases --undo /tmp/manifest.json
    --seed N   (default 20260921) for a reproducible draw
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal                                     # noqa: E402
from app.models.case import Case                                               # noqa: E402
from app.models.customer import Customer                                       # noqa: E402
from app.workers.tasks import demo_daily_feed as feed                          # noqa: E402

ROLLBACK_DIR = pathlib.Path(__file__).resolve().parents[1] / "docs" / "rollback"
_RESOLVED = ("PAID", "CLOSED", "WRITTEN_OFF")


def _eligible(db):
    """Customers of DAILY* cases that are unassigned, open and never visited —
    and whose EVERY case is in that state (a borrower with one worked case
    is not moved)."""
    rows = (
        db.query(Customer, Case)
        .join(Case, Case.customer_id == Customer.id)
        .filter(Case.case_number.like("DAILY%"))
        .all()
    )
    by_cust: dict[str, list[Case]] = {}
    custs: dict[str, Customer] = {}
    for cu, c in rows:
        by_cust.setdefault(cu.id, []).append(c)
        custs[cu.id] = cu
    out = []
    for cid, cases in by_cust.items():
        if all(c.agent_id is None and (c.visit_count or 0) == 0 and c.status.value not in _RESOLVED for c in cases):
            out.append((custs[cid], cases))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", type=str)
    ap.add_argument("--seed", type=int, default=20260921)
    args = ap.parse_args()
    db = SessionLocal()
    try:
        if args.undo:
            m = json.loads(pathlib.Path(args.undo).read_text(encoding="utf-8"))
            n = 0
            for r in m["rows"]:
                cu = db.get(Customer, r["customer_id"])
                if cu is None:
                    continue
                b = r["before"]
                cu.latitude, cu.longitude = b["latitude"], b["longitude"]
                cu.city, cu.state, cu.pincode = b["city"], b["state"], b["pincode"]
                n += 1
            db.commit()
            print(f"undone: {n} borrowers restored")
            return

        random.seed(args.seed)
        anchors = feed._territory_anchors(db)
        if not anchors:
            sys.exit("no agent bases found; nothing to spread against")
        elig = _eligible(db)
        rows = []
        for cu, cases in elig:
            base_lat, base_lon, terr = random.choice(anchors)
            lat, lon = feed._point_near(base_lat, base_lon)
            city, state, pin = feed._city_for(terr)
            rows.append({
                "customer_id": cu.id, "customer_ref": cu.customer_ref, "cases": [c.case_number for c in cases],
                "before": {"latitude": cu.latitude, "longitude": cu.longitude, "city": cu.city, "state": cu.state, "pincode": cu.pincode},
                "after": {"latitude": lat, "longitude": lon, "city": city, "state": state, "pincode": pin, "near": terr},
            })
        before_city = Counter(r["before"]["city"] for r in rows)
        after_city = Counter(r["after"]["city"] for r in rows)
        collectable = sum(float(c.target_amount or 0) - float(c.collected_amount or 0) for _, cs in elig for c in cs)
        print(f"eligible borrowers: {len(rows)}  cases: {sum(len(r['cases']) for r in rows)}  collectable: Rs {collectable:,.0f}")
        print(f"  city before: {dict(before_city)}")
        print(f"  city after : {dict(after_city)}")
        print(f"  anchors    : {len(anchors)} agent bases")

        ROLLBACK_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out = ROLLBACK_DIR / f"2026-09-21-respread-pool-{'applied' if args.apply else 'dryrun'}-{stamp}.json"
        out.write_text(json.dumps({"written_at": datetime.now(timezone.utc).isoformat(), "dry_run": not args.apply,
                                   "seed": args.seed, "rows": rows}, indent=1), encoding="utf-8")
        print(f"manifest -> {out}")
        if not args.apply:
            print("(dry run — nothing written)")
            return
        for r in rows:
            cu = db.get(Customer, r["customer_id"])
            a = r["after"]
            cu.latitude, cu.longitude = a["latitude"], a["longitude"]
            cu.city, cu.state, cu.pincode = a["city"], a["state"], a["pincode"]
        db.commit()
        print(f"applied: {len(rows)} borrowers moved")
        print(f"undo -> python -m scripts.respread_pool_cases --undo {out}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
