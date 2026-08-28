# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-27 — New file. READ-ONLY replay of the visit-priority ordering.
#
#   WHY IT EXISTS. The nightly allocator orders UNASSIGNED cases, and on the
#   demo book there are none — every case is already assigned. So the ordering
#   cannot be watched by running the job; without this there is no way to answer
#   the only question that matters about a ranking: "would a manager accept this
#   order?"
#
#   Writes nothing. Opens the connection with default_transaction_read_only=on,
#   so it cannot write even by mistake.
#
#     python -m scripts.rank_visit_priority            # top 20 + distribution
#     python -m scripts.rank_visit_priority --top 40
# ─────────────────────────────────────────────────────────────────────────────
"""Score the open book by visit priority and print the order, read-only."""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Windows consoles default to cp1252 and the output uses box-drawing characters.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import joinedload, sessionmaker  # noqa: E402

from app.models.case import Case, CaseStatus  # noqa: E402
from app.services.visit_priority_service import score_cases  # noqa: E402

# backend/.env refers to shared values as ${VAR}, which no longer resolve on the
# host (see CLAUDE.md), so settings.DATABASE_URL arrives without credentials
# when this is run outside the container. Honour DATABASE_URL when it is set and
# usable, otherwise fall back to the published dev port.
_DEV_URL = ("postgresql+psycopg2://fieldops:fieldops_dev_pass"
            "@localhost:15432/fieldops")

_OPEN = [CaseStatus.ASSIGNED, CaseStatus.IN_PROGRESS, CaseStatus.PTP_SET,
         CaseStatus.PARTIALLY_PAID, CaseStatus.ESCALATED]

_SHORT = {"RECOVERABLE_VALUE": "value", "URGENCY": "urgency", "EFFORT": "effort"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=20, metavar="N")
    ap.add_argument("--url", default=None, metavar="DSN",
                    help="database URL; defaults to DATABASE_URL or the dev port")
    args = ap.parse_args()

    env_url = os.environ.get("DATABASE_URL", "")
    url = args.url or (env_url if "${" not in env_url and env_url else _DEV_URL)
    engine = create_engine(
        url, connect_args={"options": "-c default_transaction_read_only=on"})
    db = sessionmaker(bind=engine)()

    cases = (
        db.query(Case)
        .options(joinedload(Case.loan), joinedload(Case.customer))
        .filter(Case.status.in_(_OPEN))
        .all()
    )
    print(f"open cases scored: {len(cases):,}")
    if not cases:
        print("nothing to rank")
        return

    scored = score_cases(db, cases)
    ranked = sorted(cases, key=lambda c: (-scored[c.id]["score"], c.case_number))

    print()
    print(f"── top {args.top} by visit priority "
          f"{'─' * 40}")
    print(f"  {'#':>3} {'case':14s} {'score':>6} {'value':>6} {'urg':>5} "
          f"{'eff':>5}  {'dpd':>4} {'outstanding':>13}  reason")
    for i, case in enumerate(ranked[:args.top], start=1):
        s = scored[case.id]
        pts = {c["code"]: c["points"] for c in s["components"]}
        loan = case.loan
        print(f"  {i:>3} {case.case_number:14s} {s['score']:>6.1f} "
              f"{pts['RECOVERABLE_VALUE']:>6.0f} {pts['URGENCY']:>5.0f} "
              f"{pts['EFFORT']:>5.0f}  {(loan.dpd if loan else 0):>4} "
              f"{(loan.total_outstanding if loan else 0)/1e5:>11,.1f}L  "
              f"{s['reason'][:52]}")

    print()
    print("── score distribution ─────────────────────────────────────────────")
    buckets: dict[int, int] = {}
    for case in cases:
        buckets[int(scored[case.id]["score"] // 10) * 10] = \
            buckets.get(int(scored[case.id]["score"] // 10) * 10, 0) + 1
    for lo in sorted(buckets):
        n = buckets[lo]
        print(f"  {lo:>3}-{lo+9:<3} {'#' * max(1, n * 40 // len(cases))} {n}")

    print()
    print("── does this order differ from the orders it replaced? ────────────")
    by_score = [c.case_number for c in ranked]
    by_dpd = [c.case_number for c in
              sorted(cases, key=lambda c: (-(c.loan.dpd if c.loan else 0),
                                           c.case_number))]
    by_priority = [c.case_number for c in
                   sorted(cases, key=lambda c: (-(c.allocation_score or 0.0),
                                                c.case_number))]
    for label, other in (("DPD order", by_dpd), ("old allocation_score", by_priority)):
        top = min(20, len(cases))
        overlap = len(set(by_score[:top]) & set(other[:top]))
        print(f"  top-{top} overlap with {label:22s} {overlap}/{top}")

    print()
    print("── components that abstained (no data to work with) ───────────────")
    abstained: dict[str, int] = {}
    for case in cases:
        for comp in scored[case.id]["components"]:
            if comp.get("abstained"):
                key = _SHORT.get(comp["code"], comp["code"])
                abstained[key] = abstained.get(key, 0) + 1
    print(f"  {abstained or 'none — every case scored on all three components'}")

    zero = [c for c in cases if scored[c.id]["score"] <= 0.0]
    print(f"  cases scoring zero on everything: {len(zero)}")
    db.close()


if __name__ == "__main__":
    main()
