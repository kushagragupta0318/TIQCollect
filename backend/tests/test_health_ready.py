"""The readiness probe answers with its status code, not only its body.

2026-09-24. `/api/v1/ready` returned HTTP 200 with {"status": "degraded"} when the
database or Redis was down. An orchestrator, a load balancer or a Docker
HEALTHCHECK reads the status code, so a broken instance still looked ready.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import health
from app.main import app


class _Redis:
    def __init__(self, up: bool):
        self.up = up

    def ping(self):
        if not self.up:
            raise ConnectionError("redis down (test)")
        return True


@pytest.fixture
def client():
    # No `with`: the probe is under test, not the app's startup hooks.
    return TestClient(app)


@pytest.mark.parametrize("db_up, redis_up, code, status", [
    (True, True, 200, "ready"),
    (False, True, 503, "degraded"),
    (True, False, 503, "degraded"),
    (False, False, 503, "degraded"),
])
def test_ready_answers_with_the_status_code(client, monkeypatch, db_up, redis_up, code, status):
    monkeypatch.setattr(health, "check_db_connection", lambda: db_up)
    monkeypatch.setattr(health.redis_lib, "from_url", lambda *a, **k: _Redis(redis_up))
    r = client.get("/api/v1/ready")
    assert r.status_code == code
    assert r.json()["status"] == status
    assert r.json()["checks"] == {"database": "ok" if db_up else "error",
                                  "redis": "ok" if redis_up else "error"}


def test_liveness_does_not_depend_on_the_database(client, monkeypatch):
    monkeypatch.setattr(health, "check_db_connection", lambda: False)
    assert client.get("/api/v1/health").status_code == 200
