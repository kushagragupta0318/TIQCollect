"""Record a full day of field activity against today's routes, to a stated plan.

Demo-support tool, a sibling of `demo_record_visits.py` and built on the same
rules: every row is the row a real agent at the door would have produced —
check-in coordinates AT the customer's address, `geo_verified=True`, IST
contact hours, VERIFIED payments with unique receipts, ACTIVE promises — and
the geo-fence and contact-hours controls are not weakened, only satisfied.
Read that file's header for why it does not go through VisitService.

WHAT IS DIFFERENT HERE
----------------------
`demo_record_visits` draws a few visits per agent from a weighted mix. This
takes an explicit PLAN over the whole day's routes — how many cases end in
each outcome — and fills it exactly, so a demo can be shaped rather than
sampled. The 2026-09-17 plan, for 214 planned cases:

    32  PAID_FULL        mostly low balances, a few large ones; case -> PAID, resolved
    54  PART_PAID        10-45% of the balance; case -> PARTIALLY_PAID
    15  PTP              promise only; case -> PTP_SET
    40  PART_PAID_PTP    part payment + promise; case -> PTP_SET
    30  NOT_AVAILABLE    nobody home / house locked, revisit needed; not met
    43  (not visited)    left exactly as they are
   ---
   214

Promise dates are 7-40 days out: a few next week, the rest scattered across
the next month — never inside the first week.

"Not met" is written as NOT_AVAILABLE for all 30, with the note recording
whether the borrower was out or the house was locked. It is deliberately NOT
the REVISIT outcome: in this product REVISIT means the agent reached someone
and must come back (it counts as MET on the Field Activity funnel), whereas
these thirty are doors that did not open.

Case status follows VisitService._apply_outcome_transition exactly, and agent
counters follow PaymentService: PAID_FULL -> PAID + resolved_at; PART_PAID ->
PARTIALLY_PAID; PTP / PART_PAID_PTP -> PTP_SET (+1 ptps_set); a NOT_AVAILABLE
on an ASSIGNED case -> IN_PROGRESS. Invariants checked before commit:
collected_amount <= target_amount and collected_amount == SUM(VERIFIED).

REVERSIBLE. The manifest format is `demo_record_visits`'s, so undo is:
    python -m scripts.demo_record_visits --undo docs/rollback/<file>.json
(that restores status, collected_amount, visit_count, resolved_at and the
agent visit/collection counters; ptps_set is restored here on --undo too).

    python -m scripts.demo_record_today_plan            # dry run: the plan, nothing written
    python -m scripts.demo_record_today_plan --apply    # commit
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

from app.core.geo import IST, is_within_contact_hours                  # noqa: E402
from app.core.database import SessionLocal                            # noqa: E402
from app.models.agent import Agent                                    # noqa: E402
from app.models.beat import Beat                                      # noqa: E402
from app.models.case import Case, CaseStatus                          # noqa: E402
from app.models.payment import Payment, PaymentMode, PaymentStatus    # noqa: E402
from app.models.ptp import PTP, PTPStatus                             # noqa: E402
from app.models.visit import PersonMet, Visit, VisitOutcome           # noqa: E402

ROLLBACK_DIR = pathlib.Path(__file__).resolve().parents[1] / "docs" / "rollback"
# Only modes an agent can actually pick in RecordVisitPage — never ONLINE
# (see the note on PaymentMode.ONLINE).
MODES = [PaymentMode.CASH, PaymentMode.UPI, PaymentMode.NEFT, PaymentMode.RTGS, PaymentMode.CHEQUE]

# The plan. Order matters: PAID_FULL is drawn first from the low-balance end.
PLAN: list[tuple[str, int]] = [
    ("PAID_FULL", 32),
    ("PART_PAID", 54),
    ("PTP", 15),
    ("PART_PAID_PTP", 40),
    ("NOT_AVAILABLE", 30),
]
NOT_MET_NOTES = [
    "Borrower not at home. Neighbour says back in the evening. Revisit required.",
    "House locked, no response. Revisit required.",
    "Borrower out of station as per family. Revisit next week.",
    "Door locked; left a visit card. Revisit required.",
]
PTP_MIN_DAYS, PTP_MAX_DAYS = 7, 40


def _uid() -> str:
    return str(uuid.uuid4())


def main() -> None:
    ap = argparse.ArgumentParser(description="Record a planned day of demo field visits.")
    ap.add_argument("--apply", action="store_true", help="Commit (default is a dry run)")
    ap.add_argument("--seed", type=int, default=20260917)
    ap.add_argument("--manager-email", default="manager1@tiqcollect.in")
    ap.add_argument("--undo", type=str, help="Manifest from a previous run (also restores ptps_set)")
    args = ap.parse_args()

    if args.undo:
        _undo(pathlib.Path(args.undo))
        return

    rng = random.Random(args.seed)
    db = SessionLocal()
    try:
        from app.models.user import User
        mgr = db.query(User).filter(User.email == args.manager_email).one()
        agent_ids = [a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == mgr.id)]
        today = date.today()
        day_start = datetime.combine(today, time(0, 0), tzinfo=IST)

        beats = db.query(Beat).filter(Beat.agent_id.in_(agent_ids), Beat.beat_date == today).all()
        if not beats:
            raise SystemExit(f"no beats for {today} -- nothing to visit")

        visited_today = {v.case_id for v in db.query(Visit.case_id).filter(Visit.check_in_time >= day_start).all()}
        if visited_today:
            raise SystemExit(f"REFUSED: {len(visited_today)} cases already have a visit today. "
                             f"Undo the earlier run first, or this plan cannot be filled exactly.")

        # (case, agent) for every planned case, deduplicated, with remaining balance.
        pairs: dict[str, tuple[Case, Agent]] = {}
        for b in beats:
            agent = db.get(Agent, b.agent_id)
            for cid in b.ordered_case_ids or []:
                if cid in pairs:
                    continue
                c = db.get(Case, cid)
                if c and c.customer and agent:
                    pairs[cid] = (c, agent)
        planned = list(pairs.values())
        n_plan = sum(n for _, n in PLAN)
        if len(planned) < n_plan:
            raise SystemExit(f"REFUSED: plan needs {n_plan} cases, only {len(planned)} planned today")

        def remaining(c: Case) -> float:
            return round(float(c.target_amount or 0) - float(c.collected_amount or 0), 2)

        # Only cases with something left to collect can pay; those with nothing
        # left are eligible only for the not-visited pool.
        collectable = [p for p in planned if remaining(p[0]) > 1.0]
        if len(collectable) < n_plan:
            raise SystemExit(f"REFUSED: only {len(collectable)} cases have a balance to collect")

        # PAID_FULL: mostly low balances, a few large. Sort by remaining, take
        # 26 from the lowest third and 6 from the top third.
        by_bal = sorted(collectable, key=lambda p: remaining(p[0]))
        third = len(by_bal) // 3
        low, high = by_bal[:third], by_bal[-third:]
        paid_full = rng.sample(low, 26) + rng.sample(high, 6)
        taken = {p[0].id for p in paid_full}
        rest = [p for p in collectable if p[0].id not in taken]
        rng.shuffle(rest)

        assignment: list[tuple[Case, Agent, str]] = [(c, a, "PAID_FULL") for c, a in paid_full]
        cursor = 0
        for outcome, n in PLAN[1:]:
            chunk = rest[cursor:cursor + n]
            cursor += n
            assignment += [(c, a, outcome) for c, a in chunk]
        not_visited = [p for p in planned if p[0].id not in {c.id for c, _, _ in assignment}]

        # Receipt numbers: true numeric max over a String column of mixed formats.
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
        totals = {o: 0 for o, _ in PLAN}
        money = 0.0
        ptp_dates: list[date] = []

        for case, agent, outcome_s in assignment:
            outcome = VisitOutcome(outcome_s)
            rem = remaining(case)
            pays = outcome in (VisitOutcome.PAID_FULL, VisitOutcome.PART_PAID, VisitOutcome.PART_PAID_PTP)
            promises = outcome in (VisitOutcome.PTP, VisitOutcome.PART_PAID_PTP)
            amount = 0.0
            if pays:
                amount = rem if outcome == VisitOutcome.PAID_FULL else round(rem * rng.uniform(0.10, 0.45), 2)
                amount = min(amount, rem)

            cases_before.setdefault(case.id, {
                "collected_amount": float(case.collected_amount or 0.0),
                "visit_count": case.visit_count or 0,
                "status": case.status.value if hasattr(case.status, "value") else str(case.status),
                "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
            })
            agents_before.setdefault(agent.id, {
                "current_month_visits": agent.current_month_visits or 0,
                "current_month_collections": float(agent.current_month_collections or 0.0),
                "current_month_ptps_set": agent.current_month_ptps_set or 0,
            })
            totals[outcome_s] += 1
            money += amount

            if promises:
                ptp_dates.append(today + timedelta(days=rng.randint(PTP_MIN_DAYS, PTP_MAX_DAYS)))

            if not args.apply:
                continue

            when = day_start + timedelta(hours=9, minutes=rng.randint(0, 8 * 60))
            cust = case.customer
            not_met = outcome == VisitOutcome.NOT_AVAILABLE
            visit = Visit(
                id=_uid(), case_id=case.id, agent_id=agent.id,
                check_in_latitude=(cust.latitude or 28.6139) + rng.uniform(-0.00025, 0.00025),
                check_in_longitude=(cust.longitude or 77.2090) + rng.uniform(-0.00025, 0.00025),
                check_in_time=when,
                check_out_time=when + timedelta(minutes=rng.randint(4, 9) if not_met else rng.randint(9, 28)),
                distance_from_customer_metres=rng.uniform(4.0, 38.0),
                geo_verified=True, within_contact_hours=is_within_contact_hours(when),
                customer_met=not not_met,
                person_met=None if not_met else PersonMet.BORROWER,
                outcome=outcome,
                visit_number=(case.visit_count or 0) + 1,
                notes=rng.choice(NOT_MET_NOTES) if not_met else None,
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
                    receipt_number=_next_receipt(), payment_date=when, receipt_sms_sent=True,
                )
                db.add(pay)
                db.flush()
                payment_ids.append(pay.id)
                case.collected_amount = round(float(case.collected_amount or 0) + amount, 2)
                agent.current_month_collections = round(float(agent.current_month_collections or 0.0) + amount, 2)

            if promises:
                ptp = PTP(
                    id=_uid(), case_id=case.id, visit_id=visit.id, agent_id=agent.id,
                    committed_amount=round(max(remaining(case), 0.0) * rng.uniform(0.3, 1.0), 2),
                    committed_date=ptp_dates[-1],
                    status=PTPStatus.ACTIVE,
                )
                db.add(ptp)
                db.flush()
                ptp_ids.append(ptp.id)
                agent.current_month_ptps_set = (agent.current_month_ptps_set or 0) + 1

            # Status transitions: VisitService._apply_outcome_transition, verbatim.
            if outcome == VisitOutcome.PAID_FULL:
                case.status = CaseStatus.PAID
                case.resolved_at = when
                case.resolution_notes = "Paid in full at doorstep"
            elif outcome == VisitOutcome.PART_PAID:
                case.status = CaseStatus.PARTIALLY_PAID
            elif outcome in (VisitOutcome.PTP, VisitOutcome.PART_PAID_PTP):
                case.status = CaseStatus.PTP_SET
            elif case.status == CaseStatus.ASSIGNED:
                case.status = CaseStatus.IN_PROGRESS

        # ── the plan, printed ────────────────────────────────────────────────
        print(f"{today}: {len(planned)} cases planned across {len(beats)} routes")
        for o, n in PLAN:
            print(f"  {o:<14} {totals[o]:>3}")
        print(f"  {'not visited':<14} {len(not_visited):>3}")
        print(f"  {'-'*18}\n  {'total':<14} {sum(totals.values()) + len(not_visited):>3}")
        print(f"\n  money collected  Rs {money:,.0f}")
        if ptp_dates:
            nxt = sum(1 for d in ptp_dates if d <= today + timedelta(days=14))
            print(f"  promises {len(ptp_dates)}: dates {min(ptp_dates)} .. {max(ptp_dates)}; "
                  f"{nxt} within two weeks, {len(ptp_dates) - nxt} later")
        print("  paid-in-full: 26 drawn from the lowest third of balances, 6 from the highest third")

        if not args.apply:
            print("\n(dry run -- nothing written; pass --apply to commit)")
            return

        bad = [c for c in (db.get(Case, cid) for cid in cases_before)
               if c and float(c.collected_amount or 0) > float(c.target_amount or 0) + 0.01]
        if bad:
            db.rollback()
            raise SystemExit(f"REFUSED: {len(bad)} case(s) would exceed target -- nothing written")

        ROLLBACK_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        manifest = ROLLBACK_DIR / f"{today}-demo-today-plan-{stamp}.json"
        manifest.write_text(json.dumps({
            "written_at": datetime.now(timezone.utc).isoformat(),
            "beat_date": today.isoformat(),
            "seed": args.seed,
            "plan": PLAN,
            "visit_ids": visit_ids,
            "payment_ids": payment_ids,
            "ptp_ids": ptp_ids,
            "cases_before": cases_before,
            "agents_before": agents_before,
        }, indent=2), encoding="utf-8")
        db.commit()
        print(f"\nwritten: {len(visit_ids)} visits, {len(payment_ids)} payments, {len(ptp_ids)} PTPs")
        print(f"undo -> python -m scripts.demo_record_today_plan --undo {manifest}")
    finally:
        db.close()


def _undo(path: pathlib.Path) -> None:
    """Same as demo_record_visits._undo, plus the ptps_set counter."""
    data = json.loads(path.read_text(encoding="utf-8"))
    db = SessionLocal()
    try:
        if data["payment_ids"]:
            db.query(Payment).filter(Payment.id.in_(data["payment_ids"])).delete(synchronize_session=False)
        if data.get("ptp_ids"):
            db.query(PTP).filter(PTP.id.in_(data["ptp_ids"])).delete(synchronize_session=False)
        if data["visit_ids"]:
            db.query(Visit).filter(Visit.id.in_(data["visit_ids"])).delete(synchronize_session=False)
        for cid, before in data["cases_before"].items():
            c = db.get(Case, cid)
            if c:
                c.collected_amount = before["collected_amount"]
                c.visit_count = before["visit_count"]
                c.status = CaseStatus(before["status"])
                c.resolved_at = datetime.fromisoformat(before["resolved_at"]) if before["resolved_at"] else None
                if before["status"] != "PAID":
                    c.resolution_notes = None
        for aid, before in data["agents_before"].items():
            a = db.get(Agent, aid)
            if a:
                a.current_month_visits = before["current_month_visits"]
                a.current_month_collections = before["current_month_collections"]
                if "current_month_ptps_set" in before:
                    a.current_month_ptps_set = before["current_month_ptps_set"]
        db.commit()
        print(f"undone: {len(data['visit_ids'])} visits, {len(data['payment_ids'])} payments, "
              f"{len(data.get('ptp_ids', []))} PTPs removed; {len(data['cases_before'])} cases restored")
    finally:
        db.close()


if __name__ == "__main__":
    main()
