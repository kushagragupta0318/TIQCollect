"""Two limiter instances, one Redis: the limit is shared (RESTRUCTURE-PLAN 1.8).

Needs a throwaway Redis named by TIQ_REDIS_TEST_URL (use a spare db index, e.g.
redis://host:6379/15: the test clears the limiter's keys there). Unset, it skips,
as tests/pg does without TIQ_PG_TEST_URL. CI runs it against a service container.
"""
from __future__ import annotations

import os

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.ratelimit import AUTH_LIMIT, build_limiter, storage_state

URL = os.environ.get("TIQ_REDIS_TEST_URL", "")
pytestmark = pytest.mark.skipif(not URL or "${" in URL,
                                reason="TIQ_REDIS_TEST_URL not set: the shared-Redis limiter test is skipped")
LIMIT = settings.AUTH_RATE_LIMIT_PER_MINUTE


def _instance():
    """One API process: its own Limiter object and app, as a uvicorn worker has."""
    lim = build_limiter(URL)
    app = FastAPI()
    app.state.limiter = lim
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @app.post("/login")
    @lim.limit(AUTH_LIMIT)
    def login(request: Request):
        return {"ok": True}

    return lim, app


def test_two_instances_share_one_bucket():
    lim_a, app_a = _instance()
    lim_b, app_b = _instance()
    lim_a.reset()
    try:
        with TestClient(app_a) as a, TestClient(app_b) as b:
            spent = [a.post("/login").status_code for _ in range(LIMIT)]
            assert spent == [200] * LIMIT
            assert b.post("/login").status_code == 429       # B never sent one before
        assert storage_state(lim_a) == storage_state(lim_b) == "redis"
    finally:
        lim_a.reset()
