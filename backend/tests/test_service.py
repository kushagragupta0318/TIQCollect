"""Strategy orchestration: service.run_simulation (no DB).

The two loaders (read_transitions, read_panel) are monkeypatched to canned
readings so the REAL engine runs on them — this tests the wiring, the portfolio
built from the panel's last column, the n_paths cap, the honesty stamp and the
basis, without a database. The loaders' own SQL is covered in tests/pg.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from app.core.errors import AppException, ErrorCode
from app.strategy import service as S
from app.strategy.backtest import HistoricalPanel
from app.strategy.history import PanelReading
from app.strategy.honesty import STAMP_FIELDS
from app.strategy.monte_carlo import SegmentMatrices
from app.strategy.states import CURRENT, N_STATES, SMA_0
from app.strategy.transitions import TransitionReading


def _months(n: int) -> tuple[date, ...]:
    return tuple(date(2026, m, 28) for m in range(1, n + 1))


def _canned(monkeypatch, n_loans=40, n_months=8):
    months = _months(n_months)
    counts = np.zeros((1, N_STATES, N_STATES))
    counts[0, CURRENT, CURRENT], counts[0, CURRENT, SMA_0] = 900.0, 100.0
    counts[0, SMA_0, CURRENT], counts[0, SMA_0, SMA_0] = 300.0, 700.0
    matrices = SegmentMatrices(("PERSONAL|WEST",), counts, loan_types=("PERSONAL",),
                               synthetic=True, source="canned")
    tr = TransitionReading(matrices=matrices, segments=(), month_ends=months)

    state = np.zeros((n_loans, n_months), dtype=np.int8)      # everyone CURRENT, observed
    balance = np.full((n_loans, n_months), 100000.0)
    panel = HistoricalPanel(state=state, balance=balance, segment=np.zeros(n_loans, dtype=np.int32),
                            segment_keys=("PERSONAL|WEST",), segment_loan_types=("PERSONAL",), synthetic=True)
    pr = PanelReading(panel=panel, month_ends=months, loans=n_loans)

    monkeypatch.setattr(S, "read_transitions", lambda *a, **k: tr)
    monkeypatch.setattr(S, "read_panel", lambda *a, **k: pr)
    return months


def test_resolve_scenario_and_levers():
    assert S.SimulationInputs(preset="adverse").resolve_scenario().name == "Adverse"
    custom = S.SimulationInputs(preset="custom", scenario={"gdp": -2.0, "bogus": 9}).resolve_scenario()
    assert custom.gdp == -2.0 and custom.name == "Custom"          # unknown macro field ignored
    with pytest.raises(AppException) as e:
        S.SimulationInputs(preset="nope").resolve_scenario()
    assert e.value.code is ErrorCode.VALIDATION_ERROR
    assert S.SimulationInputs(levers={"placement_rate": 0.8}).resolve_levers().placement_rate == 0.8
    with pytest.raises(AppException):
        S.SimulationInputs(levers={"not_a_lever": 1}).resolve_levers()


def test_run_simulation_wires_the_engine_and_stamps_it_synthetic(monkeypatch):
    months = _canned(monkeypatch)
    run = S.run_simulation(None, None, "bank-1", S.SimulationInputs(horizon_months=3, n_paths=20, seed=1))
    assert run.result.horizon_months == 3 and run.result.synthetic_inputs is True
    assert run.stamp.synthetic is True and run.segments == 1
    assert run.month_ends == months
    h = run.headline()
    assert set(h) >= STAMP_FIELDS and "SYNTHETIC:" in h["text"]
    assert "loans at" in h["basis"] and "under 'Baseline'" in h["basis"]


def test_n_paths_is_capped(monkeypatch):
    _canned(monkeypatch)
    run = S.run_simulation(None, None, "bank-1",
                           S.SimulationInputs(horizon_months=2, n_paths=10_000, seed=1))
    assert run.result.n_paths == S.N_PATHS_CAP


def test_a_book_with_no_current_snapshot_abstains(monkeypatch):
    _canned(monkeypatch, n_loans=5, n_months=8)
    # blank the last column so no loan is observed at the origin
    import app.strategy.service as svc
    pr = svc.read_panel(None, "bank-1")
    pr.panel.state[:, -1] = -1
    with pytest.raises(AppException) as e:
        S.run_simulation(None, None, "bank-1", S.SimulationInputs(horizon_months=2, n_paths=10))
    assert e.value.code is ErrorCode.INSUFFICIENT_HISTORY
