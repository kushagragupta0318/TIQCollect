"""Fill the demo book's gap between its last real activity and today.

THE PROBLEM. A demo book is driven forward by hand, so the moment nobody runs
the daily tools the actuals stop. On 2026-10-07 the last payments and visits
were 2026-09-22: the bank Overview showed a fortnight-old picture, the manager
and agent "today" views were empty, Usage was thin, and the Cash Forecast
anchored on stale weeks — all while the data looked superficially fine.

WHAT THIS DOES. It DETECTS the gap at run time and fills each missing business
day with that day's activity, by calling the tools that already exist rather
than writing a fourth generator:

  1. app.workers.tasks.demo_daily_feed._seed_day(db, day)
        the bank's fresh pool cases for that day. Already idempotent, keyed on
        a per-(bank, day) feed batch.
  2. app.workers.tasks.allocation.run_nightly_allocation(plan_date_str=day)
        the real planner: pool -> gates -> solve -> PLANNED beats for that day.
        The same task the 20:00 cron runs, so the routes are routed the way
        production routes them.
  3. scripts.demo_record_visits.record_day(db, day, ...)
        the day's visits, VERIFIED payments and PTPs, from the weighted
        outcome mix. Extracted from that script's main() for this (2026-10-07)
        so there is ONE definition of how a day's field activity is written.
        Every control it respected still holds: check-in coordinates at the
        customer's address, geo_verified, contact hours from the IST check-in
        time, unique receipts, and the collected <= target invariant checked
        before the commit.
  4. the day's beats closed to COMPLETED, with cases_completed and
        amount_collected read back from the rows step 3 actually wrote —
        matching seed_data's own convention that a past working day is
        COMPLETED, not left PLANNED.

"TODAY" IS IST. Every figure the bank surfaces are computed in the book's own
business day (analytics.business_date over the bank's timezone), so a UTC
date.today() is the wrong day for the first five and a half hours of it.

IDEMPOTENT. A day that already has actuals is skipped, so this is safe to run
daily and safe to re-run after a partial failure.

NOTHING GOES OUT. It writes rows; it sends no SMS and no WhatsApp, and it
never prints an OTP. The borrowers are the book's own invented people.

    python -m scripts.demo_catchup                       # dry run: show the gap
    python -m scripts.demo_catchup --apply               # fill it to today
    python -m scripts.demo_catchup --apply --until 2026-10-05
    python -m scripts.demo_catchup --apply --from 2026-09-23

AFTER FILLING, refresh what the surfaces read:
    python -m scripts.daily_refresh            # scoring -> allocation -> MV refresh
`--refresh` does it in-process at the end instead.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import date, datetime, time, timedelta

#: A day with no beats and no visits is not a working day for this book. The
#: generated agencies work six days; Sunday is left empty on purpose, so
#: filling it would invent activity the book never had.
WEEKEND = {6}          # Python's Monday=0 .. Sunday=6


def _ist_today() -> date:
    from app.core.geo import IST
    return datetime.now(IST).date()


def _is_working_day(day: date) -> bool:
    return day.weekday() not in WEEKEND


def last_actual_day(db) -> date | None:
    """The most recent day this book has REAL activity for.

    Takes the max over the three things a worked day leaves behind — a
    verified payment, a visit, and a beat that is no longer merely PLANNED —
    because any one of them alone can be stale for its own reason (a day of
    doors that opened and paid nothing still has visits).
    """
    from sqlalchemy import func

    from app.core.geo import IST
    from app.models.beat import Beat, BeatStatus
    from app.models.payment import Payment
    from app.models.visit import Visit

    days: list[date] = []
    pay = db.query(func.max(Payment.payment_date)).scalar()
    if pay is not None:
        days.append(pay.astimezone(IST).date() if pay.tzinfo else pay.date())
    vis = db.query(func.max(Visit.check_in_time)).scalar()
    if vis is not None:
        days.append(vis.astimezone(IST).date() if vis.tzinfo else vis.date())
    beat = (db.query(func.max(Beat.beat_date))
            .filter(Beat.status != BeatStatus.PLANNED).scalar())
    if beat is not None:
        days.append(beat)
    return max(days) if days else None


def day_has_actuals(db, day: date) -> bool:
    """Idempotency: has this day already been filled (or genuinely worked)?

    Keyed on a VISIT in the day's IST window rather than on a beat: step 2
    creates beats before step 3 writes anything, so a run that died in between
    would otherwise look complete and leave a day with routes and no activity.
    """
    from app.core.geo import IST
    from app.models.visit import Visit

    start = datetime.combine(day, time.min, tzinfo=IST)
    return db.query(Visit.id).filter(Visit.check_in_time >= start,
                                     Visit.check_in_time < start + timedelta(days=1)).first() is not None


def _beat_manifest_path(day: date) -> pathlib.Path:
    """Where this script records the beat states it overwrote.

    demo_record_visits' own manifest covers the visits, payments and PTPs it
    writes, and its --undo restores them -- but NOT the beat rows this script
    closes afterwards. The first run here proved why that matters: after
    undoing a day's visits, the beats were still COMPLETED, so last_actual_day
    still reported that day as worked and the catch-up refused to refill it.
    A partial undo that leaves the detector lying is worse than no undo.
    """
    d = pathlib.Path(__file__).resolve().parents[1] / "docs" / "rollback"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{day.isoformat()}-demo-catchup-beats.json"


def undo_beats(day: date) -> int:
    """Put the day's beats back the way they were before _close_beats."""
    from app.core.database import SessionLocal
    from app.models.beat import Beat, BeatStatus

    path = _beat_manifest_path(day)
    if not path.exists():
        print(f"no beat manifest for {day} — nothing to restore")
        return 0
    rows = json.loads(path.read_text(encoding="utf-8"))
    db = SessionLocal()
    try:
        n = 0
        for row in rows:
            beat = db.get(Beat, row["id"])
            if beat is None:
                continue
            beat.status = BeatStatus(row["status"])
            beat.cases_completed = row["cases_completed"]
            beat.amount_collected = row["amount_collected"]
            n += 1
        db.commit()
    finally:
        db.close()
    path.unlink()
    print(f"restored {n} beats for {day}")
    return n


def _close_beats(db, day: date) -> tuple[int, float]:
    """Close the day's beats, with figures read back from what was written.

    seed_data's own convention: a working day in the past is COMPLETED, not
    left PLANNED. The counts are NOT invented here — cases_completed is the
    distinct cases actually visited on that beat, and amount_collected the sum
    of that day's VERIFIED payments for those cases, so the beat agrees with
    the rows underneath it.
    """
    from sqlalchemy import func

    from app.core.geo import IST
    from app.models.beat import Beat, BeatStatus
    from app.models.payment import Payment
    from app.models.visit import Visit

    start = datetime.combine(day, time.min, tzinfo=IST)
    end = start + timedelta(days=1)
    closed, collected_total = 0, 0.0
    before: list[dict] = []
    for beat in db.query(Beat).filter(Beat.beat_date == day).all():
        case_ids = list(beat.ordered_case_ids or [])
        if not case_ids:
            continue
        visited = {v[0] for v in db.query(Visit.case_id)
                   .filter(Visit.case_id.in_(case_ids), Visit.agent_id == beat.agent_id,
                           Visit.check_in_time >= start, Visit.check_in_time < end).all()}
        if not visited:
            continue
        money = (db.query(func.coalesce(func.sum(Payment.amount), 0.0))
                 .filter(Payment.case_id.in_(case_ids), Payment.agent_id == beat.agent_id,
                         Payment.payment_date >= start, Payment.payment_date < end,
                         Payment.status == "VERIFIED").scalar() or 0.0)
        before.append({"id": beat.id,
                       "status": beat.status.value if hasattr(beat.status, "value") else str(beat.status),
                       "cases_completed": beat.cases_completed or 0,
                       "amount_collected": float(beat.amount_collected or 0.0)})
        beat.status = BeatStatus.COMPLETED
        beat.cases_completed = len(visited)
        beat.amount_collected = round(float(money), 2)
        closed += 1
        collected_total += float(money)
    if before:
        _beat_manifest_path(day).write_text(json.dumps(before, indent=2), encoding="utf-8")
    return closed, round(collected_total, 2)


#: How many cases an agent works, drawn per day. The book this fills does NOT
#: have uniform days: 2026-09-15..22 ran 82, 85, 197, 191, 85, 82, 179, 92
#: verified payments — light days around 85 and heavy days around 190, roughly
#: alternating. A fixed per-agent count produced ~200 every day, and thirteen
#: of those in a row would draw a flat line through the daily trend on every
#: surface that plots it: not a spike, but just as obviously generated.
#: Drawn from the day's own seed, so a re-run reproduces the same day.
_INTENSITY = (1, 1, 2, 2, 3, 3)


def _per_agent_for(day: date, seed: int, override: int | None) -> int:
    import random
    if override is not None:
        return override
    return random.Random(seed + day.toordinal()).choice(_INTENSITY)


def fill_day(day: date, *, per_agent: int | None, seed: int) -> dict:
    """One missing day, through the existing tools. Returns a per-day summary.

    EACH STEP GETS ITS OWN SESSION, opened and closed around it. The first
    version passed one session through all four and the planner failed on every
    day with "This Session's transaction has been rolled back due to a previous
    exception during flush" — the allocation task opens its own session and runs
    the real solver, and holding a second transaction open across it is a way to
    contend with itself. Run standalone the same task planned 25 runs for the
    same past date without complaint, which is what pointed at the session
    rather than the planner.

    This also mirrors how production actually does it: the 20:00 allocation is
    its own task in its own transaction, not a step inside somebody else's.
    """
    from app.core.database import SessionLocal
    from app.workers.tasks import allocation, demo_daily_feed

    from scripts.demo_record_visits import record_day

    todays_per_agent = _per_agent_for(day, seed, per_agent)
    out: dict = {"day": day.isoformat(), "fed": 0, "beats_closed": 0, "per_agent": todays_per_agent,
                 "visits": 0, "payments": 0, "ptps": 0, "collected": 0.0}

    # 1. the bank's fresh cases for that day (its own idempotency guard).
    db = SessionLocal()
    try:
        out["fed"] = demo_daily_feed._seed_day(db, day) or 0   # noqa: SLF001 — the logic to reuse
        db.commit()
    finally:
        db.close()

    # 2. the real planner, for that plan date, in its own transaction — the
    #    same task the 20:00 cron runs, so the routes are routed as production
    #    routes them.
    try:
        allocation.run_nightly_allocation.apply(
            kwargs={"strategy": "SMART", "plan_date_str": day.isoformat()}).get(propagate=True)
    except Exception as exc:  # noqa: BLE001 — one day's planner failing must not abort the catch-up
        out["planner_error"] = str(exc)[:200]

    # 3. the day's field activity, from the one definition of it.
    db = SessionLocal()
    try:
        rec = record_day(db, day, per_agent=todays_per_agent, seed=seed + day.toordinal(),
                         apply_changes=True)
        out.update({k: rec.get(k, 0) for k in ("visits", "payments", "ptps", "collected")})
    finally:
        db.close()

    # 4. close the routes against what was actually written.
    db = SessionLocal()
    try:
        closed, _ = _close_beats(db, day)
        db.commit()
        out["beats_closed"] = closed
    finally:
        db.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="Write. Default is a dry run that only reports the gap.")
    ap.add_argument("--from", dest="start", default=None,
                    help="First day to fill, ISO. Default: the day after the last real activity.")
    ap.add_argument("--until", dest="until", default=None,
                    help="Last day to fill, ISO. Default: today in IST.")
    ap.add_argument("--per-agent", type=int, default=None,
                    help="Cases visited per agent per day. Default: drawn per day from the book's own "
                         "light/heavy pattern, so the filled days vary the way the real ones do.")
    ap.add_argument("--seed", type=int, default=20261007,
                    help="Base seed; each day is seeded from this plus its ordinal, so a "
                         "re-run reproduces the same day and two days never look identical.")
    ap.add_argument("--undo-beats", dest="undo_beats", default=None,
                    help="Restore the beat states this script overwrote for one day, ISO. "
                         "Pair it with demo_record_visits --undo <manifest>, which reverses the "
                         "visits, payments and PTPs; together they put a day back.")
    ap.add_argument("--refresh", action="store_true",
                    help="Refresh the analytics materialized views in-process when done.")
    args = ap.parse_args(argv)

    if args.undo_beats:
        undo_beats(date.fromisoformat(args.undo_beats))
        return 0

    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        today = date.fromisoformat(args.until) if args.until else _ist_today()
        last = last_actual_day(db)
        if args.start:
            first = date.fromisoformat(args.start)
        elif last is None:
            print("This book has NO activity at all — seed it first "
                  "(scripts/generate_demo_v2.py); catch-up fills a gap, it does not create a book.",
                  file=sys.stderr)
            return 1
        else:
            first = last + timedelta(days=1)

        print(f"last real activity: {last}")
        print(f"filling:            {first} .. {today}  ({'APPLY' if args.apply else 'dry run'})")
        if first > today:
            print("nothing to fill — the book is current.")
            return 0

        wanted = [d for d in (first + timedelta(days=i) for i in range((today - first).days + 1))
                  if _is_working_day(d)]
        skipped = [d for d in wanted if day_has_actuals(db, d)]
        todo = [d for d in wanted if d not in set(skipped)]
        print(f"{len(wanted)} working days in range; {len(skipped)} already have activity; "
              f"{len(todo)} to fill")
        if not args.apply:
            for d in todo:
                print(f"  would fill {d}")
            print("\n(dry run — nothing written; pass --apply)")
            return 0

        rows = []
        for d in todo:
            print(f"\n--- {d} ---", flush=True)
            rows.append(fill_day(d, per_agent=args.per_agent, seed=args.seed))

        print("\nfilled:")
        for r in rows:
            note = f"  planner: {r['planner_error']}" if r.get("planner_error") else ""
            print(f"  {r['day']}  fed {r['fed']:>3}  beats {r['beats_closed']:>3}  "
                  f"x{r['per_agent']}  visits {r['visits']:>4}  payments {r['payments']:>4}  "
                  f"Rs {r['collected']:>12,.0f}{note}")
        print(f"\n{len(rows)} days, {sum(r['visits'] for r in rows)} visits, "
              f"{sum(r['payments'] for r in rows)} payments, "
              f"Rs {sum(r['collected'] for r in rows):,.0f} collected")

        if args.refresh:
            from app.core.database import engine
            from app.workers.tasks import analytics_refresh
            print("\nrefreshing analytics views…", flush=True)
            print(f"  {analytics_refresh.refresh_all(engine)}")
        else:
            print("\nNOW RUN: python -m scripts.daily_refresh   "
                  "(scoring -> allocation -> MV refresh, or the surfaces stay stale)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
