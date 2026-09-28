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
}.items():
    os.environ.setdefault(_name, _value)


# 2026-09-28 (B19): the Postgres suite (tests/pg/) talks to exactly one
# database server, named by TIQ_PG_TEST_URL. Its host is the ONE address let
# through; everything else stays blocked. Unset (every ordinary run), nothing
# changes and tests/pg skips.
def _pg_test_host() -> str | None:
    url = os.environ.get("TIQ_PG_TEST_URL", "")
    if not url:
        return None
    from urllib.parse import urlsplit
    return urlsplit(url.replace("+psycopg2", "")).hostname


_PG_TEST_HOST = _pg_test_host()


def _is_local(address) -> bool:
    if not isinstance(address, tuple) or not address:
        return True                                   # AF_UNIX and the like
    host = str(address[0])
    if host in ("localhost", ""):
        return True
    if _PG_TEST_HOST and host in _pg_test_addresses():
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False                                  # a hostname: resolving it is network


def _pg_test_addresses() -> set[str]:
    """The Postgres test host and what it resolves to (the socket sees the IP)."""
    out = {_PG_TEST_HOST}
    try:
        out |= {ai[4][0] for ai in socket.getaddrinfo(_PG_TEST_HOST, None)}
    except OSError:
        pass
    return out


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
