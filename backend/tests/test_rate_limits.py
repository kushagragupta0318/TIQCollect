"""Rate limiting on the public routes — proven to fire, because it never had.

2026-09-14. main.py had built a Limiter with a 60/minute default, set it on
app.state and registered the 429 handler, and CLAUDE.md listed "slowapi rate
limiting" as a control fixed once and not to be regressed. slowapi enforces
defaults only through SlowAPIMiddleware or a @limiter.limit decorator; the
codebase had neither. Measured: 80 requests to a public route, 70 to login,
zero 429s. These tests exist so that the next time the limiter is quietly
disconnected, something red says so.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.ratelimit import limiter
from app.core.security import create_agent_verify_token
from app.main import app

engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
LIMIT = settings.AUTH_RATE_LIMIT_PER_MINUTE


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(engine)

    def override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _fresh_bucket():
    # Every test starts with an empty window; the limiter keys on client
    # address and TestClient always presents the same one.
    limiter.reset()
    yield
    limiter.reset()


def _hammer(client, method, url, n, **kw):
    return [getattr(client, method)(url, **kw).status_code for _ in range(n)]


def test_verify_agent_is_limited_per_client_and_the_limit_is_the_declared_one(client):
    url = f"/api/v1/verify-agent?token={create_agent_verify_token(str(uuid.uuid4()))}"
    codes = _hammer(client, "get", url, LIMIT + 3)
    assert codes[:LIMIT] == [404] * LIMIT           # under the limit: the normal answer
    assert codes[LIMIT:] == [429] * 3               # over it: refused before the handler runs
    assert LIMIT == 10, "AUTH_RATE_LIMIT_PER_MINUTE moved; update CLAUDE.md with it"


def test_login_is_limited_at_the_same_rate(client):
    body = {"email": "nobody@t.io", "password": "wrong-password", "device_id": "device-000001"}
    codes = _hammer(client, "post", "/api/v1/auth/login", LIMIT + 2, json=body)
    assert all(c == 401 for c in codes[:LIMIT]), codes  # bad credentials, not a limit
    assert codes[LIMIT:] == [429, 429]


def test_quick_login_is_limited_too(client):
    codes = _hammer(client, "post", "/api/v1/auth/quick-login", LIMIT + 1,
                    json={"token": "not-a-real-token", "device_id": "device-000001"})
    assert 429 in codes and codes[-1] == 429


def test_the_429_carries_the_standard_body(client):
    url = f"/api/v1/verify-agent?token={create_agent_verify_token(str(uuid.uuid4()))}"
    _hammer(client, "get", url, LIMIT)
    r = client.get(url)
    assert r.status_code == 429
    assert "Rate limit exceeded" in r.json().get("error", "") or "limit" in r.text.lower()


def test_authenticated_routes_are_not_globally_limited(client):
    """The global default is deliberately NOT enforced: the manager overview
    issues a dozen requests on load and the live map polls, and one office
    NAT is one client address. A route with no decorator must be able to
    take more than the auth limit without a 429 — here, unauthenticated
    calls to a protected route return 401 sixty times, never 429."""
    codes = _hammer(client, "get", "/api/v1/manager/dashboard", settings.RATE_LIMIT_PER_MINUTE + 5)
    assert 429 not in codes
    assert set(codes) <= {401, 403}
