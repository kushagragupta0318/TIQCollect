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

The 2026-09-18 plan (PLANS["2026-09-18"]), again for 214 planned cases:

    25  PAID_FULL        HIGH balances only (top third); case -> PAID, resolved
    40  PART_PAID        10-45% of the balance
    50  PART_PAID_PTP    part payment + promise
    26  PTP              promise only; half due next week (7-13 d), half in October
    10  met, no money    4 RTP (refused to pay -> case ESCALATED, CUSTOMER_HOSTILE,
                         exactly as VisitService does) + 6 REVISIT with a money-
                         issue default_reason and "revisit required" in the notes
    23  NOT_AVAILABLE    doors that did not open
    40  (not visited)
   ---
   214

Plans are keyed by date; the script refuses to run on a day it has no plan
for, so a re-run on the wrong day cannot silently replay yesterday's shape.

The 2026-09-21 plan, for 225 planned cases (15 agents x 15):

    40  PAID_FULL        promises due today first (none were), then the top third
    50  PART_PAID        OLD cases first — most-visited (only 24 had 2+ visits)
    29  PTP              due next month or the month after, random dates
    24  REVISIT          met, no money: hardship reason, LOW balances
    50  NOT_AVAILABLE    not met
    32  (not visited)

Per-outcome `pick` rules (see PLANS): "high" / "low" take from the top / bottom
third of collectable balance, "old_first" sorts by visit_count descending,
"ptp_due_first" puts cases with a promise due today ahead of the rest.

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

# One plan per day. `outcomes` is filled in order after PAID_FULL, which is
# drawn first by `paid_full` — ("low_mostly", n_low, n_high) takes n_low from
# the lowest third of balances and n_high from the top third; ("high", n)
# takes all n from the top third. `ptp_dates` names the promise-date policy.
PLANS: dict[str, dict] = {
    "2026-09-17": {
        "paid_full": ("low_mostly", 26, 6),
        "outcomes": [("PAID_FULL", 32), ("PART_PAID", 54), ("PTP", 15),
                     ("PART_PAID_PTP", 40), ("NOT_AVAILABLE", 30)],
        "ptp_dates": "7_to_40",
    },
    "2026-09-18": {
        "paid_full": ("high", 25),
        "outcomes": [("PAID_FULL", 25), ("PART_PAID", 40), ("PART_PAID_PTP", 50), ("PTP", 26),
                     ("RTP", 4), ("REVISIT", 6), ("NOT_AVAILABLE", 23)],
        "ptp_dates": "next_week_or_october",
    },
    "2026-09-21": {
        "paid_full": ("high", 40),
        "outcomes": [("PAID_FULL", 40), ("PART_PAID", 50), ("PTP", 29),
                     ("REVISIT", 24), ("NOT_AVAILABLE", 50)],
        # how the non-PAID_FULL outcomes choose their cases from what is left
        "pick": {"PART_PAID": "old_first", "REVISIT": "low"},
        "paid_full_ptp_due_first": True,
        "ptp_dates": "next_month_or_two",
    },
}
# Met, no money: what the borrower said. RTP escalates the case (VisitService);
# REVISIT records a capacity reason and asks for another visit.
RTP_NOTES = [
    "Borrower met; refuses to pay, says the bank should take it up legally.",
    "Borrower met; flatly refused to pay and asked the agent to leave.",
    "Borrower met; refuses to pay until the bank waives the penal charges.",
]
REVISIT_MONEY_ISSUES = [
    ("SALARY_CUT", "Borrower met; salary cut this quarter, cannot pay this week. Revisit required after salary date."),
    ("JOB_LOSS", "Borrower met; lost job last month, looking for work. Revisit required in two weeks."),
    ("OVER_LEVERAGED", "Borrower met; servicing three other loans, no money this month. Revisit required."),
    ("MEDICAL", "Borrower met; hospital expenses this month, will pay next month. Revisit required."),
    ("BUSINESS_FAILURE", "Borrower met; shop closed, no income right now. Revisit required."),
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
    ap.add_argument("--seed", type=int, default=None, help="default: YYYYMMDD of today")
    ap.add_argument("--manager-email", default="manager1@tiqcollect.in")
    ap.add_argument("--undo", type=str, help="Manifest from a previous run (also restores ptps_set)")
    args = ap.parse_args()

    if args.undo:
        _undo(pathlib.Path(args.undo))
        return

    today = date.today()
    plan_cfg = PLANS.get(today.isoformat())
    if plan_cfg is None:
        raise SystemExit(f"no plan defined for {today}; add one to PLANS before running")
    PLAN: list[tuple[str, int]] = plan_cfg["outcomes"]
    if args.seed is None:
        args.seed = int(today.strftime("%Y%m%d"))
    rng = random.Random(args.seed)
    db = SessionLocal()
    try:
        from app.models.user import User
        from app.models.case import EscalationReason
        from app.models.visit import DefaultReason
        mgr = db.query(User).filter(User.email == args.manager_email).one()
        agent_ids = [a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == mgr.id)]
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

        # PAID_FULL first, by the day's rule, over balances sorted ascending.
        by_bal = sorted(collectable, key=lambda p: remaining(p[0]))
        third = len(by_bal) // 3
        low, high = by_bal[:third], by_bal[-third:]
        pf = plan_cfg["paid_full"]
        if pf[0] == "low_mostly":
            paid_full = rng.sample(low, pf[1]) + rng.sample(high, pf[2])
            paid_full_note = f"paid-in-full: {pf[1]} drawn from the lowest third of balances, {pf[2]} from the highest third"
        else:
            due_today: list = []
            if plan_cfg.get("paid_full_ptp_due_first"):
                due_ids = {r[0] for r in db.query(PTP.case_id).filter(PTP.status == PTPStatus.ACTIVE, PTP.committed_date <= today).all()}
                due_today = [p for p in collectable if p[0].id in due_ids][:pf[1]]
            high_pool = [p for p in high if p[0].id not in {q[0].id for q in due_today}]
            paid_full = due_today + rng.sample(high_pool, pf[1] - len(due_today))
            paid_full_note = (f"paid-in-full: {len(due_today)} with a promise due today, "
                              f"{pf[1] - len(due_today)} from the highest third of balances (Rs {remaining(high[0][0]):,.0f}+)")
        assert len(paid_full) == PLAN[0][1] and PLAN[0][0] == "PAID_FULL"
        taken = {p[0].id for p in paid_full}
        rest = [p for p in collectable if p[0].id not in taken]
        rng.shuffle(rest)

        assignment: list[tuple[Case, Agent, str]] = [(c, a, "PAID_FULL") for c, a in paid_full]
        pick_rules = plan_cfg.get("pick", {})
        pool = list(rest)                      # already shuffled
        pick_notes = []
        for outcome, n in PLAN[1:]:
            rule = pick_rules.get(outcome)
            if rule == "old_first":
                ordered = sorted(pool, key=lambda p: -(p[0].visit_count or 0))
                chunk = ordered[:n]
                pick_notes.append(f"{outcome}: {sum(1 for c, _ in chunk if (c.visit_count or 0) >= 2)} of {n} had 2+ earlier visits")
            elif rule == "low":
                ordered = sorted(pool, key=lambda p: remaining(p[0]))
                chunk = ordered[:n]
                pick_notes.append(f"{outcome}: balances Rs {remaining(chunk[0][0]):,.0f}-{remaining(chunk[-1][0]):,.0f}")
            elif rule == "high":
                ordered = sorted(pool, key=lambda p: -remaining(p[0]))
                chunk = ordered[:n]
            else:
                chunk = pool[:n]
            taken_ids = {c.id for c, _ in chunk}
            pool = [p for p in pool if p[0].id not in taken_ids]
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

        def _ptp_date() -> date:
            if plan_cfg["ptp_dates"] == "next_month_or_two":
                nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)   # 1st of next month
                after = (nxt + timedelta(days=32)).replace(day=1)                   # 1st of the month after
                start = nxt if rng.random() < 0.5 else after
                return start + timedelta(days=rng.randint(0, 27))
            if plan_cfg["ptp_dates"] == "next_week_or_october":
                if rng.random() < 0.5:
                    return today + timedelta(days=rng.randint(7, 13))           # next week
                nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)  # 1st of next month
                return nxt + timedelta(days=rng.randint(0, 30))                 # anywhere in it
            return today + timedelta(days=rng.randint(PTP_MIN_DAYS, PTP_MAX_DAYS))

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
                # RTP escalates; restored on --undo
                "is_escalated": bool(case.is_escalated),
                "escalation_reason": case.escalation_reason.value if case.escalation_reason else None,
                "escalated_at": case.escalated_at.isoformat() if case.escalated_at else None,
            })
            agents_before.setdefault(agent.id, {
                "current_month_visits": agent.current_month_visits or 0,
                "current_month_collections": float(agent.current_month_collections or 0.0),
                "current_month_ptps_set": agent.current_month_ptps_set or 0,
            })
            totals[outcome_s] += 1
            money += amount

            if promises:
                ptp_dates.append(_ptp_date())
            met_no_money = outcome in (VisitOutcome.RTP, VisitOutcome.REVISIT)
            reason, note = None, None
            if outcome == VisitOutcome.RTP:
                note = rng.choice(RTP_NOTES)
            elif outcome == VisitOutcome.REVISIT:
                reason, note = rng.choice(REVISIT_MONEY_ISSUES)

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
                default_reason=DefaultReason(reason) if reason else None,
                visit_number=(case.visit_count or 0) + 1,
                notes=rng.choice(NOT_MET_NOTES) if not_met else (note if met_no_money else None),
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
            elif outcome == VisitOutcome.RTP:
                case.status = CaseStatus.ESCALATED
                case.is_escalated = True
                case.escalation_reason = EscalationReason.CUSTOMER_HOSTILE
                case.escalated_at = when
            elif case.status == CaseStatus.ASSIGNED:      # REVISIT / NOT_AVAILABLE
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
        print(f"  {paid_full_note}")
        for note in pick_notes:
            print(f"  {note}")

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
                if "is_escalated" in before:
                    from app.models.case import EscalationReason
                    c.is_escalated = before["is_escalated"]
                    c.escalation_reason = EscalationReason(before["escalation_reason"]) if before["escalation_reason"] else None
                    c.escalated_at = datetime.fromisoformat(before["escalated_at"]) if before["escalated_at"] else None
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
