"""The one rate limiter, importable by the routes it protects.

2026-09-14. Until today `main.py` built a `Limiter` with
`default_limits=["60/minute"]`, set it on `app.state`, registered the 429
handler — and that was the whole of it. slowapi enforces default limits only
through `SlowAPIMiddleware` or a `@limiter.limit` decorator, and the codebase
had neither. Measured before this change: 80 rapid requests to a public route
and 70 to /auth/login, zero 429s. CLAUDE.md listed "slowapi rate limiting"
among the controls that were "each fixed once" and must not regress. It had
never worked.

Why the limiter moves here instead of the middleware being switched on. The
global 60-per-minute default is the wrong tool for this API: the manager
overview issues a dozen requests on load, the live map polls, and every user
behind one office NAT shares an address, so a blanket per-IP limit would 429
legitimate sessions before it inconvenienced anyone hostile. What the config
actually declared — `AUTH_RATE_LIMIT_PER_MINUTE = 10`, unused since it was
written — is a limit for the PUBLIC, unauthenticated routes, which are the
ones an attacker can hit without a token: login, quick-login and the ID-card
check. Those are decorated; nothing else is. The limiter lives in core/ so
`endpoints/*.py` can import it without importing `main`, which imports them.

`default_limits` is kept on the object so the intent is still visible, but
with no middleware it applies to nothing, and that is stated rather than left
to be discovered a second time.
"""
from __future__ import annotations

import logging

import structlog
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

logger = structlog.get_logger()


def storage_uri() -> str:
    """Redis unless RATE_LIMIT_STORAGE_URI names something else. A count kept
    in process memory is multiplied by the number of uvicorn workers."""
    uri = settings.RATE_LIMIT_STORAGE_URI.strip()
    if not uri or uri.startswith("${"):
        return settings.REDIS_URL
    return uri


def build_limiter(uri: str) -> Limiter:
    # in_memory_fallback_enabled: with Redis unreachable, count per process until
    # it returns (slowapi re-checks it) — limits still apply, login never 500s.
    return Limiter(
        key_func=get_remote_address,
        default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"],
        storage_uri=uri,
        in_memory_fallback_enabled=True,
    )


def storage_state(lim: Limiter) -> str:
    """'redis', 'memory' (configured so) or 'memory-fallback' (Redis is down).
    `_storage_dead` is slowapi 0.1.9's own flag (pinned in requirements.txt)."""
    if (lim._storage_uri or "").startswith("memory://"):
        return "memory"
    return "memory-fallback" if getattr(lim, "_storage_dead", False) else "redis"


class _StorageTransitions(logging.Handler):
    """slowapi logs each switch exactly once (to memory on the first failure, back
    on recovery), quietly. Re-log both where alerts look; never per request."""

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if msg.startswith("Rate limit storage unreachable"):
            logger.error("ratelimit.storage_unreachable", now="memory-fallback",
                         effect="limits count per process until Redis returns")
        elif msg.startswith("Rate limit storage recovered"):
            logger.warning("ratelimit.storage_recovered", now="redis")


_slowapi_log = logging.getLogger("slowapi")
if not any(isinstance(h, _StorageTransitions) for h in _slowapi_log.handlers):
    _slowapi_log.addHandler(_StorageTransitions())
    _slowapi_log.setLevel(logging.INFO)     # the recovery message is INFO

limiter = build_limiter(storage_uri())

#: Per client address, on the routes that need no token to reach.
AUTH_LIMIT = f"{settings.AUTH_RATE_LIMIT_PER_MINUTE}/minute"
