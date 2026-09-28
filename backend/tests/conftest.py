"""The suite runs the same on every machine.

1. **Settings come from the code's defaults plus this file, never from a
   `backend/.env`.** A demo `.env` with CONTACT_HOUR_START=0 / END=24 used to fail
   six contact-hour tests on code that passes at 8/19. Values already in the
   process environment still win (a CI job or a throwaway container may set them).
2. **No test reaches the network.** Six planner tests used to call the public OSRM
   server (router.project-osrm.org, 8 s timeout per call) with nothing stubbing it.
   Any socket to a non-loopback address now fails at once. Routing therefore falls
   back to Haversine, which is what the planner does in production when OSRM is
   down.

This runs before any test module imports `app`, which is when Settings is built.
"""
from __future__ import annotations

import ipaddress
import os
import socket

os.environ["TIQ_ENV_FILE"] = ""
for _name, _value in {
    # The four settings with no default in core/config.py. Fake, and unusable
    # outside a test.
    "SECRET_KEY": "test-secret-key-not-real-0123456789abcdef",
    "DATABASE_URL": "postgresql+psycopg2://nobody:nothing@127.0.0.1:1/none",
    "MINIO_ACCESS_KEY": "test",
    "MINIO_SECRET_KEY": "test-secret",
    # Per process: the default is REDIS_URL, and on a dev machine that is the
    # shared stack's Redis on loopback, which the network guard lets through.
    "RATE_LIMIT_STORAGE_URI": "memory://",
}.items():
    os.environ.setdefault(_name, _value)


# 2026-09-28 (B19): the Postgres suite (tests/pg/) talks to exactly one
# database server, named by TIQ_PG_TEST_URL. That host AND port is the ONE
# address let through; everything else stays blocked. Unset, or an unresolved
# "${...}", and nothing changes (tests/pg skips).
# (bb's review of ec9f2c6: the first version allowed the host on ANY port —
# Redis, MinIO or the API on the same machine would have passed — and resolved
# DNS on every connect. Host AND port now, resolved once.)
def pg_test_target(url: str | None) -> tuple[str, int] | None:
    """(host, port) named by a Postgres URL, or None when unset or unresolved."""
    if not url or "${" in url:
        return None
    from urllib.parse import urlsplit
    u = urlsplit(url.replace("+psycopg2", ""))
    return (u.hostname, u.port or 5432) if u.hostname else None


def resolve_addresses(host: str) -> frozenset[str]:
    out = {host}
    try:
        out |= {ai[4][0] for ai in socket.getaddrinfo(host, None)}
    except OSError:
        pass
    return frozenset(out)


def allowed_pg_address(address, target, addresses) -> bool:
    return (target is not None and isinstance(address, tuple) and len(address) >= 2
            and str(address[0]) in addresses and address[1] == target[1])


_PG_TARGET = pg_test_target(os.environ.get("TIQ_PG_TEST_URL"))
_PG_ADDRESSES = resolve_addresses(_PG_TARGET[0]) if _PG_TARGET else frozenset()

# The same rule for the one Redis test server (tests/test_rate_limit_redis.py).
def redis_test_target(url: str | None) -> tuple[str, int] | None:
    if not url or "${" in url:
        return None
    from urllib.parse import urlsplit
    u = urlsplit(url)
    return (u.hostname, u.port or 6379) if u.hostname else None


_REDIS_TARGET = redis_test_target(os.environ.get("TIQ_REDIS_TEST_URL"))
_REDIS_ADDRESSES = resolve_addresses(_REDIS_TARGET[0]) if _REDIS_TARGET else frozenset()


def _is_local(address) -> bool:
    if not isinstance(address, tuple) or not address:
        return True                                   # AF_UNIX and the like
    host = str(address[0])
    if host in ("localhost", ""):
        return True
    if allowed_pg_address(address, _PG_TARGET, _PG_ADDRESSES):
        return True
    if allowed_pg_address(address, _REDIS_TARGET, _REDIS_ADDRESSES):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False                                  # a hostname: resolving it is network


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _guarded_connect(self, address):
    if not _is_local(address):
        raise ConnectionRefusedError(f"network access blocked in tests: {address!r}")
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    if not _is_local(address):
        raise ConnectionRefusedError(f"network access blocked in tests: {address!r}")
    return _real_connect_ex(self, address)


def pytest_configure(config):
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex


def pytest_unconfigure(config):
    socket.socket.connect = _real_connect
    socket.socket.connect_ex = _real_connect_ex
