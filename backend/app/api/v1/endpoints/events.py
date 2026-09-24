"""Live field events for supervisors — recent list and a Server-Sent Events stream.

2026-09-24 (standalone plan, P0-05/P0-06). See `core/events.py` for why this
exists and what publishes into it.

Two routes, deliberately:

- `GET /events/recent` — a plain JSON read of the last N events. Works with or
  without Redis (in-process fallback), and is what a client polls when it
  cannot hold a stream open.
- `GET /events/stream` — `text/event-stream`. Browsers' `EventSource` cannot
  send an Authorization header, and putting the access token in the URL would
  write it into every proxy and access log, so the frontend reads this with
  `fetch` + a stream reader instead (`lib/eventStream.ts`). The token is
  checked once, at connect; the stream closes itself after
  `STREAM_MAX_SECONDS` so a revoked or expired session cannot listen forever —
  the client reconnects with a fresh token.

Scope: the caller's own events channel (their agents). Both routes are
manager-only today; bank and agency scopes arrive with tenancy (task A02).
"""
from __future__ import annotations

import json
import time

import structlog
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.dependencies import ManagerOnly
from app.core.errors import AppException, ErrorCode
from app.core.events import manager_channel, recent_events

logger = structlog.get_logger()
router = APIRouter(prefix="/events", tags=["Events"])

STREAM_MAX_SECONDS = 600       # reconnect every 10 min with a fresh token
HEARTBEAT_SECONDS = 15         # keeps proxies from closing an idle stream


@router.get("/recent")
def get_recent_events(
    current_user: ManagerOnly,
    limit: int = Query(50, ge=1, le=200),
):
    """The caller's most recent events, newest first."""
    return {"events": recent_events(current_user.id, limit)}


def _sse(event_type: str, data: str) -> str:
    # `data` is one line of compact JSON (json.dumps never emits a newline
    # unless indent is set), so a single `data:` field is always valid SSE.
    return f"event: {event_type}\ndata: {data}\n\n"


@router.get("/stream")
async def stream_events(request: Request, current_user: ManagerOnly):
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(settings.REDIS_URL, decode_responses=True,
                                   socket_connect_timeout=0.5)
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        logger.warning("events.stream_unavailable", error=str(exc), error_type=type(exc).__name__)
        raise AppException(503, ErrorCode.SERVICE_UNAVAILABLE,
                           "Live stream unavailable — poll /events/recent instead")

    channel = manager_channel(current_user.id)

    async def gen():
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)
        started = last_beat = time.monotonic()
        try:
            # `retry` tells a spec-following client how long to wait before
            # reconnecting; the comment line flushes headers immediately so the
            # client knows it is connected before the first real event.
            yield "retry: 3000\n\n: connected\n\n"
            while time.monotonic() - started < STREAM_MAX_SECONDS:
                if await request.is_disconnected():
                    break
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    data = msg.get("data")
                    try:
                        event_type = json.loads(data).get("type", "message")
                    except (TypeError, ValueError):
                        continue
                    yield _sse(event_type, data)
                if time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
                    yield ": ping\n\n"
                    last_beat = time.monotonic()
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
                await client.aclose()
            except Exception:  # noqa: BLE001 — closing a dead connection is not news
                pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
