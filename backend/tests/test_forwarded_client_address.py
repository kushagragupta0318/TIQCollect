# 2026-09-28: converted to the v2 harness (tests/_db) at the merge of v1 main
# into standalone-p1.
"""The client address behind a reverse proxy, which the auth rate limit keys on.

2026-09-24. Behind Caddy every request reached uvicorn from the proxy's
container address, and uvicorn believes X-Forwarded-For only from 127.0.0.1,
so the 10-per-minute login limit was one bucket shared by every user of the
deployment.

The app now believes the header only from FORWARDED_ALLOW_IPS. The default is
loopback, which fails safe. A deployment pins the proxy's own address
(docs/MERGING-INTO-PLATFORM.md). These tests go through the real middleware
stack with a chosen peer address. The deployed shape is emulated the way
uvicorn runs it: its ProxyHeadersMiddleware in front of the app, trusting the
pinned proxy.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.config import Settings, settings
from app.core.database import get_db
from app.core.ratelimit import limiter
from app.main import app
from tests._db import create_schema, drop_schema, make_engine, make_session_factory

PROXY = "172.30.0.10"       # Caddy, pinned to a fixed address on the compose network
STRANGER = "203.0.113.7"    # a public address reaching the API port itself
NATTED = "172.17.0.1"       # an internet client that NAT made look private
LIMIT = settings.AUTH_RATE_LIMIT_PER_MINUTE
BAD_LOGIN = {"email": "nobody@t.io", "password": "wrong-password", "device_id": "device-000001"}

# This file built its own `create_engine("sqlite://")` with no schema
# awareness, so `Base.metadata.create_all` failed outright on the v2 model
# (real Postgres schemas, no SQLite equivalent). Every test here posts a bad
# login for a user that never exists, so the request only needs the `users`
# table to be reachable, not seeded — tests/_db.make_engine() +
# create_schema() gives it a schema-mapped SQLite database the login query
# can run against, same as every other converted file in this suite.
engine = make_engine()
Session = make_session_factory(engine)


@pytest.fixture(scope="module", autouse=True)
def _db():
    create_schema(engine)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    yield
    app.dependency_overrides.pop(get_db, None)
    drop_schema(engine)


@pytest.fixture(autouse=True)
def _fresh_bucket():
    limiter.reset()
    yield
    limiter.reset()


def _logins(peer: str, forwarded_for: list[str], trusted: str | None = None) -> list[int]:
    """POST a bad login once per header value, from `peer`; return the codes.

    `trusted` puts uvicorn's server-level middleware in front, as a deployment
    with FORWARDED_ALLOW_IPS=<trusted> runs it. None drives the app as built.
    """
    target = app if trusted is None else ProxyHeadersMiddleware(app, trusted_hosts=trusted)

    async def run() -> list[int]:
        transport = httpx.ASGITransport(app=target, client=(peer, 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as c:
            return [
                (await c.post("/api/v1/auth/login", json=BAD_LOGIN,
                              headers={"X-Forwarded-For": xff})).status_code
                for xff in forwarded_for
            ]
    return asyncio.run(run())


def _distinct(n: int) -> list[str]:
    return [f"198.51.100.{i}" for i in range(n)]


# ── The default: loopback only ────────────────────────────────────────────────

def test_the_default_trusts_loopback_and_nothing_else(monkeypatch):
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    for name, value in (("SECRET_KEY", "k" * 32), ("DATABASE_URL", "sqlite://"),
                        ("MINIO_ACCESS_KEY", "a"), ("MINIO_SECRET_KEY", "b"),
                        ("COMMAND_CENTRE_API_KEY", "c")):
        monkeypatch.setenv(name, value)
    assert Settings(_env_file=None).FORWARDED_ALLOW_IPS == "127.0.0.1"


def test_the_app_believes_a_proxy_on_loopback():
    codes = _logins("127.0.0.1", _distinct(LIMIT + 2))
    assert codes == [401] * (LIMIT + 2)


def test_an_unconfigured_proxy_fails_safe_to_one_shared_bucket():
    # A private peer that nobody listed: its header is ignored, so everyone
    # behind it shares one bucket. That is degraded, but it is not a bypass.
    codes = _logins(PROXY, _distinct(LIMIT + 2))
    assert codes == [401] * LIMIT + [429, 429]


# ── The deployment: FORWARDED_ALLOW_IPS pinned to the proxy's address ─────────

def test_users_behind_the_pinned_proxy_each_get_their_own_bucket():
    codes = _logins(PROXY, _distinct(LIMIT + 3), trusted=PROXY)
    assert codes == [401] * (LIMIT + 3)


def test_one_user_behind_the_pinned_proxy_is_still_limited():
    codes = _logins(PROXY, ["198.51.100.50"] * (LIMIT + 2), trusted=PROXY)
    assert codes == [401] * LIMIT + [429, 429]


def test_a_forged_header_from_anywhere_but_the_proxy_is_ignored():
    for peer in (STRANGER, NATTED):
        limiter.reset()
        codes = _logins(peer, _distinct(LIMIT + 2), trusted=PROXY)
        assert codes == [401] * LIMIT + [429, 429], peer


def test_an_address_the_client_writes_into_the_header_cannot_choose_its_bucket():
    # The proxy appends the address it saw; anything to its left came from the client.
    codes = _logins(PROXY, [f"192.0.2.{i}, 198.51.100.77" for i in range(LIMIT + 2)],
                    trusted=PROXY)
    assert codes == [401] * LIMIT + [429, 429]


def test_why_the_setting_must_never_be_a_range():
    # Trust a whole private range and a NATted client arriving from inside it
    # writes addresses from that range. With every entry trusted, uvicorn takes
    # the leftmost, so the client chooses a new bucket per request and is never
    # limited. This pins the dependency behaviour the default avoids.
    codes = _logins(NATTED, [f"172.20.0.{i}" for i in range(LIMIT + 2)],
                    trusted="172.16.0.0/12")
    assert codes == [401] * (LIMIT + 2)
