# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-19 — New file. The Visit model has carried the evidence for this
#   since it was written — check-in coordinates and time, per-photo GPS and
#   capture time, a SHA-256 of each image, the device id, and the measured
#   distance from the customer's address — and nothing ever read any of it.
#   A manager's only assurance that a visit happened as reported was the
#   geo-fence at the moment of recording, which proves where the phone was and
#   nothing else.
#
#   Rules, not a model, and deliberately so: every signal here is arithmetic
#   over facts already captured, so it needs no training data — which matters,
#   because this database is seeded with random values and would teach a model
#   the seed script's own distribution. What a manager confirms or dismisses
#   here becomes the labelled set a supervised version could later learn from.
#
#   The thresholds are conservative on purpose. A detector that cries wolf is
#   worse than none: it trains the manager to dismiss the panel, and then the
#   real one is dismissed too.
# ───────────────────────────────────────────────────────────────────────────
"""Field-visit anomaly detection over evidence the app already collects."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.geo import haversine_metres
from app.models.agent import Agent
from app.models.agent_location import AgentLocation
from app.models.case import Case
from app.models.fraud_review import FraudReview, ReviewVerdict
from app.models.visit import Visit

# ── Finding types ────────────────────────────────────────────────────────────
IMPOSSIBLE_TRAVEL = "IMPOSSIBLE_TRAVEL"
OVERLAPPING_VISITS = "OVERLAPPING_VISITS"
PHOTO_LOCATION_MISMATCH = "PHOTO_LOCATION_MISMATCH"
DUPLICATE_PHOTO = "DUPLICATE_PHOTO"
VISIT_TOO_SHORT = "VISIT_TOO_SHORT"
FAR_FROM_CUSTOMER = "FAR_FROM_CUSTOMER"
TRAIL_CONTRADICTS_VISIT = "TRAIL_CONTRADICTS_VISIT"

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def implied_speed_kmh(metres: float, seconds: float) -> float | None:
    """Speed needed to cover `metres` in `seconds`. None when time did not pass —
    a zero or negative gap is an ordering problem, reported separately as an
    overlap rather than as an infinite speed."""
    if seconds <= 0:
        return None
    return (metres / seconds) * 3.6


class FraudService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # -----------------------------------------------------------------
    def scan(
        self,
        agent_ids: list[str],
        date_from: date | None = None,
        date_to: date | None = None,
        include_dismissed: bool = False,
    ) -> dict:
        """Run every check over one manager's agents for a date window.

        `agent_ids` must already be tenant-filtered by the caller — this service
        never derives the set itself.
        """
        if not agent_ids:
            return {"findings": [], "counts": {}, "by_severity": {},
                    "visits_examined": 0, "by_agent": [], "dismissed_hidden": 0}

        date_to = date_to or datetime.now(timezone.utc).date()
        date_from = date_from or (date_to - timedelta(days=settings.FRAUD_SCAN_DAYS))

        visits = (
            self.db.query(Visit)
            .filter(
                Visit.agent_id.in_(agent_ids),
                Visit.check_in_time >= datetime.combine(date_from, datetime.min.time()).replace(tzinfo=timezone.utc),
                Visit.check_in_time < datetime.combine(date_to + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone.utc),
            )
            .options(joinedload(Visit.agent).joinedload(Agent.user), joinedload(Visit.case))
            .order_by(Visit.agent_id, Visit.check_in_time)
            .all()
        )

        findings: list[dict] = []
        findings += self._travel_and_overlap(visits)
        findings += self._photo_location(visits)
        findings += self._duplicate_photos(visits)
        findings += self._short_visits(visits)
        findings += self._far_from_customer(visits)
        findings += self._trail_contradiction(visits, agent_ids, date_from, date_to)

        # Attach any standing verdict, keyed on (visit, check) — the pair that
        # survives a rescan, which is what lets a dismissal stay dismissed
        # without ever storing the finding itself.
        reviews = {
            (r.visit_id, r.finding_type): r
            for r in self.db.query(FraudReview)
            .filter(FraudReview.visit_id.in_([f["visit_id"] for f in findings] or [""]))
            .all()
        }
        dismissed_hidden = 0
        kept: list[dict] = []
        for f in findings:
            r = reviews.get((f["visit_id"], f["type"]))
            f["review"] = None if r is None else {
                "verdict": r.verdict.value if hasattr(r.verdict, "value") else str(r.verdict),
                "note": r.note,
                "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            }
            if r is not None and r.verdict == ReviewVerdict.DISMISSED and not include_dismissed:
                dismissed_hidden += 1
                continue
            kept.append(f)
        findings = kept

        counts: dict[str, int] = defaultdict(int)
        by_sev: dict[str, int] = defaultdict(int)
        per_agent: dict[str, dict] = {}
        for f in findings:
            counts[f["type"]] += 1
            by_sev[f["severity"]] += 1
            a = per_agent.setdefault(f["agent_id"], {
                "agent_id": f["agent_id"], "agent_name": f["agent_name"],
                "employee_code": f["employee_code"], "total": 0, "high": 0, "confirmed": 0,
            })
            a["total"] += 1
            if f["severity"] == HIGH:
                a["high"] += 1
            if f["review"] and f["review"]["verdict"] == "CONFIRMED":
                a["confirmed"] += 1

        order = {HIGH: 0, MEDIUM: 1, LOW: 2}
        findings.sort(key=lambda f: (order.get(f["severity"], 9), f["occurred_at"]), reverse=False)

        return {
            "findings": findings,
            "counts": dict(counts),
            "by_severity": dict(by_sev),
            # Which agents account for the findings. The point of the whole
            # feature: one agent with twelve is a very different conversation
            # from twelve agents with one each.
            "by_agent": sorted(per_agent.values(),
                               key=lambda a: (a["high"], a["total"]), reverse=True),
            "dismissed_hidden": dismissed_hidden,
            "visits_examined": len(visits),
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
        }

    # -----------------------------------------------------------------
    def _meta(self, visit: Visit) -> dict:
        agent = visit.agent
        case = visit.case
        return {
            "agent_id": visit.agent_id,
            "employee_code": agent.employee_code if agent else None,
            "agent_name": agent.user.full_name if agent and agent.user else None,
            "visit_id": visit.id,
            "case_id": visit.case_id,
            "case_number": case.case_number if case else None,
        }

    # ── 1 & 2. impossible travel, and visits that overlap in time ────────────
    def _travel_and_overlap(self, visits: list[Visit]) -> list[dict]:
        out: list[dict] = []
        by_agent_day: dict[tuple, list[Visit]] = defaultdict(list)
        for v in visits:
            t = _as_utc(v.check_in_time)
            if t is None:
                continue
            by_agent_day[(v.agent_id, t.date())].append(v)

        for (_agent_id, _day), day_visits in by_agent_day.items():
            day_visits.sort(key=lambda v: _as_utc(v.check_in_time))
            for prev, cur in zip(day_visits, day_visits[1:]):
                prev_out = _as_utc(prev.check_out_time) or _as_utc(prev.check_in_time)
                cur_in = _as_utc(cur.check_in_time)
                if prev_out is None or cur_in is None:
                    continue

                gap = (cur_in - prev_out).total_seconds()
                metres = haversine_metres(
                    prev.check_in_latitude, prev.check_in_longitude,
                    cur.check_in_latitude, cur.check_in_longitude,
                )

                # Overlap: the agent was recorded as still at the previous visit
                # when this one began. No speed can explain it — it is a
                # bookkeeping impossibility, not a fast journey.
                if gap < 0:
                    # An overlap at the SAME address is not fraud — co-borrowers
                    # and multiple loans in one building genuinely produce two
                    # visits at one doorstep, and a careless check-out makes them
                    # overlap. Only an overlap across real distance is a claim to
                    # have been in two places at once.
                    far = metres > settings.FRAUD_OVERLAP_MIN_METRES
                    out.append({
                        "type": OVERLAPPING_VISITS,
                        "severity": HIGH if far else LOW,
                        "occurred_at": cur_in.isoformat(),
                        "summary": (
                            f"Checked in {abs(int(gap // 60))} min before checking out of the "
                            f"previous visit {round(metres / 1000, 1)} km away"
                            if far else
                            f"Two visits at the same address overlap by "
                            f"{abs(int(gap // 60))} min — check-out likely missed"
                        ),
                        "evidence": {
                            "previous_visit_id": prev.id,
                            "overlap_seconds": int(-gap),
                            "distance_metres": round(metres),
                        },
                        **self._meta(cur),
                    })
                    continue

                speed = implied_speed_kmh(metres, gap)
                if speed is not None and speed > settings.FRAUD_MAX_SPEED_KMH:
                    out.append({
                        "type": IMPOSSIBLE_TRAVEL,
                        "severity": HIGH,
                        "occurred_at": cur_in.isoformat(),
                        "summary": (
                            f"Covered {round(metres / 1000, 1)} km in {int(gap // 60)} min — "
                            f"implies {round(speed)} km/h"
                        ),
                        "evidence": {
                            "previous_visit_id": prev.id,
                            "distance_metres": round(metres),
                            "gap_seconds": int(gap),
                            "implied_speed_kmh": round(speed),
                        },
                        **self._meta(cur),
                    })
        return out

    # ── 3. a photo taken somewhere other than the visit ─────────────────────
    def _photo_location(self, visits: list[Visit]) -> list[dict]:
        out: list[dict] = []
        for v in visits:
            for label, lat, lon in (
                ("agent selfie", v.agent_photo_lat, v.agent_photo_lon),
                ("borrower photo", v.borrower_photo_lat, v.borrower_photo_lon),
                ("asset photo", v.object_photo_lat, v.object_photo_lon),
            ):
                if lat is None or lon is None:
                    continue
                metres = haversine_metres(v.check_in_latitude, v.check_in_longitude, lat, lon)
                if metres > settings.FRAUD_PHOTO_DRIFT_METRES:
                    out.append({
                        "type": PHOTO_LOCATION_MISMATCH,
                        "severity": HIGH,
                        "occurred_at": (_as_utc(v.check_in_time) or datetime.now(timezone.utc)).isoformat(),
                        "summary": f"{label.capitalize()} was taken {round(metres)} m from the visit location",
                        "evidence": {"photo": label, "drift_metres": round(metres)},
                        **self._meta(v),
                    })
        return out

    # ── 4. the same image submitted more than once ──────────────────────────
    def _duplicate_photos(self, visits: list[Visit]) -> list[dict]:
        """Identical image bytes across visits. The strongest single signal
        here: two visits cannot legitimately produce a byte-identical photo."""
        seen: dict[str, Visit] = {}
        out: list[dict] = []
        for v in visits:
            for label, digest in (
                ("agent selfie", v.agent_photo_sha256),
                ("borrower photo", v.borrower_photo_sha256),
                ("asset photo", v.object_photo_sha256),
            ):
                if not digest:
                    continue
                first = seen.get(digest)
                if first is None:
                    seen[digest] = v
                    continue
                out.append({
                    "type": DUPLICATE_PHOTO,
                    "severity": HIGH,
                    "occurred_at": (_as_utc(v.check_in_time) or datetime.now(timezone.utc)).isoformat(),
                    "summary": f"{label.capitalize()} is byte-identical to one submitted on an earlier visit",
                    "evidence": {
                        "photo": label,
                        "sha256": digest[:16] + "…",
                        "first_seen_visit_id": first.id,
                        "same_agent": first.agent_id == v.agent_id,
                    },
                    **self._meta(v),
                })
        return out

    # ── 5. a visit too brief to have been a conversation ────────────────────
    def _short_visits(self, visits: list[Visit]) -> list[dict]:
        out: list[dict] = []
        for v in visits:
            start, end = _as_utc(v.check_in_time), _as_utc(v.check_out_time)
            if start is None or end is None:
                continue
            secs = (end - start).total_seconds()
            if 0 <= secs < settings.FRAUD_MIN_VISIT_SECONDS:
                out.append({
                    "type": VISIT_TOO_SHORT,
                    "severity": MEDIUM if v.customer_met else LOW,
                    "occurred_at": start.isoformat(),
                    "summary": (
                        f"Visit lasted {int(secs)} s"
                        + (" and the customer was recorded as met" if v.customer_met else "")
                    ),
                    "evidence": {"duration_seconds": int(secs), "customer_met": v.customer_met},
                    **self._meta(v),
                })
        return out

    # ── 6. recorded from outside the customer's geo-fence ───────────────────
    def _far_from_customer(self, visits: list[Visit]) -> list[dict]:
        """The fence is enforced when the visit is recorded, so anything here
        got in another way — a client that skipped the check, a direct API
        call, or a fence that was wider at the time."""
        out: list[dict] = []
        limit = settings.GEO_FENCE_METRES * settings.FRAUD_FENCE_TOLERANCE
        for v in visits:
            d = v.distance_from_customer_metres
            if d is None or d <= limit:
                continue
            out.append({
                "type": FAR_FROM_CUSTOMER,
                "severity": HIGH if not v.geo_verified else MEDIUM,
                "occurred_at": (_as_utc(v.check_in_time) or datetime.now(timezone.utc)).isoformat(),
                "summary": f"Recorded {round(d)} m from the customer's address",
                "evidence": {
                    "distance_metres": round(d),
                    "fence_metres": settings.GEO_FENCE_METRES,
                    "geo_verified": v.geo_verified,
                },
                **self._meta(v),
            })
        return out

    # ── 7. the location trail says the agent was somewhere else ─────────────
    def _trail_contradiction(
        self, visits: list[Visit], agent_ids: list[str],
        date_from: date, date_to: date,
    ) -> list[dict]:
        """Compare a claimed visit against where the agent's phone actually was.

        This is the strongest check available, because it does not rely on the
        agent's own submission — but it only speaks when the trail has something
        to say. ABSENCE OF TRAIL IS NEVER A FINDING: tracking runs only while the
        agent has the app open, so a gap means the phone was locked far more
        often than it means anything was concealed. Treating silence as guilt
        would make this useless within a week.
        """
        if not visits:
            return []

        rows = (
            self.db.query(AgentLocation)
            .filter(
                AgentLocation.agent_id.in_(agent_ids),
                AgentLocation.recorded_at >= datetime.combine(date_from, datetime.min.time()).replace(tzinfo=timezone.utc),
                AgentLocation.recorded_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone.utc),
            )
            .order_by(AgentLocation.agent_id, AgentLocation.recorded_at)
            .all()
        )
        if not rows:
            return []

        by_agent: dict[str, list[AgentLocation]] = defaultdict(list)
        for r in rows:
            by_agent[r.agent_id].append(r)

        out: list[dict] = []
        for v in visits:
            start, end = _as_utc(v.check_in_time), _as_utc(v.check_out_time)
            if start is None or end is None:
                continue
            during = [
                p for p in by_agent.get(v.agent_id, [])
                if start <= _as_utc(p.recorded_at) <= end
            ]
            # One stray fix is not evidence — a single bad GPS read happens.
            if len(during) < settings.FRAUD_TRAIL_MIN_POINTS:
                continue

            distances = [
                haversine_metres(v.check_in_latitude, v.check_in_longitude, p.latitude, p.longitude)
                for p in during
            ]
            # Every tracked point must be far away. If the agent was ever near
            # the door, the visit happened and the rest is travel.
            if min(distances) <= settings.FRAUD_TRAIL_AWAY_METRES:
                continue

            out.append({
                "type": TRAIL_CONTRADICTS_VISIT,
                "severity": HIGH,
                "occurred_at": start.isoformat(),
                "summary": (
                    f"Location trail places the agent {round(min(distances))}–"
                    f"{round(max(distances))} m away for the whole visit "
                    f"({len(during)} tracked points)"
                ),
                "evidence": {
                    "tracked_points": len(during),
                    "closest_metres": round(min(distances)),
                    "farthest_metres": round(max(distances)),
                },
                **self._meta(v),
            })
        return out
