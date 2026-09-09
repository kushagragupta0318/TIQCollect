"""Reconcile what a beat was PLANNED to be against what it actually was.

WHY THIS EXISTS. `Beat` recorded `estimated_distance_km` and
`estimated_duration_minutes` and had nothing to compare them against. Two
consequences, both large:

  * no routing change could ever be shown to have improved anything — the only
    evidence available was the estimate produced by the change itself;
  * a travel-time model had no label. The ML plan's M1 needs realised leg times,
    and realised leg times are exactly what nobody was recording.

WHAT IT RECONSTRUCTS FROM. Two append-only sources, deliberately:

  AgentLocation   GPS fixes, `recorded_at` is the device clock at capture. This
                  is the actual path walked and driven.
  Visit           check_in_time / check_out_time, which bound the working day
                  and give per-stop service time.

Neither is overwritten in place, so a past day can be reconstructed honestly —
unlike `Loan.dpd` and friends, which cannot (see models/repayment_snapshot.py).

WHAT IT DOES NOT DO. It does not snap the trail to roads, so `actual_distance_km`
is the polyline length through the recorded fixes: an UNDER-estimate when fixes
are sparse and a slight over-estimate when GPS jitters. It is written as measured
rather than corrected, because a reconciliation that quietly adjusts its own
input is not a measurement.
"""
from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()

# Fixes further apart than this are treated as a gap, not a leg: an agent out of
# signal for an hour did not teleport, and joining the two ends of the gap with a
# straight line would invent kilometres they never drove.
MAX_FIX_GAP_SECONDS = 15 * 60

# Two fixes closer than this apart are GPS jitter while stationary. Summing them
# inflates the distance of a day spent mostly standing at doors.
MIN_FIX_MOVE_METRES = 25.0


@celery_app.task(name="app.workers.tasks.beat_reconciliation.reconcile_beats", bind=True)
def reconcile_beats(self, days_back: int = 1):
    """Fill actual_distance_km / actual_duration_minutes for completed beats."""
    from datetime import date, datetime, time, timedelta, timezone

    from app.core.database import SessionLocal
    from app.core.routing import _haversine_pair
    from app.models.agent_location import AgentLocation
    from app.models.beat import Beat, BeatStatus
    from app.models.visit import Visit

    target = date.today() - timedelta(days=days_back)
    db = SessionLocal()
    reconciled = skipped = 0
    try:
        beats = (
            db.query(Beat)
            .filter(Beat.beat_date == target,
                    Beat.status != BeatStatus.PLANNED,
                    Beat.is_leave_day.is_(False))
            .all()
        )
        for beat in beats:
            day_start = datetime.combine(target, time.min, tzinfo=timezone.utc)
            day_end = day_start + timedelta(days=1)

            fixes = (
                db.query(AgentLocation)
                .filter(AgentLocation.agent_id == beat.agent_id,
                        AgentLocation.recorded_at >= day_start,
                        AgentLocation.recorded_at < day_end)
                .order_by(AgentLocation.recorded_at.asc())
                .all()
            )

            metres = 0.0
            gaps = 0
            for prev, cur in zip(fixes, fixes[1:]):
                gap_s = (cur.recorded_at - prev.recorded_at).total_seconds()
                if gap_s > MAX_FIX_GAP_SECONDS:
                    gaps += 1
                    continue
                _, m = _haversine_pair(prev.latitude, prev.longitude,
                                       cur.latitude, cur.longitude)
                if m >= MIN_FIX_MOVE_METRES:
                    metres += m

            visits = (
                db.query(Visit)
                .filter(Visit.agent_id == beat.agent_id,
                        Visit.check_in_time >= day_start,
                        Visit.check_in_time < day_end)
                .order_by(Visit.check_in_time.asc())
                .all()
            )

            # The working day runs from the first check-in to the last check-out
            # (falling back to the last check-in where a visit was never closed).
            duration_min = None
            if visits:
                first = visits[0].check_in_time
                last = max(
                    [v.check_out_time for v in visits if v.check_out_time]
                    or [v.check_in_time for v in visits])
                duration_min = int(max(0, (last - first).total_seconds()) // 60)

            # A beat with neither a trail nor a visit is not a zero-kilometre
            # day, it is a day with no evidence. Leaving the columns NULL keeps
            # "not measured" distinguishable from "measured as nothing" — the
            # same reason actual_outcome is nullable on model_predictions.
            if not fixes and duration_min is None:
                skipped += 1
                continue

            if fixes:
                beat.actual_distance_km = round(metres / 1000.0, 2)
            if duration_min is not None:
                beat.actual_duration_minutes = duration_min
            reconciled += 1

            logger.info(
                "beat.reconciled", beat=beat.beat_number, agent_id=beat.agent_id,
                planned_km=beat.estimated_distance_km,
                actual_km=beat.actual_distance_km,
                planned_min=beat.estimated_duration_minutes,
                actual_min=beat.actual_duration_minutes,
                route_source=beat.route_source, fixes=len(fixes), trail_gaps=gaps,
            )

        db.commit()
        logger.info("beat_reconciliation.done", date=str(target),
                    beats=len(beats), reconciled=reconciled, skipped=skipped)
        return {"date": str(target), "beats": len(beats),
                "reconciled": reconciled, "skipped_no_evidence": skipped}
    except Exception as exc:
        db.rollback()
        # Reported rather than swallowed. Two scheduled tasks in this repo were
        # once found reporting work they never did (fixed 2026-09-06); a
        # reconciliation that fails silently would leave the plan-versus-actual
        # record permanently and invisibly empty.
        logger.error("beat_reconciliation.failed", date=str(target), error=str(exc))
        raise
    finally:
        db.close()
