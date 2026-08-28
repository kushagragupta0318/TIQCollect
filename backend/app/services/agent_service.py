# ─── CHANGELOG (prototype → product) ───
# New file, 2026-07-22. Fourth router→service extraction this week
# (after visit_service, payment_service, case_service's completion),
# absorbing home_summary/checkin/get_beat/get_profile/trigger_sos/
# cancel_sos/get_availability_calendar out of agent.py per
# final_changes.md §7.1 item #2. Same "pure extraction, not a rewrite"
# discipline as every prior one this week: no query changed, no
# validation reordered, no response field added or removed. See
# prototype_to_product/21.07.md for the overall sequencing this
# continues, and changelog.md for full detail + verification.
from __future__ import annotations

from datetime import datetime, date, timezone, timedelta
from collections import defaultdict
from math import cos, radians

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.security import create_agent_verify_token
from app.models.agent import Agent, AgentStatus
from app.models.beat import Beat
from app.models.case import Case
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.ptp import PTP, PTPStatus
from app.models.user import User
from app.models.visit import Visit
from app.services.notification_service import NotificationService

# Fixed offsets (as fractions of DEMO_ANCHOR_RADIUS_M) used to scatter the demo
# "anchor" customers a few tens of metres around the agent's live GPS. Each
# magnitude is < 1.0 so every customer stays inside the radius (and inside the
# 100m geo-fence). Deterministic → the cluster is stable between check-ins.
_DEMO_ANCHOR_OFFSETS = [
    (0.20, 0.30),   # ~29% of radius
    (0.55, -0.30),
    (-0.40, 0.50),
    (0.30, 0.62),
    (-0.55, -0.35),
    (0.62, 0.18),
    (-0.22, -0.58),
]

import structlog

logger = structlog.get_logger()


class AgentService:
    """Business logic for agent home/profile/checkin/SOS/availability.
    No HTTP knowledge — callable from a router, a Celery task, or a
    WebSocket handler without duplicating any of this."""

    def __init__(self, db: Session):
        self.db = db

    # -----------------------------------------------------------------
    # GET /agent/home-summary
    # -----------------------------------------------------------------
    def home_summary(self, agent: Agent) -> dict:
        from app.api.v1.endpoints.agent import _effective_day

        eff_day = _effective_day(agent.id, self.db)
        start_of_day = datetime.combine(eff_day, datetime.min.time()).replace(tzinfo=timezone.utc)
        end_of_day = datetime.combine(eff_day, datetime.max.time()).replace(tzinfo=timezone.utc)

        # Use beat as single source of truth for cases/target — same as beat map and my-cases
        beat = (
            self.db.query(Beat)
            .filter(Beat.agent_id == agent.id)
            .order_by(Beat.beat_date.desc())
            .first()
        )
        beat_case_ids = beat.ordered_case_ids if beat else []
        cases_today = len(beat_case_ids)
        total_target_today = (
            self.db.query(func.coalesce(func.sum(Case.target_amount), 0.0))
            .filter(Case.id.in_(beat_case_ids))
            .scalar() or 0.0
        ) if beat_case_ids else 0.0
        visits_today = (
            self.db.query(func.count(Visit.id))
            .filter(Visit.agent_id == agent.id, Visit.check_in_time >= start_of_day, Visit.check_in_time <= end_of_day)
            .scalar() or 0
        )
        amount_today = (
            self.db.query(func.sum(Payment.amount))
            .filter(Payment.agent_id == agent.id, Payment.payment_date >= start_of_day, Payment.payment_date <= end_of_day)
            .scalar() or 0.0
        )
        ptps_due = (
            self.db.query(func.count(PTP.id))
            .filter(PTP.agent_id == agent.id, PTP.committed_date == eff_day, PTP.status == PTPStatus.ACTIVE)
            .scalar() or 0
        )

        return {
            "cases_today": cases_today,
            "total_target_today": total_target_today,
            "visits_done": visits_today,
            "amount_collected_today": amount_today,
            "ptps_due_today": ptps_due,
            "check_in_status": agent.status,
            "beat_status": beat.status if beat else None,
            "sos_active": agent.sos_active,
        }

    # -----------------------------------------------------------------
    # POST /agent/checkin
    # -----------------------------------------------------------------
    def checkin(self, agent: Agent, req) -> dict:
        # A demo always begins with a check-in, which makes this the one moment
        # it is safe to rewind the showcase case: never mid-flow, and never
        # after a visit the audience is still looking at.
        if settings.DEMO_REHEARSAL_MODE:
            self._rewind_demo_case()
        agent.status = AgentStatus.ON_DUTY
        agent.last_known_latitude = req.latitude
        agent.last_known_longitude = req.longitude
        agent.last_location_update = datetime.now(timezone.utc).isoformat()
        if settings.DEMO_MODE:
            self._anchor_demo_customers(req.latitude, req.longitude)
        self.db.commit()
        return {"status": "ON_DUTY", "message": "Check-in successful. Have a safe day!"}

    # -----------------------------------------------------------------
    # POST /agent/checkout
    # -----------------------------------------------------------------
    def checkout(self, agent: Agent) -> dict:
        """End the working day.

        2026-08-20 — added. Check-in has existed since the beginning with no
        counterpart, so an agent went ON_DUTY and stayed there until a manager
        changed it. That also meant location tracking, which is gated on duty
        status, had no way to be stopped by the person being tracked.

        Deliberately does NOT take coordinates. Check-in captures a position
        because it starts the day's record and anchors the geo-fence; ending a
        shift needs no such proof, and asking for GPS to clock off would be one
        more reason to stay on duty.
        """
        agent.status = AgentStatus.OFF_DUTY
        self.db.commit()
        return {"status": "OFF_DUTY", "message": "Checked out. Location tracking has stopped."}

    def _rewind_demo_case(self) -> None:
        """DEMO_REHEARSAL_MODE only: undo the last run-through of the demo case.

        Best-effort by design. A missing baseline or a renamed ref must never
        stop an agent checking in — the worst outcome is a demo that still shows
        yesterday's completed visit, which is what happens today anyway.
        """
        from app.services import demo_service
        try:
            result = demo_service.rewind(self.db, settings.DEMO_CONTACT_REF)
            if not result["clean"]:
                logger.info(
                    "demo.rewound_on_checkin",
                    case_number=result["case_number"],
                    deleted=result["deleted"],
                    fields_restored=len(result["changed"]),
                )
        except demo_service.DemoBaselineMissing:
            logger.warning(
                "demo.no_baseline",
                ref=settings.DEMO_CONTACT_REF,
                hint="take one with: python -m scripts.demo_reset --save",
            )
        except Exception:
            logger.exception("demo rewind on check-in failed; continuing with check-in")
            self.db.rollback()

    def _anchor_demo_customers(self, lat: float, lon: float) -> None:
        """DEMO_MODE only: re-place the configured demo 'anchor' customers a few
        tens of metres around the agent's live GPS (lat/lon) so the geo-fence
        passes wherever on earth the demo is run. Deterministic offsets keep the
        cluster stable. Non-anchor customers are left untouched, so out-of-range
        cases still exist to demo the fence blocking a visit."""
        refs = settings.demo_anchor_refs_list
        if not refs:
            return
        radius = settings.DEMO_ANCHOR_RADIUS_M
        # metres → degrees (longitude scaled by latitude); guard the pole edge case
        m_per_deg_lat = 111_111.0
        m_per_deg_lon = 111_111.0 * max(cos(radians(lat)), 1e-6)
        customers = (
            self.db.query(Customer)
            .filter(Customer.customer_ref.in_(refs))
            .all()
        )
        for c in customers:
            north_m, east_m = _DEMO_ANCHOR_OFFSETS[
                refs.index(c.customer_ref) % len(_DEMO_ANCHOR_OFFSETS)
            ]
            c.latitude = lat + (north_m * radius) / m_per_deg_lat
            c.longitude = lon + (east_m * radius) / m_per_deg_lon

    # -----------------------------------------------------------------
    # GET /agent/beat
    # -----------------------------------------------------------------
    def get_beat(self, agent: Agent) -> dict | None:
        from app.api.v1.endpoints.agent import _effective_day, _visited_today, _format_case

        beat = (
            self.db.query(Beat)
            .filter(Beat.agent_id == agent.id)
            .order_by(Beat.beat_date.desc())
            .first()
        )
        if not beat:
            return None

        # Compute real-time today stats from DB (same window as home-summary)
        eff_day = _effective_day(agent.id, self.db)
        # Scope visited IDs to cases in this beat — prevents off-beat visits from
        # inflating the "done" count on Home while the Cases page fades fewer cards.
        _all_visited = _visited_today(agent.id, eff_day, self.db)
        _beat_case_set = set(beat.ordered_case_ids or [])
        visited_today_ids = list(_all_visited & _beat_case_set)
        _day_start = datetime.combine(eff_day, datetime.min.time()).replace(tzinfo=timezone.utc)
        amount_collected_today = (
            self.db.query(func.coalesce(func.sum(Payment.amount), 0.0))
            .filter(Payment.agent_id == agent.id, Payment.payment_date >= _day_start)
            .scalar() or 0.0
        )
        total_target_today = (
            self.db.query(func.coalesce(func.sum(Case.target_amount), 0.0))
            .filter(Case.id.in_(beat.ordered_case_ids))
            .scalar() or 0.0
        ) if beat.ordered_case_ids else 0.0

        cases_by_id: dict[str, Case] = {}
        if beat.ordered_case_ids:
            cases = (
                self.db.query(Case)
                .options(joinedload(Case.customer), joinedload(Case.loan))
                .filter(Case.id.in_(beat.ordered_case_ids))
                .all()
            )
            cases_by_id = {c.id: c for c in cases}

        # Case IDs that have an active PTP committed for today specifically
        ptp_due_today_ids: set[str] = set(
            row[0] for row in
            self.db.query(PTP.case_id)
            .filter(PTP.agent_id == agent.id, PTP.committed_date == eff_day, PTP.status == PTPStatus.ACTIVE)
            .all()
        )

        # Visit priority for every case on the beat.
        #
        # THIS is the payload the Cases page renders from — AgentCasesPage reads
        # BeatContext (GET /agent/beat), not GET /agent/cases. Scoring only in
        # CaseService.list_cases left the score on an endpoint the screen never
        # calls: the chip and the reason were coded, shipped, and invisible.
        #
        # Loans are joinedload-ed above, so this costs two queries for the beat.
        from app.services.visit_priority_service import score_cases
        scored = score_cases(self.db, list(cases_by_id.values()), today=eff_day,
                             loans={c.loan_id: c.loan for c in cases_by_id.values()
                                    if c.loan_id and c.loan})

        ordered_cases = []
        for cid in beat.ordered_case_ids:
            if cid in cases_by_id:
                case_dict = _format_case(cases_by_id[cid])
                case_dict["ptp_due_today"] = cid in ptp_due_today_ids
                case_dict["visit_priority"] = scored.get(cid)
                ordered_cases.append(case_dict)

        ptps_due_today = len(ptp_due_today_ids)

        return {
            "id": beat.id,
            "beat_date": beat.beat_date.isoformat(),
            "beat_number": beat.beat_number,
            "ordered_case_ids": beat.ordered_case_ids,
            "total_cases": len(ordered_cases),
            "estimated_distance_km": beat.estimated_distance_km,
            "estimated_duration_minutes": beat.estimated_duration_minutes,
            "total_target_amount": round(float(total_target_today), 2),
            "status": beat.status,
            # Real-time today stats — single source of truth for all agent views
            "cases_visited_today": len(visited_today_ids),
            "visited_today_ids": visited_today_ids,
            "amount_collected_today": round(float(amount_collected_today), 2),
            "cases": ordered_cases,
            # Fields formerly only in /home-summary — now consolidated here
            "ptps_due_today": ptps_due_today,
            "check_in_status": agent.status,
            "sos_active": agent.sos_active,
        }

    # -----------------------------------------------------------------
    # GET /agent/profile
    # -----------------------------------------------------------------
    def get_profile(self, current_user) -> dict:
        agent = (
            self.db.query(Agent)
            .options(joinedload(Agent.user))
            .filter(Agent.user_id == current_user.id)
            .first()
        )
        if not agent:
            raise HTTPException(status_code=404, detail="Agent profile not found")

        beat = self.db.query(Beat).filter(Beat.agent_id == agent.id).order_by(Beat.beat_date.desc()).first()
        cases_today = len(beat.ordered_case_ids) if beat and beat.ordered_case_ids else 0

        return {
            "id": agent.id,
            "user_id": agent.user_id,
            "employee_code": agent.employee_code,
            "id_card_number": agent.id_card_number,
            "full_name": current_user.full_name,
            "email": current_user.email,
            "phone": current_user.phone,
            "date_of_birth": current_user.date_of_birth,
            "territory": agent.territory,
            "tier": agent.tier,
            "status": agent.status,
            "specialization": agent.specialization,
            "languages_spoken": agent.languages_spoken,
            "ranking_score": agent.ranking_score,
            "lifetime_collection_rate": agent.lifetime_collection_rate,
            "max_cases_per_day": agent.max_cases_per_day,
            "current_month_visits": agent.current_month_visits,
            "current_month_collections": agent.current_month_collections,
            "current_month_ptps_set": agent.current_month_ptps_set,
            "current_month_ptps_honored": agent.current_month_ptps_honored,
            "last_known_latitude": agent.last_known_latitude,
            "last_known_longitude": agent.last_known_longitude,
            "sos_active": agent.sos_active,
            "cases_today": cases_today,
        }

    # -----------------------------------------------------------------
    # POST /agent/sos
    # -----------------------------------------------------------------
    def trigger_sos(self, agent: Agent, req) -> dict:
        """Raise an SOS for this agent and alert their manager.

        2026-08-18 — reworked. The client no longer guarantees coordinates:
        SOSButton.tsx previously substituted hardcoded Gurugram coordinates
        whenever the browser did not answer within 2.5s, so an alert could
        carry a confidently wrong position and send help to the wrong place.
        Resolution order is now live fix -> last tracked fix -> none, and the
        quality is reported to both the agent and the manager rather than
        being flattened into a plain map link.
        """
        from app.models.agent_location import AgentLocation, LocationSource

        now_utc = datetime.now(timezone.utc)
        agent.sos_active = True
        agent.sos_triggered_at = now_utc.isoformat()

        lat, lon = req.latitude, req.longitude
        quality = "NONE"
        age_seconds: int | None = None

        if lat is not None and lon is not None:
            quality = "LIVE"
            age_seconds = 0
            agent.last_known_latitude = lat
            agent.last_known_longitude = lon
            agent.last_location_update = now_utc.isoformat()
            # Pin the SOS itself into the trail so an incident replay has a
            # marked origin, independent of the sos_active flag that
            # cancel_sos() later clears.
            self.db.add(AgentLocation(
                agent_id=agent.id, latitude=lat, longitude=lon,
                accuracy_metres=getattr(req, "accuracy_metres", None),
                recorded_at=now_utc, source=LocationSource.SOS, is_sos=True,
                battery_pct=getattr(req, "battery_pct", None),
            ))
        else:
            # No live fix. Fall back to the most recent tracked position and
            # say how old it is — a stale location that is labelled stale is
            # useful; one presented as current is dangerous.
            last = (
                self.db.query(AgentLocation)
                .filter(AgentLocation.agent_id == agent.id)
                .order_by(AgentLocation.recorded_at.desc())
                .first()
            )
            if last:
                lat, lon = last.latitude, last.longitude
                recorded = last.recorded_at
                if recorded.tzinfo is None:
                    recorded = recorded.replace(tzinfo=timezone.utc)
                age_seconds = int((now_utc - recorded).total_seconds())
                quality = "LAST_KNOWN"

        self.db.commit()

        # SMS/WhatsApp the assigned manager immediately — the dashboard's red SOS
        # banner is poll-based (up to 30s, and only while the tab is open), so a
        # manager away from the screen would otherwise never find out.
        manager = self.db.query(User).filter(User.id == agent.manager_user_id).first() if agent.manager_user_id else None
        notified = False
        if manager and manager.phone:
            alert_time = now_utc.strftime("%d %b %Y, %I:%M %p")
            if quality == "NONE":
                where_sms = "Location UNAVAILABLE - agent's GPS did not respond."
                where_wa = "📍 Location *UNAVAILABLE* - the agent's GPS did not respond."
            else:
                maps_link = f"https://www.google.com/maps?q={lat},{lon}"
                if quality == "LIVE":
                    where_sms = f"Location: {maps_link}"
                    where_wa = f"📍 Location: {maps_link}"
                else:
                    mins = max(1, round((age_seconds or 0) / 60))
                    where_sms = f"LAST KNOWN location ({mins} min old): {maps_link}"
                    where_wa = f"📍 *Last known* location ({mins} min old): {maps_link}"
            sms_body = (
                f"SOS ALERT: Field agent {agent.user.full_name} ({agent.employee_code}) "
                f"triggered an emergency SOS at {alert_time} UTC. {where_sms} - ABC Bank"
            )
            wa_body = (
                f"\U0001f6a8 *SOS ALERT*\n\n"
                f"Agent *{agent.user.full_name}* ({agent.employee_code}) triggered an emergency SOS.\n"
                f"\U0001f550 {alert_time} UTC\n"
                f"{where_wa}\n\n"
                f"Please respond immediately."
            )
            NotificationService.send_twilio(e164 := "+" + NotificationService.normalize_phone(manager.phone), sms_body, wa_body)
            notified = True
            logger.info("sos.manager_alerted", agent_id=agent.id, quality=quality,
                        age_seconds=age_seconds, to=e164[-4:])
        else:
            logger.warning("sos.no_manager_notified", agent_id=agent.id, manager_user_id=agent.manager_user_id)

        if quality == "NONE":
            message = "SOS triggered. Your manager has been alerted — but your location could NOT be sent."
        elif quality == "LAST_KNOWN":
            message = "SOS triggered. Your manager has been alerted with your last known location."
        else:
            message = "SOS triggered. Your manager has been alerted with your location."

        return {
            "sos_triggered": True,
            "triggered_at": now_utc.isoformat(),
            "message": message,
            "location_quality": quality,
            "location_age_seconds": age_seconds,
            "manager_notified": notified,
        }

    # -----------------------------------------------------------------
    # POST /agent/sos/cancel
    # -----------------------------------------------------------------
    def cancel_sos(self, agent: Agent) -> dict:
        agent.sos_active = False
        agent.sos_triggered_at = None
        self.db.commit()
        return {"sos_cancelled": True, "message": "SOS deactivated. Stay safe."}

    # -----------------------------------------------------------------
    # GET /agent/availability/calendar
    # -----------------------------------------------------------------
    def availability_calendar(self, current_user) -> dict:
        """Return a 6-month day-by-day duty calendar derived from Beat records.
        Days with a beat = ON_DUTY; days without = OFF_DUTY (effectively off/leave).
        Sundays are excluded (RBI mandate: no field collection on Sundays).
        """
        agent = (
            self.db.query(Agent).filter(Agent.user_id == current_user.id).first()
        )
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        today = date.today()
        six_months_ago = today - timedelta(days=180)

        beats = (
            self.db.query(Beat.beat_date, Beat.status, Beat.total_cases)
            .filter(Beat.agent_id == agent.id, Beat.beat_date >= six_months_ago)
            .all()
        )
        beat_map: dict[date, dict] = {
            b.beat_date: {
                "beat_status": b.status.value if hasattr(b.status, "value") else str(b.status),
                "cases": b.total_cases or 0,
            }
            for b in beats
        }

        calendar_days: list[dict] = []
        d = six_months_ago
        while d <= today:
            if d.weekday() != 6:  # exclude Sundays
                info = beat_map.get(d)
                calendar_days.append(
                    {
                        "date": d.isoformat(),
                        "day_of_week": d.strftime("%a"),
                        "status": "ON_DUTY" if info else "OFF_DUTY",
                        "beat_status": info["beat_status"] if info else None,
                        "cases": info["cases"] if info else 0,
                    }
                )
            d += timedelta(days=1)

        on_duty = sum(1 for c in calendar_days if c["status"] == "ON_DUTY")
        total = len(calendar_days)

        # Monthly summary buckets
        monthly: dict[str, dict] = defaultdict(lambda: {"on_duty": 0, "off_duty": 0, "total_cases": 0})
        for c in calendar_days:
            m = c["date"][:7]
            if c["status"] == "ON_DUTY":
                monthly[m]["on_duty"] += 1
                monthly[m]["total_cases"] += c["cases"]
            else:
                monthly[m]["off_duty"] += 1

        monthly_summary = [
            {
                "month": m,
                "on_duty": v["on_duty"],
                "off_duty": v["off_duty"],
                "total_cases": v["total_cases"],
                "attendance_pct": round(v["on_duty"] / max(v["on_duty"] + v["off_duty"], 1) * 100, 1),
            }
            for m, v in sorted(monthly.items())
        ]

        return {
            "agent_id": agent.id,
            "current_status": agent.status.value if hasattr(agent.status, "value") else str(agent.status),
            "calendar": calendar_days,
            "summary": {
                "total_working_days": total,
                "on_duty_days": on_duty,
                "off_duty_days": total - on_duty,
                "attendance_rate_pct": round(on_duty / max(total, 1) * 100, 1),
            },
            "monthly_summary": monthly_summary,
        }
