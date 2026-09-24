"""The client address behind a reverse proxy, which the auth rate limit keys on.

2026-09-24. Behind Caddy every request reached uvicorn from the proxy's
container address, and uvicorn believes X-Forwarded-For only from 127.0.0.1,
so the 10-per-minute login limit was one bucket shared by every user of the
deployment. These tests go through the app's real middleware stack with a
chosen peer address: a proxy on the Docker network, or a stranger talking to
the API port directly.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.ratelimit import limiter
from app.main import app

PROXY = "172.18.0.5"        # Caddy on a compose network
STRANGER = "203.0.113.7"    # a public address reaching the API port itself
LIMIT = settings.AUTH_RATE_LIMIT_PER_MINUTE
BAD_LOGIN = {"email": "nobody@t.io", "password": "wrong-password", "device_id": "device-000001"}

engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="module", autouse=True)
def _db():
    Base.metadata.create_all(engine)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _fresh_bucket():
    limiter.reset()
    yield
    limiter.reset()


def _logins(peer: str, forwarded_for: list[str]) -> list[int]:
    """POST a bad login once per header value, from `peer`; return the codes."""
    async def run() -> list[int]:
        transport = httpx.ASGITransport(app=app, client=(peer, 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as c:
            return [
                (await c.post("/api/v1/auth/login", json=BAD_LOGIN,
                              headers={"X-Forwarded-For": xff})).status_code
                for xff in forwarded_for
            ]
    return asyncio.run(run())


def test_users_behind_the_proxy_each_get_their_own_bucket():
    codes = _logins(PROXY, [f"198.51.100.{i}" for i in range(LIMIT + 3)])
    assert codes == [401] * (LIMIT + 3)


def test_one_user_behind_the_proxy_is_still_limited():
    codes = _logins(PROXY, ["198.51.100.50"] * (LIMIT + 2))
    assert codes == [401] * LIMIT + [429, 429]


def test_a_forged_header_from_outside_the_trusted_networks_is_ignored():
    codes = _logins(STRANGER, [f"198.51.100.{i}" for i in range(LIMIT + 2)])
    assert codes == [401] * LIMIT + [429, 429]


def test_an_address_the_client_writes_into_the_header_cannot_choose_its_bucket():
    # Caddy appends the address it saw; anything to its left came from the client.
    codes = _logins(PROXY, [f"192.0.2.{i}, 198.51.100.77" for i in range(LIMIT + 2)])
    assert codes == [401] * LIMIT + [429, 429]
