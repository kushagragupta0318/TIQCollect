"""Record a few field visits per agent, with payments on some of them.

Demo-support tool. An agent cannot record a visit from a desk: VisitService
enforces a 100 m geo-fence against the customer's registered address
(visit_service.py, `within_geo_fence`) and RBI contact hours. Both are
load-bearing compliance controls and neither is weakened here -- this script
writes the rows a real agent standing at the door would have produced, with
check-in coordinates AT the customer's address, exactly as
`demo_collect_to_target.py` has done since 2026-09-02.

WHAT IT WRITES
--------------
For each agent holding cases in today's beats, N cases (default 3), each with

  * a Visit, GPS jittered a few metres around the customer's own coordinates,
    `geo_verified=True`, `within_contact_hours` derived from the IST check-in
    time by the same rule the API enforces, a plausible check-out;
  * for the paying outcomes only, a VERIFIED Payment with a unique receipt;
  * for the promising outcomes, an ACTIVE PTP with a committed date.

OUTCOMES ARE A MIX, NOT ALL PAYMENTS. A book where every visit collects is not
a collections book. Roughly a third pay, the rest promise, are absent or
refuse -- see OUTCOME_MIX. PAID_FULL is deliberately rare: clearing a whole
arrears balance at the doorstep is the exception in field collections.

EVERY ROW IS A REAL ROW
-----------------------
Payments are written VERIFIED because this codebase carries three different
definitions of "collected" -- unfiltered, == VERIFIED, and NOT IN (REJECTED,
REVERSED) -- so anything less appears on some screens and not others, which is
the exact class of inconsistency this database keeps being cleaned of.

THE TWO INVARIANTS THIS MUST NOT BREAK, both checked before the commit:
  collected_amount <= target_amount        (never write money against a debt
                                            that does not exist)
  collected_amount == SUM(VERIFIED payments)
`PaymentService.collect_payment` refuses over-collection outright
(payment_service.py:150). This script does not go through that service, so it
clamps to the remaining balance itself rather than relying on a guard it never
calls.

WHY NOT THROUGH VisitService
----------------------------
Because the service's whole job here is to refuse. Calling it would mean either
passing the customer's coordinates as the agent's -- which is what this does
anyway, minus the HTTP round trip -- or disabling the fence, which is the one
thing that must not happen.

REVERSIBLE
----------
Every id written is recorded to a JSON manifest under docs/rollback/. Undo with

    python -m scripts.demo_record_visits --undo docs/rollback/<file>.json

which deletes exactly those rows and restores each case and agent counter to the
value captured before the run. Nothing else is touched.

    python -m scripts.demo_record_visits                  # dry run, prints a plan
    python -m scripts.demo_record_visits --apply
    python -m scripts.demo_record_visits --apply --per-agent 2
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.geo import IST, is_within_contact_hours
from app.core.database import SessionLocal                           # noqa: E402
from app.models.agent import Agent                                   # noqa: E402
from app.models.beat import Beat                                     # noqa: E402
from app.models.case import Case, CaseStatus                         # noqa: E402
from app.models.payment import Payment, PaymentMode, PaymentStatus   # noqa: E402
from app.models.ptp import PTP, PTPStatus                            # noqa: E402
from app.models.visit import PersonMet, Visit, VisitOutcome          # noqa: E402

# parents[1], NOT parents[2]. This script is run inside the api container, where
# the repo is bind-mounted at /app == host ./backend and there is no parent above
# it — `parents[2]` resolves to "/" and the manifest lands in the container's own
# ephemeral filesystem, where it is lost the moment the container is recreated.
# The first run wrote to /docs/rollback and had to be rescued with `docker cp`.
# /app is the mount, so anchoring here puts the file on the host either way.
# (`demo_collect_to_target.py` uses parents[2] because it is run on the host.)
ROLLBACK_DIR = pathlib.Path(__file__).resolve().parents[1] / "docs" / "rollback"

#: (outcome, weight, money changes hands, a promise is recorded)
OUTCOME_MIX = [
    (VisitOutcome.PART_PAID,     6, True,  False),
    (VisitOutcome.PART_PAID_PTP, 3, True,  True),
    (VisitOutcome.PAID_FULL,     2, True,  False),
    (VisitOutcome.PTP,           6, False, True),
    (VisitOutcome.NOT_AVAILABLE, 5, False, False),
    (VisitOutcome.RTP,           2, False, False),
    (VisitOutcome.DISPUTE,       1, False, False),
]
MODES = [PaymentMode.CASH, PaymentMode.UPI, PaymentMode.NEFT, PaymentMode.CHEQUE]


def _uid() -> str:
    return str(uuid.uuid4())


def _pick(rng: random.Random):
    total = sum(w for _, w, _, _ in OUTCOME_MIX)
    r = rng.uniform(0, total)
    upto = 0.0
    for outcome, w, pays, promises in OUTCOME_MIX:
        upto += w
        if r <= upto:
            return outcome, pays, promises
    return OUTCOME_MIX[0][0], OUTCOME_MIX[0][2], OUTCOME_MIX[0][3]


def _undo(path: pathlib.Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    db = SessionLocal()
    try:
        pay_ids = data["payment_ids"]
        visit_ids = data["visit_ids"]
        ptp_ids = data.get("ptp_ids", [])
        if pay_ids:
            db.query(Payment).filter(Payment.id.in_(pay_ids)).delete(synchronize_session=False)
        if ptp_ids:
            db.query(PTP).filter(PTP.id.in_(ptp_ids)).delete(synchronize_session=False)
        if visit_ids:
            db.query(Visit).filter(Visit.id.in_(visit_ids)).delete(synchronize_session=False)
        # Counters are RESTORED to the value captured before the run rather than
        # decremented, so a partially-applied run cannot leave them drifting.
        for cid, before in data["cases_before"].items():
            c = db.get(Case, cid)
            if c:
                c.collected_amount = before["collected_amount"]
                c.visit_count = before["visit_count"]
                c.status = CaseStatus(before["status"])
                c.resolved_at = (datetime.fromisoformat(before["resolved_at"])
                                 if before["resolved_at"] else None)
        for aid, before in data["agents_before"].items():
            a = db.get(Agent, aid)
            if a:
                a.current_month_visits = before["current_month_visits"]
                a.current_month_collections = before["current_month_collections"]
        db.commit()
        print(f"undone: {len(visit_ids)} visits, {len(pay_ids)} payments, "
              f"{len(ptp_ids)} PTPs removed; {len(data['cases_before'])} cases and "
              f"{len(data['agents_before'])} agents restored")
    finally:
        db.close()


def _summary(day, visit_ids, payment_ids, ptp_ids, plan_rows) -> dict:
    return {"day": day.isoformat(), "visits": len(visit_ids), "payments": len(payment_ids),
            "ptps": len(ptp_ids), "planned": len(plan_rows),
            "collected": round(sum(r[3] for r in plan_rows), 2)}


def record_day(db, day, *, per_agent: int = 3, seed: int = 20260910, apply_changes: bool = False) -> dict:
    """Record one DAY's field activity against that day's beats.

    Extracted from main() unchanged (2026-10-07) so scripts/demo_catchup.py can
    fill a gap of past days with THIS logic rather than a second copy of it.
    Everything the header promises still holds: check-in coordinates at the
    customer's address, geo_verified, contact hours derived from the IST
    check-in time, VERIFIED payments with unique receipts, the
    collected <= target invariant checked BEFORE the commit, and a rollback
    manifest written for --undo.

    `day` replaces the old `date.today()`; main() passes today, catch-up passes
    each missing day. Returns a summary dict.
    """
    rng = random.Random(seed)
    # IST, explicitly: the 9:00-17:00 window below is meant in Indian time.
    # Built in UTC it was 14:30-22:30 IST, and 24 rows recorded on
    # 2026-09-10 sat outside contact hours while flagged inside them.
    day_start = datetime.combine(day, time(0, 0), tzinfo=IST)

    beats = db.query(Beat).filter(Beat.beat_date == day).all()
    if not beats:
        print(f"no beats for {day} -- nothing to visit")
        return _summary(day, [], [], [], [])

    # A case already visited day is skipped. A second visit to the same
    # case on the same day is not what this tool is for, and the duplicate
    # guard in VisitService exists for the same reason.
    visited_day = {
        v.case_id for v in db.query(Visit.case_id)
        .filter(Visit.check_in_time >= day_start).all()
    }

    # Receipt numbers are unique and this table holds several historical
    # formats, so func.max() over the String column sorts lexicographically
    # and returns the wrong row. Take the true numeric maximum and check
    # every candidate against what is already there.
    existing_receipts = {r[0] for r in db.query(Payment.receipt_number).all()}
    n0 = max((int(re.sub(r"\D", "", r) or 0) for r in existing_receipts), default=0)

    def _next_receipt() -> str:
        nonlocal n0
        while True:
            n0 += 1
            cand = f"RCP{n0}"
            if cand not in existing_receipts:
                existing_receipts.add(cand)
                return cand

    cases_before: dict[str, dict] = {}
    agents_before: dict[str, dict] = {}
    visit_ids: list[str] = []
    payment_ids: list[str] = []
    ptp_ids: list[str] = []
    plan_rows: list[tuple] = []

    for beat in sorted(beats, key=lambda b: b.agent_id):
        agent = db.get(Agent, beat.agent_id)
        if not agent:
            continue
        case_ids = [c for c in (beat.ordered_case_ids or []) if c not in visited_day]
        chosen = case_ids[: per_agent]
        if not chosen:
            continue

        if agent.id not in agents_before:
            agents_before[agent.id] = {
                "current_month_visits": agent.current_month_visits or 0,
                "current_month_collections": float(agent.current_month_collections or 0.0),
            }

        for cid in chosen:
            case = db.get(Case, cid)
            if not case or not case.customer:
                continue
            remaining = round(float(case.target_amount or 0)
                              - float(case.collected_amount or 0), 2)
            outcome, pays, promises = _pick(rng)
            # Nothing left to collect means nothing to collect. Fall back to
            # a promise rather than writing a zero-rupee payment.
            if pays and remaining <= 1.0:
                outcome, pays, promises = VisitOutcome.PTP, False, True

            amount = 0.0
            if pays:
                amount = (remaining if outcome == VisitOutcome.PAID_FULL
                          else round(remaining * rng.uniform(0.10, 0.45), 2))
                amount = min(amount, remaining)     # the invariant, enforced here

            cases_before.setdefault(case.id, {
                "collected_amount": float(case.collected_amount or 0.0),
                "visit_count": case.visit_count or 0,
                "status": case.status.value if hasattr(case.status, "value")
                          else str(case.status),
                "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
            })
            plan_rows.append((agent.employee_code, case.case_number, outcome.value, amount))

            if not apply_changes:
                continue

            when = day_start + timedelta(hours=9, minutes=rng.randint(0, 8 * 60))
            cust = case.customer
            visit = Visit(
                id=_uid(), case_id=case.id, agent_id=agent.id,
                check_in_latitude=(cust.latitude or 28.6139) + rng.uniform(-0.00025, 0.00025),
                check_in_longitude=(cust.longitude or 77.2090) + rng.uniform(-0.00025, 0.00025),
                check_in_time=when,
                check_out_time=when + timedelta(minutes=rng.randint(8, 27)),
                distance_from_customer_metres=rng.uniform(4.0, 38.0),
                geo_verified=True, within_contact_hours=is_within_contact_hours(when),
                customer_met=outcome != VisitOutcome.NOT_AVAILABLE,
                person_met=(PersonMet.BORROWER
                            if outcome != VisitOutcome.NOT_AVAILABLE else None),
                outcome=outcome,
                visit_number=(case.visit_count or 0) + 1,
            )
            db.add(visit)
            db.flush()
            visit_ids.append(visit.id)
            case.visit_count = (case.visit_count or 0) + 1
            agent.current_month_visits = (agent.current_month_visits or 0) + 1

            if pays and amount > 0:
                pay = Payment(
                    id=_uid(), case_id=case.id, visit_id=visit.id, agent_id=agent.id,
                    amount=amount, mode=rng.choice(MODES),
                    status=PaymentStatus.VERIFIED, verified_at=when,
                    receipt_number=_next_receipt(),
                    payment_date=when, receipt_sms_sent=True,
                )
                db.add(pay)
                db.flush()
                payment_ids.append(pay.id)
                case.collected_amount = round(float(case.collected_amount or 0) + amount, 2)
                agent.current_month_collections = round(
                    float(agent.current_month_collections or 0.0) + amount, 2)
                if case.collected_amount >= float(case.target_amount) - 0.01:
                    case.status = CaseStatus.PAID
                    case.resolved_at = when
                else:
                    case.status = CaseStatus.PARTIALLY_PAID

            if promises:
                ptp = PTP(
                    id=_uid(), case_id=case.id, visit_id=visit.id, agent_id=agent.id,
                    # `committed_amount`, not `promised_amount` — the column
                    # is named for what the borrower committed to, and the
                    # first draft of this script guessed the other name.
                    committed_amount=round(max(remaining - amount, 0.0)
                                           * rng.uniform(0.3, 1.0), 2),
                    committed_date=day + timedelta(days=rng.randint(2, 10)),
                    status=PTPStatus.ACTIVE,
                )
                db.add(ptp)
                db.flush()
                ptp_ids.append(ptp.id)

    agents_touched = len({r[0] for r in plan_rows})
    paying = [r for r in plan_rows if r[3] > 0]
    print(f"{len(plan_rows)} visits across {agents_touched} agents")
    print(f"  {len(paying)} collect money, total Rs {sum(r[3] for r in paying):,.0f}")
    for row in plan_rows[:8]:
        money = f"Rs {row[3]:,.0f}" if row[3] else ""
        print(f"    {row[0]}  {row[1]}  {row[2]:<14} {money}")
    if len(plan_rows) > 8:
        print(f"    ... and {len(plan_rows) - 8} more")

    if not apply_changes:
        print("\n(dry run -- nothing written; pass --apply to commit)")
        return

    # The invariant, checked BEFORE the commit rather than reported after it.
    bad = [c for c in (db.get(Case, cid) for cid in cases_before)
           if c and float(c.collected_amount or 0) > float(c.target_amount or 0) + 0.01]
    if bad:
        db.rollback()
        raise SystemExit(f"REFUSED: {len(bad)} case(s) would exceed target "
                         f"-- nothing written")

    ROLLBACK_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    manifest = ROLLBACK_DIR / f"{day}-demo-visits-{stamp}.json"
    manifest.write_text(json.dumps({
        "written_at": datetime.now(timezone.utc).isoformat(),
        "beat_date": day.isoformat(),
        "seed": seed,
        "visit_ids": visit_ids,
        "payment_ids": payment_ids,
        "ptp_ids": ptp_ids,
        "cases_before": cases_before,
        "agents_before": agents_before,
    }, indent=2), encoding="utf-8")

    db.commit()
    print(f"\nwritten: {len(visit_ids)} visits, {len(payment_ids)} payments, "
          f"{len(ptp_ids)} PTPs")
    print(f"undo -> python -m scripts.demo_record_visits --undo {manifest}")
    return _summary(day, visit_ids, payment_ids, ptp_ids, plan_rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Record demo field visits.")
    ap.add_argument("--apply", action="store_true", help="Commit (default is a dry run)")
    ap.add_argument("--per-agent", type=int, default=3, help="Cases to visit per agent")
    ap.add_argument("--seed", type=int, default=20260910)
    ap.add_argument("--date", dest="day", default=None,
                    help="The beat date to record against, ISO YYYY-MM-DD. Default: today in IST.")
    ap.add_argument("--undo", type=str, help="Path to a manifest from a previous run")
    args = ap.parse_args()

    if args.undo:
        _undo(pathlib.Path(args.undo))
        return

    # IST, not date.today(): the book's business day is Indian, and a UTC
    # "today" is yesterday for the first 5.5 hours of it.
    day = date.fromisoformat(args.day) if args.day else datetime.now(IST).date()
    db = SessionLocal()
    try:
        record_day(db, day, per_agent=args.per_agent, seed=args.seed, apply_changes=args.apply)
    finally:
        db.close()


if __name__ == "__main__":
    main()
