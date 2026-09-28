"""Bring today's collections up to a chosen share of what is actually owed.

Demo-support tool. Records field visits and VERIFIED payments against the cases
in today's beats until collections reach TARGET_SHARE of the amount that was
still to collect against those cases at the start of today.

WHY "OF WHAT IS OWED" AND NOT "OF TARGET"
-----------------------------------------
The obvious reading -- 80% of the summed target_amount of today's beat cases --
is not reachable. On 2026-09-02 that book carried Rs 1,76,60,492 of target
against Rs 96,39,996 already collected, 47 of the 214 cases having been paid off
in full and still sitting in the beat. The most that can ever be collected is
the Rs 80,20,496 of headroom; paying more would mean writing money against debts
that do not exist, which breaks collected <= target, breaks
collected == SUM(VERIFIED), and is
refused outright by PaymentService.collect_payment (payment_service.py:150).

So the share is taken against the REMAINING TARGET this morning -- assigned
target less what was already collected against it. Not Loan.total_outstanding,
which is the borrower's whole bill and a different number entirely. That
is also the number the dashboard gauge should be dividing by; it was not, and
manager.py's amount_target_today was corrected in the same change.

EVERY ROW IS A REAL ROW
-----------------------
Payments are written VERIFIED. The codebase carries three different definitions
of "collected" -- unfiltered, == VERIFIED, and NOT IN (REJECTED, REVERSED) -- so
anything less than VERIFIED appears on some screens and not others, which is
exactly the class of inconsistency this database keeps being cleaned of. Visits
carry check-in coordinates, geo verification and contact-hours flags, so the
compliance and fraud surfaces read them as ordinary field work.

The denormalised counters the application maintains on write (collected_amount,
status, visit_count, agent.current_month_*) are updated here too. Skipping them
is how a ledger and a screen drift apart.

USAGE
    python scripts/demo_collect_to_target.py --snapshot --dry-run
    python scripts/demo_collect_to_target.py --snapshot --apply
"""
from __future__ import annotations

import argparse
import csv
import os
import pathlib
import random
import re
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _resolve_database_url() -> str:
    """backend/.env holds ${VAR} placeholders that only resolved inside the
    platform monorepo (see CLAUDE.md). Substitute them from the root .env."""
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
os.environ.setdefault("SECRET_KEY", "demo-script")

from sqlalchemy import func                                             # noqa: E402
from app.core.geo import IST, is_within_contact_hours
from app.core.database import SessionLocal                              # noqa: E402
from app.models.agent import Agent                                      # noqa: E402
from app.models.beat import Beat                                        # noqa: E402
from app.models.case import Case, CaseStatus                            # noqa: E402
from app.models.payment import Payment, PaymentMode, PaymentStatus      # noqa: E402
from app.models.user import User, UserRole                              # noqa: E402
from app.models.visit import Visit, VisitOutcome, PersonMet             # noqa: E402

TARGET_SHARE = 0.80
PAYER_RATE = 0.82      # share of visitable cases that actually hand over money
SEED = 20260902        # fixed, so the same run twice produces the same plan
MODES = [PaymentMode.CASH, PaymentMode.UPI, PaymentMode.NEFT, PaymentMode.CHEQUE]
ROLLBACK_DIR = (pathlib.Path(__file__).resolve().parents[2]
                / "docs" / "rollback" / "2026-09-02-demo-collection")


def _uid() -> str:
    return str(uuid.uuid4())


def _beat_cases(db, aids, today):
    beats = db.query(Beat).filter(Beat.agent_id.in_(aids), Beat.beat_date == today).all()
    agent_of: dict[str, str] = {}
    for b in beats:
        for cid in (b.ordered_case_ids or []):
            agent_of.setdefault(cid, b.agent_id)
    cases = db.query(Case).filter(Case.id.in_(list(agent_of))).all() if agent_of else []
    return cases, agent_of


def _money_plan(cases, need, rng):
    """Split `need` across cases without exceeding any case's remaining target."""
    payable = [c for c in cases if (c.target_amount - c.collected_amount) > 1.0]
    rng.shuffle(payable)
    payers = payable[: max(1, int(len(payable) * PAYER_RATE))]
    if not payers:
        return {}
    caps = {c.id: round(c.target_amount - c.collected_amount, 2) for c in payers}

    # Provisional split: proportional to what is owed, with spread, so the book
    # does not read as every borrower paying the same fraction on the same day.
    prov = {cid: caps[cid] * rng.uniform(0.30, 1.0) for cid in caps}
    scale = need / max(1e-9, sum(prov.values()))
    plan = {cid: min(caps[cid], round(v * scale, 2)) for cid, v in prov.items()}

    # Clamping loses money; hand the shortfall back to cases with room, repeatedly.
    for _ in range(60):
        short = round(need - sum(plan.values()), 2)
        if abs(short) < 1.0:
            break
        if short > 0:
            room = [cid for cid in plan if caps[cid] - plan[cid] > 1.0]
            if not room:
                break
            total_room = sum(caps[cid] - plan[cid] for cid in room)
            for cid in room:
                add = short * ((caps[cid] - plan[cid]) / total_room)
                plan[cid] = min(caps[cid], round(plan[cid] + add, 2))
        else:
            for cid in sorted(plan, key=lambda k: -plan[k]):
                take = min(plan[cid] - 1.0, -short)
                if take <= 0:
                    continue
                plan[cid] = round(plan[cid] - take, 2)
                short += take
                if short >= -1.0:
                    break
            break

    # Absorb the last rupees of rounding into one case that can take them.
    delta = round(need - sum(plan.values()), 2)
    if abs(delta) >= 0.01:
        for cid in sorted(plan, key=lambda k: -(caps[k] - plan[k])):
            if 0 < plan[cid] + delta <= caps[cid]:
                plan[cid] = round(plan[cid] + delta, 2)
                break
    return {cid: v for cid, v in plan.items() if v >= 1.0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--manager", default="manager1@tiqcollect.in")
    args = ap.parse_args()

    db = SessionLocal()
    today = date.today()
    # IST, explicitly — the 9:00-17:00 window below is meant in Indian time.
    day_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=IST)
    rng = random.Random(SEED)

    mgr = db.query(User).filter(
        User.email == args.manager,
        User.role.in_([UserRole.AGENCY_MANAGER, UserRole.AGENCY_ADMIN]),
    ).one()
    agents = db.query(Agent).filter(Agent.manager_user_id == mgr.id).all()
    agent_by_id = {a.id: a for a in agents}
    aids = list(agent_by_id)

    cases, agent_of = _beat_cases(db, aids, today)
    if not cases:
        print("No beats for today -- nothing to do.")
        return

    target_total = sum(c.target_amount for c in cases)
    collected_all = sum(c.collected_amount for c in cases)
    # Two different populations, deliberately, because the dashboard gauge uses
    # both: its NUMERATOR is every payment the team took today (including cases
    # outside today's beats), its DENOMINATOR is what today's beat cases owed at
    # open. Computing the shortfall against the wrong one lands the gauge short —
    # it read 77% on the first run because Rs 3.35L of today's takings were on
    # non-beat cases and had been netted off the beat remaining target.
    paid_today = float(db.query(func.coalesce(func.sum(Payment.amount), 0.0))
                       .filter(Payment.agent_id.in_(aids),
                               Payment.payment_date >= day_start).scalar())
    paid_today_on_beat = float(db.query(func.coalesce(func.sum(Payment.amount), 0.0))
                               .filter(Payment.case_id.in_([c.id for c in cases]),
                                       Payment.payment_date >= day_start).scalar())
    owed_at_open = target_total - (collected_all - paid_today_on_beat)
    want_today = owed_at_open * TARGET_SHARE
    need = round(want_today - paid_today, 2)

    print(f"cases in today's beats : {len(cases)}")
    print(f"target (lifetime)      : Rs {target_total:>14,.0f}")
    print(f"owed at start of today : Rs {owed_at_open:>14,.0f}")
    print(f"already paid today     : Rs {paid_today:>14,.0f}")
    print(f"{int(TARGET_SHARE * 100)}% of owed           : Rs {want_today:>14,.0f}")
    print(f"still to write         : Rs {need:>14,.0f}\n")

    if need <= 0:
        print("Already at or above target -- nothing to write.")
        return

    if args.snapshot:
        ROLLBACK_DIR.mkdir(parents=True, exist_ok=True)
        with open(ROLLBACK_DIR / "01-cases-before.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["case_id", "case_number", "status", "collected_amount",
                        "target_amount", "visit_count", "resolved_at"])
            for c in cases:
                w.writerow([c.id, c.case_number, c.status.value, c.collected_amount,
                            c.target_amount, c.visit_count,
                            c.resolved_at.isoformat() if c.resolved_at else ""])
        with open(ROLLBACK_DIR / "02-restore-cases.sql", "w", encoding="utf-8") as f:
            f.write("-- Restores today's beat cases to their state before\n"
                    "-- scripts/demo_collect_to_target.py ran on 2026-09-02.\n"
                    "-- Run 03-delete-inserted.sql FIRST, then this.\n")
            for c in cases:
                rs = f"'{c.resolved_at.isoformat()}'" if c.resolved_at else "NULL"
                f.write(f"UPDATE cases SET collected_amount={c.collected_amount}, "
                        f"status='{c.status.value}', visit_count={c.visit_count}, "
                        f"resolved_at={rs} WHERE id='{c.id}';\n")
        with open(ROLLBACK_DIR / "00-agents-before.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["agent_id", "current_month_visits", "current_month_collections"])
            for a in agents:
                w.writerow([a.id, a.current_month_visits, a.current_month_collections])
        print(f"snapshot written -> {ROLLBACK_DIR}\n")

    plan = _money_plan(cases, need, rng)
    case_by_id = {c.id: c for c in cases}
    print(f"payments to write      : {len(plan)}")
    print(f"sum of planned amounts : Rs {sum(plan.values()):,.2f}")

    if args.dry_run or not args.apply:
        print("\n(dry run -- nothing written; pass --apply to commit)")
        return

    # Receipt numbers are unique, and this table holds at least three historical
    # formats: RCP00000136, RCP20261170494 and TIQ-2026-1170493B. func.max() over
    # a String column sorts LEXICOGRAPHICALLY, so it returned the TIQ- row, and
    # the numeric base parsed out of it landed inside a range already in use --
    # the first attempt died on a duplicate key partway through the insert. Take
    # the true NUMERIC maximum, and check every candidate against what is there.
    existing_receipts = {r[0] for r in db.query(Payment.receipt_number).all()}
    n0 = max((int(re.sub(r"\D", "", r) or 0) for r in existing_receipts), default=0)

    def _next_receipt() -> str:
        nonlocal n0
        while True:
            n0 += 1
            candidate = f"RCP{n0}"
            if candidate not in existing_receipts:
                existing_receipts.add(candidate)
                return candidate

    issued: list[str] = []

    visited_today = {
        v.case_id: v for v in db.query(Visit)
        .filter(Visit.agent_id.in_(aids), Visit.check_in_time >= day_start).all()
    }
    new_visits = new_payments = 0
    inserted_visits: list[str] = []

    for cid, amount in sorted(plan.items()):
        case = case_by_id[cid]
        aid = agent_of[cid]
        agent = agent_by_id.get(aid)
        cust = case.customer
        when = day_start + timedelta(hours=9, minutes=rng.randint(0, 8 * 60))

        visit = visited_today.get(cid)
        if visit is None:
            lat = (cust.latitude if cust and cust.latitude else 28.6139) + rng.uniform(-0.00025, 0.00025)
            lon = (cust.longitude if cust and cust.longitude else 77.2090) + rng.uniform(-0.00025, 0.00025)
            full = amount >= round(case.target_amount - case.collected_amount, 2) - 0.01
            visit = Visit(
                id=_uid(), case_id=cid, agent_id=aid,
                check_in_latitude=lat, check_in_longitude=lon,
                check_in_time=when,
                check_out_time=when + timedelta(minutes=rng.randint(9, 26)),
                distance_from_customer_metres=rng.uniform(4.0, 38.0),
                geo_verified=True, within_contact_hours=is_within_contact_hours(when),
                customer_met=True, person_met=PersonMet.BORROWER,
                outcome=VisitOutcome.PAID_FULL if full else VisitOutcome.PART_PAID,
                visit_number=(case.visit_count or 0) + 1,
            )
            db.add(visit)
            db.flush()
            case.visit_count = (case.visit_count or 0) + 1
            if agent:
                agent.current_month_visits = (agent.current_month_visits or 0) + 1
            new_visits += 1
            inserted_visits.append(visit.id)

        receipt = _next_receipt()
        issued.append(receipt)
        db.add(Payment(
            id=_uid(), case_id=cid, visit_id=visit.id, agent_id=aid,
            amount=amount, mode=rng.choice(MODES),
            status=PaymentStatus.VERIFIED, verified_at=when,
            receipt_number=receipt,
            payment_date=when, receipt_sms_sent=True,
        ))
        new_payments += 1

        case.collected_amount = round(case.collected_amount + amount, 2)
        if case.collected_amount >= case.target_amount - 0.01:
            case.status = CaseStatus.PAID
            case.resolved_at = when
        else:
            case.status = CaseStatus.PARTIALLY_PAID
        if agent:
            agent.current_month_collections = round(
                (agent.current_month_collections or 0.0) + amount, 2)

    db.commit()

    if args.snapshot:
        with open(ROLLBACK_DIR / "03-delete-inserted.sql", "w", encoding="utf-8") as f:
            f.write("-- Removes the rows demo_collect_to_target.py inserted on 2026-09-02.\n"
                    "-- Payments first: visits are their foreign key.\n")
            ids = ", ".join(f"'{r}'" for r in issued)
            f.write(f"DELETE FROM payments WHERE receipt_number IN ({ids});\n")
            for rid in inserted_visits:
                f.write(f"DELETE FROM visits WHERE id='{rid}';\n")
        with open(ROLLBACK_DIR / "MANIFEST.txt", "w", encoding="utf-8") as f:
            f.write(
                "2026-09-02 demo collection top-up\n"
                "=================================\n"
                f"manager        : {args.manager}\n"
                f"cases in beats : {len(cases)}\n"
                f"payments added : {new_payments}  (Rs {sum(plan.values()):,.2f})\n"
                f"visits added   : {new_visits}\n"
                f"receipts       : {issued[0] if issued else '-'} .. {issued[-1] if issued else '-'}\n\n"
                "To undo, in this order:\n"
                "  psql < 03-delete-inserted.sql\n"
                "  psql < 02-restore-cases.sql\n"
                "  then restore agent counters from 00-agents-before.csv\n"
            )

    fresh = float(db.query(func.coalesce(func.sum(Payment.amount), 0.0))
                  .filter(Payment.agent_id.in_(aids),
                          Payment.payment_date >= day_start).scalar())
    print(f"\nwrote {new_payments} payments, {new_visits} visits")
    print(f"collected today now    : Rs {fresh:,.0f}")
    print(f"share of what was owed : {fresh / owed_at_open * 100:.1f}%")
    db.close()


if __name__ == "__main__":
    main()
