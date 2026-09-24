"""Live field events: what just happened, pushed to whoever supervises it.

2026-09-24 (standalone plan, tasks P0-05/P0-06). Every manager surface polls —
the live map every few seconds, the SOS bell every 30 — and there was no push
channel anywhere in the tree (CLAUDE.md feature #12: "Nothing is push-based").
The mobile simulator made that concrete: record a visit on the phone and the
manager's screen found out up to half a minute later, which reads as "the web
and the phone are not linked" to anyone watching.

`publish_event` is called at the service commit points that already write an
audit row (visit, payment, PTP) plus check-in/out, location and SOS. It sends
one JSON event to a Redis pub/sub channel and keeps the last `RECENT_MAX` in a
capped list, so a client that connects late — or cannot hold a stream open —
can still read what happened.

Contract, the same one `core/audit.write_audit` established: **publishing never
raises and never changes what happened.** The business write has already
committed by the time an event exists; a Redis outage costs the live view its
immediacy, never the agent their visit. Failures log at WARNING (not ERROR —
nothing is lost that the database does not still hold) and are rate-limited by
a short back-off so a dead Redis does not add a timeout to every request.

Scope today is the agent's MANAGER (`Agent.manager_user_id`), because that is
the only tenant boundary the schema has. When tenancy lands (plan §3, task A02)
the same event also goes to the agency and bank channels; the event shape does
not change.
"""
from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

import structlog

from app.core.config import settings

logger = structlog.get_logger()

# The event vocabulary. A new type is added here first, so a typo at a call
# site fails loudly in tests instead of publishing an event nobody listens for.
EVENT_TYPES = frozenset({
    "agent.checked_in",
    "agent.checked_out",
    "agent.location",
    "visit.recorded",
    "payment.submitted",
    "payment.verified",
    "ptp.set",
    "sos.triggered",
    "sos.cancelled",
})

RECENT_MAX = 200                # events kept per manager for late joiners
RECENT_TTL_SECONDS = 86_400     # a day; the live view is about today
_BACKOFF_SECONDS = 30.0         # after a Redis failure, skip it for this long


def manager_channel(manager_user_id: str) -> str:
    return f"tiq:events:mgr:{manager_user_id}"


def manager_recent_key(manager_user_id: str) -> str:
    return f"tiq:events:recent:mgr:{manager_user_id}"


class _MemoryStore:
    """In-process stand-in when Redis is unreachable (tests, a bare laptop).

    Keeps `recent` working within one process. It cannot fan out across
    processes, so `publish` is a no-op — the stream endpoint reports itself
    unavailable in that case and clients fall back to polling `recent`.
    """

    is_memory = True

    def __init__(self) -> None:
        self._lists: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=RECENT_MAX))

    def publish_recent(self, channel: str, key: str, payload: str) -> None:  # noqa: ARG002
        self._lists[key].appendleft(payload)

    def recent(self, key: str, limit: int) -> list[str]:
        return list(self._lists.get(key, ()))[:limit]


class _RedisStore:
    is_memory = False

    def __init__(self, client) -> None:
        self._c = client

    def publish_recent(self, channel: str, key: str, payload: str) -> None:
        pipe = self._c.pipeline(transaction=False)
        pipe.publish(channel, payload)
        pipe.lpush(key, payload)
        pipe.ltrim(key, 0, RECENT_MAX - 1)
        pipe.expire(key, RECENT_TTL_SECONDS)
        pipe.execute()

    def recent(self, key: str, limit: int) -> list[str]:
        return self._c.lrange(key, 0, max(0, limit - 1))


_store: _MemoryStore | _RedisStore | None = None
_memory = _MemoryStore()
_down_until = 0.0


def _get_store():
    """Redis when reachable, else the in-process store. Re-probes Redis after
    the back-off instead of pinning the fallback for the life of the process —
    Redis restarting under a running API must not leave events local forever."""
    global _store, _down_until
    if _store is not None:
        return _store
    if time.monotonic() < _down_until:
        return _memory
    try:
        import redis
        client = redis.from_url(settings.REDIS_URL, decode_responses=True,
                                socket_connect_timeout=0.5, socket_timeout=0.5)
        client.ping()
        _store = _RedisStore(client)
        return _store
    except Exception as exc:  # noqa: BLE001
        _down_until = time.monotonic() + _BACKOFF_SECONDS
        logger.warning("events.redis_unavailable", error=str(exc), error_type=type(exc).__name__)
        return _memory


def _mark_redis_down(exc: Exception) -> None:
    global _store, _down_until
    _store = None
    _down_until = time.monotonic() + _BACKOFF_SECONDS
    logger.warning("events.redis_failed", error=str(exc), error_type=type(exc).__name__)


def set_store_for_tests(store) -> None:
    """Tests inject a store; `None` resets to auto-detection."""
    global _store, _down_until, _memory
    _store = store
    _down_until = 0.0
    _memory = _MemoryStore()


def _agent_name(agent) -> str | None:
    try:
        user = getattr(agent, "user", None)
        return getattr(user, "full_name", None)
    except Exception:  # noqa: BLE001 — a lazy-load on a closed session must not cost the event
        return None


def publish_event(
    event_type: str,
    *,
    agent=None,
    manager_user_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> bool:
    """Publish one event to the agent's manager. Returns whether it was sent.

    Never raises. Returns False — and publishes nothing — when there is no
    manager to send it to: an unassigned agent's events have no audience yet.
    """
    try:
        if event_type not in EVENT_TYPES:
            # A programming error, but still not worth failing a visit over.
            logger.error("events.unknown_type", event_type=event_type)
            return False
        mgr = manager_user_id or (getattr(agent, "manager_user_id", None) if agent is not None else None)
        if not mgr:
            return False
        event = {
            "id": str(uuid.uuid4()),
            "type": event_type,
            "at": datetime.now(timezone.utc).isoformat(),
            "agent_id": getattr(agent, "id", None) if agent is not None else None,
            "agent_name": _agent_name(agent) if agent is not None else None,
            "data": data or {},
        }
        payload = json.dumps(event, default=str)
        store = _get_store()
        try:
            store.publish_recent(manager_channel(mgr), manager_recent_key(mgr), payload)
        except Exception as exc:  # noqa: BLE001
            _mark_redis_down(exc)
            _memory.publish_recent(manager_channel(mgr), manager_recent_key(mgr), payload)
            return False
        return not getattr(store, "is_memory", False)
    except Exception as exc:  # noqa: BLE001 — see module docstring: never raises
        logger.warning("events.publish_failed", event_type=event_type,
                       error=str(exc), error_type=type(exc).__name__)
        return False


def recent_events(manager_user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Newest first. Unparseable entries are skipped, not fatal."""
    limit = max(1, min(limit, RECENT_MAX))
    store = _get_store()
    try:
        raw = store.recent(manager_recent_key(manager_user_id), limit)
    except Exception as exc:  # noqa: BLE001
        _mark_redis_down(exc)
        raw = _memory.recent(manager_recent_key(manager_user_id), limit)
    out: list[dict[str, Any]] = []
    for item in raw:
        try:
            out.append(json.loads(item))
        except (TypeError, ValueError):
            continue
    return out
