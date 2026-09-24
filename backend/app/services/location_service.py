# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-18 — New file. Backs POST /agent/location (batch ingest) and the two
#   manager read endpoints. Ingest is batch-only by design: a field phone loses
#   signal constantly, so the client queues fixes locally and flushes them when
#   it reconnects. A one-fix-per-request endpoint would have silently dropped
#   every point captured in a dead zone — which is exactly where a lone worker
#   most needs a trail.
# ───────────────────────────────────────────────────────────────────────────
"""On-duty agent location trail: ingest, live positions, day trail."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.errors import AppException, ErrorCode
from app.core.events import publish_event
from app.core.geo import haversine_metres
from app.models.agent import Agent
from app.models.agent_location import AgentLocation, LocationSource

# A batch is a flushed offline queue, not a firehose. 500 fixes at the client's
# 60s ceiling is over eight hours of backlog — more than a full shift.
MAX_BATCH = 500

# Fixes older than this are dropped. A queue that survived this long is from a
# previous shift and would draw a misleading trail across a day it does not
# belong to.
MAX_AGE_HOURS = 24

# Device clocks drift and can be set wrong outright. Anything beyond this in the
# future is clamped to now rather than rejected, so a badly-set phone still
# produces a usable trail instead of a silent gap.
FUTURE_TOLERANCE_SECONDS = 120

# Server-side echo of the client distance filter. The client already thins
# fixes; this catches a stationary phone whose GPS jitters a few metres and
# would otherwise write hundreds of near-identical rows overnight.
#
# 2026-09-14 — a fix dropped by THIS filter is still the agent reporting in.
# It used to be counted as `rejected` and forgotten, and `last_location_update`
# advanced only from stored rows, so a parked agent's "last seen" on the live
# map never moved however often the phone reported: measured, an agent at
# "28 min ago" while uploading every minute. The row is still not written —
# that is the filter's whole point — but the agent's heard-from time is.
MIN_MOVE_METRES = 25.0


class LocationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # -----------------------------------------------------------------
    # POST /agent/location
    # -----------------------------------------------------------------
    def record_batch(self, agent: Agent, pings: list) -> dict:
        """Ingest a batch of GPS fixes for one agent.

        Returns per-batch counts rather than raising on individual bad fixes:
        one malformed point from a flaky sensor must not cost the agent the
        rest of their queued trail.
        """
        if not pings:
            return {"accepted": 0, "rejected": 0, "last_recorded_at": None}
        if len(pings) > MAX_BATCH:
            raise AppException(
                400, ErrorCode.VALIDATION_ERROR,
                f"Batch too large — {len(pings)} fixes, maximum {MAX_BATCH}",
            )

        now = datetime.now(timezone.utc)
        floor = now - timedelta(hours=MAX_AGE_HOURS)
        ceiling = now + timedelta(seconds=FUTURE_TOLERANCE_SECONDS)

        # Ordering by device clock is what makes the distance filter meaningful —
        # an out-of-order queue would compare each fix against an arbitrary
        # neighbour rather than the one before it.
        ordered = sorted(pings, key=lambda p: self._as_utc(p.recorded_at))

        # Anchor the filter on the most recent stored fix, so a flush that
        # resumes an existing trail does not re-record its starting point.
        last = (
            self.db.query(AgentLocation)
            .filter(AgentLocation.agent_id == agent.id)
            .order_by(AgentLocation.recorded_at.desc())
            .first()
        )
        prev_lat = last.latitude if last else None
        prev_lon = last.longitude if last else None
        prev_at = self._as_utc(last.recorded_at) if last else None

        accepted = 0
        rejected = 0
        newest: datetime | None = None
        # Newest fix that was real but stationary — heard from, not stored.
        heard: datetime | None = None
        rows: list[AgentLocation] = []

        for p in ordered:
            at = self._as_utc(p.recorded_at)
            if at > ceiling:
                at = now                      # clock skew — clamp, do not discard
            if at < floor:
                rejected += 1
                continue
            if not (-90.0 <= p.latitude <= 90.0) or not (-180.0 <= p.longitude <= 180.0):
                rejected += 1
                continue
            # Null Island: a real fix here is implausible for field collections
            # and is the classic signature of a zeroed sensor reading.
            if p.latitude == 0.0 and p.longitude == 0.0:
                rejected += 1
                continue
            # Never let an already-stored fix be re-inserted by a duplicate flush.
            if prev_at is not None and at <= prev_at:
                rejected += 1
                continue

            source = self._source(getattr(p, "source", None))
            if prev_lat is not None and source == LocationSource.HEARTBEAT:
                moved = haversine_metres(prev_lat, prev_lon, p.latitude, p.longitude)
                if moved < MIN_MOVE_METRES:
                    rejected += 1
                    heard = at if heard is None or at > heard else heard
                    continue

            rows.append(AgentLocation(
                agent_id=agent.id,
                latitude=p.latitude,
                longitude=p.longitude,
                accuracy_metres=getattr(p, "accuracy_metres", None),
                recorded_at=at,
                source=source,
                is_sos=bool(agent.sos_active),
                battery_pct=self._battery(getattr(p, "battery_pct", None)),
            ))
            prev_lat, prev_lon, prev_at = p.latitude, p.longitude, at
            newest = at
            accepted += 1

        if rows:
            self.db.add_all(rows)
            # Keep the denormalised "latest" in step, but only ever forwards —
            # a late-arriving offline batch must not drag the live position
            # backwards to where the agent was an hour ago.
            if newest is not None and self._is_newer(agent.last_location_update, newest):
                agent.last_known_latitude = rows[-1].latitude
                agent.last_known_longitude = rows[-1].longitude
                agent.last_location_update = newest.isoformat()
        # A stationary heartbeat moves the clock and nothing else: the position
        # is within MIN_MOVE_METRES of what is already stored.
        if heard is not None and (newest is None or heard > newest)                 and self._is_newer(agent.last_location_update, heard):
            agent.last_location_update = heard.isoformat()
        if rows or heard is not None:
            self.db.commit()
        # 2026-09-24 — one event per batch carrying only the newest stored fix,
        # never one per row: a flushed offline queue can hold hundreds, and the
        # live map needs where the agent IS, not the backlog. A stationary
        # heartbeat stores nothing and publishes nothing.
        if rows:
            publish_event("agent.location", agent=agent,
                          data={"lat": rows[-1].latitude, "lon": rows[-1].longitude,
                                "accuracy_m": rows[-1].accuracy_metres,
                                "recorded_at": rows[-1].recorded_at.isoformat(),
                                "accepted": accepted})

        return {
            "accepted": accepted,
            "rejected": rejected,
            "last_recorded_at": newest.isoformat() if newest else None,
        }

    # -----------------------------------------------------------------
    # GET /manager/agents/live
    # -----------------------------------------------------------------
    def live_positions(self, agent_ids: list[str]) -> list[dict]:
        """Latest fix per agent, for the manager live map.

        Scoped by the caller passing an already tenant-filtered agent_ids —
        this service never derives that set itself.
        """
        if not agent_ids:
            return []

        agents = self.db.query(Agent).filter(Agent.id.in_(agent_ids)).all()

        # One grouped query for the newest fix per agent, rather than N queries
        # in the loop below — this endpoint is polled by every open manager tab.
        newest_sub = (
            self.db.query(
                AgentLocation.agent_id.label("aid"),
                func.max(AgentLocation.recorded_at).label("mx"),
            )
            .filter(AgentLocation.agent_id.in_(agent_ids))
            .group_by(AgentLocation.agent_id)
            .subquery()
        )
        latest = {
            row.agent_id: row
            for row in self.db.query(AgentLocation)
            .join(
                newest_sub,
                (AgentLocation.agent_id == newest_sub.c.aid)
                & (AgentLocation.recorded_at == newest_sub.c.mx),
            )
            .all()
        }

        now = datetime.now(timezone.utc)
        out: list[dict] = []
        for a in agents:
            fix = latest.get(a.id)
            # WHICHEVER SOURCE IS NEWER SUPPLIES BOTH THE POSITION AND THE TIME.
            #
            # Two things can carry an agent's whereabouts and they are written
            # independently:
            #   * an AgentLocation row — the trail, written by record_batch;
            #   * agent.last_known_lat/lon + last_location_update — written by
            #     record_batch for a stationary heartbeat it deliberately did
            #     NOT store, and ALSO by agent_service.checkin, trigger_sos and
            #     visit_service, none of which are constrained to be near the
            #     newest row.
            #
            # 2026-09-15 — this took the position from the row and the time from
            # max(row, agent), which is only safe if the two are always within
            # MIN_MOVE_METRES. Check-in and visit recording break that: an agent
            # checking in 10 km from their last stored fix appeared AT THE OLD
            # FIX with age_seconds ~ 0 — a stale position wearing a fresh
            # timestamp, on the screen a manager uses to find someone. Reading
            # both fields off one source cannot produce that, whoever wrote it.
            stored_at = self._as_utc(fix.recorded_at) if fix else None
            heard_at = self._parse(a.last_location_update)
            use_agent_row = (
                heard_at is not None
                and (stored_at is None or heard_at > stored_at)
                and a.last_known_latitude is not None
                and a.last_known_longitude is not None
            )
            # Accuracy and battery describe the STORED fix. They still describe
            # this position when the agent row is merely the stationary
            # heartbeat's clock — same place by construction — and describe a
            # different place entirely after a check-in elsewhere. Measure it
            # rather than guess: the two paths are indistinguishable here.
            same_place = True
            if use_agent_row:
                same_place = fix is not None and haversine_metres(
                    fix.latitude, fix.longitude,
                    a.last_known_latitude, a.last_known_longitude,
                ) < MIN_MOVE_METRES
                lat, lon, at = a.last_known_latitude, a.last_known_longitude, heard_at
            else:
                lat = fix.latitude if fix else a.last_known_latitude
                lon = fix.longitude if fix else a.last_known_longitude
                at = stored_at if stored_at is not None else heard_at
            out.append({
                "agent_id": a.id,
                "employee_code": a.employee_code,
                "full_name": a.user.full_name if a.user else a.employee_code,
                "status": a.status.value if hasattr(a.status, "value") else str(a.status),
                "sos_active": a.sos_active,
                "sos_triggered_at": a.sos_triggered_at,
                "latitude": lat,
                "longitude": lon,
                "accuracy_metres": fix.accuracy_metres if (fix and same_place) else None,
                "battery_pct": fix.battery_pct if (fix and same_place) else None,
                "recorded_at": at.isoformat() if at else None,
                # Age is computed server-side so every client agrees on what
                # counts as stale, and so the UI never has to reason about
                # timezones to grey out an old pin.
                "age_seconds": int((now - at).total_seconds()) if at else None,
            })
        return out

    # -----------------------------------------------------------------
    # GET /manager/agents/{agent_id}/trail
    # -----------------------------------------------------------------
    def trail(self, agent_id: str, day: str | None = None, sos_only: bool = False) -> dict:
        """One agent's fixes for one IST day, oldest first."""
        from app.core.geo import IST

        try:
            target = (
                datetime.strptime(day, "%Y-%m-%d").date()
                if day else datetime.now(IST).date()
            )
        except ValueError as exc:
            raise AppException(
                400, ErrorCode.VALIDATION_ERROR, "date must be YYYY-MM-DD"
            ) from exc

        start_ist = datetime.combine(target, datetime.min.time()).replace(tzinfo=IST)
        end_ist = start_ist + timedelta(days=1)

        q = self.db.query(AgentLocation).filter(
            AgentLocation.agent_id == agent_id,
            AgentLocation.recorded_at >= start_ist.astimezone(timezone.utc),
            AgentLocation.recorded_at < end_ist.astimezone(timezone.utc),
        )
        if sos_only:
            q = q.filter(AgentLocation.is_sos.is_(True))

        points = q.order_by(AgentLocation.recorded_at.asc()).all()

        distance_m = 0.0
        for a, b in zip(points, points[1:]):
            distance_m += haversine_metres(a.latitude, a.longitude, b.latitude, b.longitude)

        return {
            "agent_id": agent_id,
            "date": target.isoformat(),
            "point_count": len(points),
            "distance_metres": round(distance_m, 1),
            "points": [{
                "latitude": p.latitude,
                "longitude": p.longitude,
                "accuracy_metres": p.accuracy_metres,
                "recorded_at": self._as_utc(p.recorded_at).isoformat(),
                "source": p.source.value if hasattr(p.source, "value") else str(p.source),
                "is_sos": p.is_sos,
                "battery_pct": p.battery_pct,
            } for p in points],
        }

    # -----------------------------------------------------------------
    # helpers
    # -----------------------------------------------------------------
    @staticmethod
    def _as_utc(dt: datetime) -> datetime:
        """Postgres returns tz-aware datetimes, but a naive one from a client
        payload or a SQLite-backed test would raise on comparison. Normalise
        once, at every boundary."""
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    @staticmethod
    def _source(raw) -> LocationSource:
        if isinstance(raw, LocationSource):
            return raw
        try:
            return LocationSource(str(raw).upper())
        except (ValueError, AttributeError):
            return LocationSource.HEARTBEAT

    @staticmethod
    def _battery(raw) -> int | None:
        if raw is None:
            return None
        try:
            return max(0, min(100, int(raw)))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _parse(cls, raw: str | None) -> datetime | None:
        if not raw:
            return None
        try:
            return cls._as_utc(datetime.fromisoformat(raw))
        except ValueError:
            return None

    @classmethod
    def _is_newer(cls, existing_iso: str | None, candidate: datetime) -> bool:
        existing = cls._parse(existing_iso)
        return existing is None or candidate > existing
