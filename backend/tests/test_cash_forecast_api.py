"""GET /bank/strategy/cash-forecast (E06): the thin endpoint layer only — the
engine itself (Holt fit, bands, reconciliation, backtest) is covered in
test_cash_forecast_engine.py, and its DB reads in test_cash_forecast_reads.py.
This is the request shape, the `strategy.forecast` capability gate, the
honesty stamp travelling with the response, and the 422 abstain surfacing
correctly over HTTP — same convention as test_bank_strategy_api.py.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.dependencies import get_tenant_analytics_db
from app.core.errors import AppException, ErrorCode
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.user import User, UserRole
from app.strategy import cash_forecast as CF
from tests._db import TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

BASE = "/api/v1/bank/strategy/cash-forecast"
AS_OF = date(2026, 10, 7)


def _canned(monkeypatch):
    """A real (deterministic) engine run on canned inputs — the endpoint test
    is about the HTTP layer, not a second copy of the engine's own fixtures."""
    history = CF.WeeklyHistory(
        week_starts=tuple(AS_OF - timedelta(weeks=w) for w in range(CF.MIN_WEEKS_HISTORY, 0, -1)),
        amounts=np.array([5000.0 + 50.0 * i for i in range(CF.MIN_WEEKS_HISTORY)]),
    )
    monkeypatch.setattr(CF, "read_weekly_payments", lambda *a, **k: history)
    monkeypatch.setattr(CF, "read_ptp_schedule",
                        lambda *a, **k: np.zeros(CF.HORIZON_WEEKS, dtype=np.float64))
    monkeypatch.setattr(CF, "read_ptp_honor_rate", lambda *a, **k: (None, 0))
    monkeypatch.setattr(CF, "read_recovery_risk_next_cycle", lambda *a, **k: (0.0, 0))


@pytest.fixture()
def w(monkeypatch):
    _canned(monkeypatch)
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine)
    db = Session()
    admin = User(id=test_id("u:cf-ba"), email="cf-ba@example.test", phone="9800000201",
                full_name="Bank Admin", hashed_password=hash_password("Harbour-Lights-2026"),
                role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID)
    agent_user = User(id=test_id("u:cf-fa"), email="cf-fa@example.test", phone="9800000202",
                      full_name="Field Agent", hashed_password=hash_password("Harbour-Lights-2026"),
                      role=UserRole.FIELD_AGENT, bank_id=None)
    db.add_all([admin, agent_user])
    db.commit()

    def override_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override_db
    # The reads are monkeypatched above, same reasoning as test_bank_strategy_api.py.
    app.dependency_overrides[get_tenant_analytics_db] = lambda: None
    try:
        yield {"c": TestClient(app), "admin": admin, "agent": agent_user}
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_tenant_analytics_db, None)
        db.close()


def _h(user):
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "dev-1")}


def test_the_response_carries_13_weeks_ordered_bands_and_the_honesty_stamp(w):
    r = w["c"].get(BASE, headers=_h(w["admin"]), params={"as_of": AS_OF.isoformat()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["weeks"]) == CF.HORIZON_WEEKS
    assert body["weeks"][0]["week_start"] == AS_OF.isoformat()
    for wk in body["weeks"]:
        assert wk["p10"] <= wk["p50"] <= wk["p90"]
    assert body["synthetic"] is True
    assert "text" in body and body["text"]
    assert body["engine_version"] == CF.ENGINE_VERSION


def test_a_field_agent_is_refused(w):
    r = w["c"].get(BASE, headers=_h(w["agent"]))
    assert r.status_code == 403


def test_insufficient_history_abstains_with_422(w, monkeypatch):
    def _abstain(*a, **k):
        raise AppException(422, ErrorCode.INSUFFICIENT_HISTORY, "no VERIFIED payment history")
    monkeypatch.setattr(CF, "read_weekly_payments", _abstain)
    r = w["c"].get(BASE, headers=_h(w["admin"]))
    assert r.status_code == 422
    assert r.json()["code"] == ErrorCode.INSUFFICIENT_HISTORY.value
