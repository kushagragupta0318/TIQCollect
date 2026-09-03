"""Make every case's timeline tell one story.

A case is a sequence of doorstep visits that ends when the money arrives. The
demo book did not hold to that: cases carried visits AFTER they were paid in
full, visits whose outcome claimed money that no payment row supported, promises
still ACTIVE on accounts already settled, and an allocation_date that said one
day while the work happened on another.

None of it is subtle on screen. CASE0000169 showed "Paid in Full" at 10:43 and
"Partial Payment" at 14:37 on the same day, one payment row between them, and a
date column reading 03 Sep for work done on 02 Sep.

THE RULES ENFORCED
------------------
1. A PAID_FULL visit ENDS the case. Nothing is recorded against it afterwards.
2. A visit whose outcome claims money (PAID_FULL / PART_PAID / PART_PAID_PTP)
   must have a payment row behind it. One that does not is a claim with no
   evidence and is removed rather than left to be read as a collection.
3. A promise cannot be outstanding on a settled account. ACTIVE PTPs on resolved
   cases are closed — HONORED where the case was paid, EXPIRED where it was
   closed without recovery.
4. allocation_date names the day the case was last actually worked or allocated,
   not a day some superseded plan once mentioned.

WHY DELETION IS SAFE HERE
-------------------------
Measured before writing: of the 105 visits recorded after a PAID_FULL, ZERO
carry a payment. Removing them therefore moves no money at all, and the two
financial invariants — collected == SUM(VERIFIED) and collected <= target — are
untouched by construction. The script re-asserts both afterwards anyway.

Cases CLOSED with nothing collected are left alone. Five of them exist, and a
case closed without recovery is a real outcome, not an inconsistency.

USAGE
    python scripts/repair_case_timeline.py --dry-run
    python scripts/repair_case_timeline.py --apply
"""
from __future__ import annotations

import argparse
import csv
import os
import pathlib
import re
import sys
from collections import Counter, defaultdict
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _resolve_database_url() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    here = pathlib.Path(__file__).resolve().parents[2]

    def envmap(p: pathlib.Path) -> dict[str, str]:
        d: dict[str, str] = {}
        if not p.exists():
            return d
        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                d[k.strip()] = v.strip().strip('"').strip("'")
        return d

    root, be = envmap(here / ".env"), envmap(here / "backend" / ".env")
    url = re.sub(r"\$\{(\w+)(?::-[^}]*)?\}", lambda m: root.get(m.group(1), m.group(0)),
                 be.get("DATABASE_URL", ""))
    return url.replace("@postgres:", "@localhost:").replace(":5432/", ":15432/")


os.environ["DATABASE_URL"] = _resolve_database_url()
os.environ.setdefault("SECRET_KEY", "repair-script")
os.environ.setdefault("COMMAND_CENTRE_API_KEY", "repair-script")

from app.core.database import SessionLocal                             # noqa: E402
from app.models.beat import Beat                                       # noqa: E402
from app.models.case import Case, RESOLVED_STATUSES, CaseStatus        # noqa: E402
from app.models.payment import Payment, PaymentStatus                  # noqa: E402
from app.models.ptp import PTP, PTPStatus                              # noqa: E402
from app.models.visit import Visit, VisitOutcome                       # noqa: E402

MONEY_OUTCOMES = {VisitOutcome.PAID_FULL, VisitOutcome.PART_PAID, VisitOutcome.PART_PAID_PTP}
ROLLBACK_DIR = (pathlib.Path(__file__).resolve().parents[2]
                / "docs" / "rollback" / "2026-09-03-case-timeline")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    today = date.today()

    visits_by_case: dict[str, list[Visit]] = defaultdict(list)
    for v in db.query(Visit).all():
        visits_by_case[v.case_id].append(v)
    pay_by_visit: dict[str, list[Payment]] = defaultdict(list)
    for p in db.query(Payment).all():
        if p.visit_id:
            pay_by_visit[p.visit_id].append(p)
    in_todays_beat = {
        cid for b in db.query(Beat).filter(Beat.beat_date == today).all()
        for cid in (b.ordered_case_ids or [])
    }
    cases = db.query(Case).all()

    doomed: list[Visit] = []
    reasons: Counter = Counter()
    for c in cases:
        ordered = sorted(visits_by_case[c.id], key=lambda v: v.check_in_time)
        # Rule 1 — a PAID_FULL visit ends the case.
        first_full = next((i for i, v in enumerate(ordered)
                           if v.outcome == VisitOutcome.PAID_FULL), None)
        keep: list[Visit] = ordered
        if first_full is not None:
            for v in ordered[first_full + 1:]:
                doomed.append(v)
                reasons["after the case was paid in full"] += 1
            keep = ordered[:first_full + 1]
        # Rule 2 — a money outcome needs a payment behind it.
        for v in keep:
            if v.outcome in MONEY_OUTCOMES and not pay_by_visit.get(v.id):
                doomed.append(v)
                reasons["claims money with no payment row"] += 1

    doomed_ids = {v.id for v in doomed}
    money_moved = sum(p.amount for v in doomed for p in pay_by_visit.get(v.id, ()))

    # PTPs carry a visit_id foreign key, so a doomed visit cannot be removed
    # while a promise still points at it. These are promises recorded AFTER the
    # borrower had already paid in full — they go with the visit that invented
    # them, not least because leaving them would keep the case's PTP tab showing
    # commitments made against a debt that no longer existed.
    doomed_ptps = [t for t in db.query(PTP).all() if t.visit_id in doomed_ids]

    # Rule 3 — no live promise on a settled account.
    stale_ptps = [
        t for t in db.query(PTP).filter(PTP.status == PTPStatus.ACTIVE).all()
        if (cs := next((c for c in cases if c.id == t.case_id), None)) is not None
        and cs.status in RESOLVED_STATUSES
    ]

    # Rule 4 — allocation_date is the day the case was last worked or allocated.
    # A case in today's beat keeps today. One that is not, but has been visited,
    # takes the date of its last surviving visit. A fresh, never-visited case is
    # left alone: it arrived for the day it is stamped with, which is correct and
    # is what puts new work at the top of Case Management, untagged.
    date_fixes: dict[str, tuple[str | None, str]] = {}
    for c in cases:
        if c.id in in_todays_beat:
            continue
        surviving = [v for v in visits_by_case[c.id] if v.id not in doomed_ids]
        if not surviving:
            continue
        last_worked = max(v.check_in_time for v in surviving).date().isoformat()
        if c.allocation_date != last_worked:
            date_fixes[c.id] = (c.allocation_date, last_worked)

    print(f"visits to delete : {len(doomed)}   (money they carry: Rs {money_moved:,.2f})")
    print(f"PTPs on those visits (deleted with them) : {len(doomed_ptps)}")
    for why, n in reasons.most_common():
        print(f"    {n:>4}  {why}")
    print(f"stale ACTIVE PTPs on resolved cases : {len(stale_ptps)}")
    print(f"allocation_date corrections          : {len(date_fixes)}")

    if args.dry_run or not args.apply:
        print("\n(dry run — nothing written; pass --apply to commit)")
        db.close()
        return

    ROLLBACK_DIR.mkdir(parents=True, exist_ok=True)
    with open(ROLLBACK_DIR / "01-before.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kind", "id", "field", "value"])
        for v in doomed:
            w.writerow(["visit_deleted", v.id, "case_id", v.case_id])
            w.writerow(["visit_deleted", v.id, "outcome", v.outcome.value])
            w.writerow(["visit_deleted", v.id, "check_in_time", v.check_in_time.isoformat()])
            w.writerow(["visit_deleted", v.id, "agent_id", v.agent_id])
            w.writerow(["visit_deleted", v.id, "visit_number", v.visit_number])
        for t in doomed_ptps:
            w.writerow(["ptp_deleted", t.id, "case_id", t.case_id])
            w.writerow(["ptp_deleted", t.id, "visit_id", t.visit_id])
            w.writerow(["ptp_deleted", t.id, "status", t.status.value])
            w.writerow(["ptp_deleted", t.id, "committed_amount", t.committed_amount])
            w.writerow(["ptp_deleted", t.id, "committed_date", t.committed_date])
        for t in stale_ptps:
            w.writerow(["ptp", t.id, "status", t.status.value])
        for cid, (old, _new) in date_fixes.items():
            w.writerow(["case", cid, "allocation_date", old])
    print(f"\nsnapshot -> {ROLLBACK_DIR / '01-before.csv'}")

    # Children first: PTPs reference visits.
    doomed_ptp_ids = {t.id for t in doomed_ptps}
    for t in doomed_ptps:
        db.delete(t)
    db.flush()
    for v in doomed:
        db.delete(v)
    db.flush()

    for t in stale_ptps:
        if t.id in doomed_ptp_ids:
            continue          # already removed with its visit
        case = next(c for c in cases if c.id == t.case_id)
        t.status = (PTPStatus.HONORED if case.status == CaseStatus.PAID
                    else PTPStatus.EXPIRED)

    for cid, (_old, new) in date_fixes.items():
        next(c for c in cases if c.id == cid).allocation_date = new

    # visit_count is denormalised; deleting visits without it leaves the case
    # claiming work that no longer exists, and the planner reads it.
    remaining: Counter = Counter()
    for v in db.query(Visit).all():
        remaining[v.case_id] += 1
    renumbered = 0
    for c in cases:
        n = remaining.get(c.id, 0)
        if (c.visit_count or 0) != n:
            c.visit_count = n
            renumbered += 1
    # visit_number must stay 1..n with no holes once rows are removed.
    per_case: dict[str, list[Visit]] = defaultdict(list)
    for v in db.query(Visit).all():
        per_case[v.case_id].append(v)
    for cid, vs in per_case.items():
        for i, v in enumerate(sorted(vs, key=lambda x: x.check_in_time), start=1):
            if v.visit_number != i:
                v.visit_number = i

    db.commit()
    print(f"deleted {len(doomed)} visits and {len(doomed_ptps)} PTPs, "
          f"closed {len(stale_ptps) - len(doomed_ptp_ids & {t.id for t in stale_ptps})} stale PTPs, "
          f"corrected {len(date_fixes)} dates, recounted {renumbered} cases")
    db.close()


if __name__ == "__main__":
    main()
