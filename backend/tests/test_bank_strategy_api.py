"""POST /api/v1/bank/strategy/simulate (E05 fan-chart addition, 2026-10-06):
the opt-in `series` field returns the full monthly bands instead of just the
horizon value `headline()` already returned. The engine itself (run_simulation,
the Monte Carlo core) is covered in tests/test_service.py and
tests/test_calibration.py — this is the thin endpoint layer: request shape,
the capability gate, and that `series` is additive (off by default, and off
never changes the existing response).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.dependencies import get_tenant_analytics_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.user import User, UserRole
from app.strategy import service as S
from app.strategy.monte_carlo import SegmentMatrices
from app.strategy.states import CURRENT, N_STATES, SMA_0
from app.strategy.history import PanelReading
from app.strategy.backtest import HistoricalPanel
from app.strategy.transitions import TransitionReading
from tests._db import TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

BASE = "/api/v1/bank/strategy/simulate"


def _canned(monkeypatch, n_loans=40, n_months=8):
    """Same canned engine inputs as test_service.py's _canned(), so the REAL
    engine runs — this test is about the endpoint's shape, not a second copy
    of the engine's own test data."""
    months = tuple(date(2026, m, 28) for m in range(1, n_months + 1))
    counts = np.zeros((1, N_STATES, N_STATES))
    counts[0, CURRENT, CURRENT], counts[0, CURRENT, SMA_0] = 900.0, 100.0
    counts[0, SMA_0, CURRENT], counts[0, SMA_0, SMA_0] = 300.0, 700.0
    matrices = SegmentMatrices(("PERSONAL|WEST",), counts, loan_types=("PERSONAL",),
                               synthetic=True, source="canned")
    tr = TransitionReading(matrices=matrices, segments=(), month_ends=months)

    state = np.zeros((n_loans, n_months), dtype=np.int8)
    balance = np.full((n_loans, n_months), 100000.0)
    panel = HistoricalPanel(state=state, balance=balance, segment=np.zeros(n_loans, dtype=np.int32),
                            segment_keys=("PERSONAL|WEST",), segment_loan_types=("PERSONAL",), synthetic=True)
    pr = PanelReading(panel=panel, month_ends=months, loans=n_loans)

    monkeypatch.setattr(S, "read_transitions", lambda *a, **k: tr)
    monkeypatch.setattr(S, "read_panel", lambda *a, **k: pr)


@pytest.fixture()
def w(monkeypatch):
    _canned(monkeypatch)
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine)
    db = Session()
    admin = User(id=test_id("u:ba"), email="ba@example.test", phone="9800000001",
                full_name="Bank Admin", hashed_password=hash_password("Harbour-Lights-2026"),
                role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID)
    agent_user = User(id=test_id("u:fa"), email="fa@example.test", phone="9800000002",
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
    # The analytics session is never actually queried: read_transitions/
    # read_panel are monkeypatched above, same as test_service.py's direct
    # S.run_simulation(None, None, ...) calls.
    app.dependency_overrides[get_tenant_analytics_db] = lambda: None
    try:
        yield {"c": TestClient(app), "admin": admin, "agent": agent_user}
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_tenant_analytics_db, None)
        db.close()


def _h(user):
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "dev-1")}


def test_series_false_by_default_and_omits_the_key(w):
    r = w["c"].post(BASE, headers=_h(w["admin"]), json={"horizon_months": 3, "n_paths": 20, "seed": 1})
    assert r.status_code == 200, r.text
    assert "series" not in r.json()


def test_series_true_returns_full_monthly_bands_excluding_state_share(w):
    r = w["c"].post(BASE, headers=_h(w["admin"]),
                    json={"horizon_months": 3, "n_paths": 20, "seed": 1, "series": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "series" in body
    assert "STATE_SHARE" not in body["series"]
    net_recovery = body["series"]["NET_RECOVERY"]
    # months 0..horizon_months inclusive = 4 points for horizon_months=3.
    assert len(net_recovery["p50"]) == 4
    assert set(net_recovery) >= {"p5", "p10", "p50", "p90", "p95", "mean", "sem"}
    # The horizon value in the series must agree with headline()'s own figure —
    # two different code paths reading the same Band, not two sources of truth.
    assert net_recovery["p50"][-1] == pytest.approx(body["metrics"]["NET_RECOVERY"]["p50"])


def test_a_field_agent_is_refused(w):
    r = w["c"].post(BASE, headers=_h(w["agent"]), json={"horizon_months": 3, "n_paths": 20, "seed": 1})
    assert r.status_code == 403
