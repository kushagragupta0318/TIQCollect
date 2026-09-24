"""The missingness monitor — the drift PSI cannot see, 2026-09-16.

`evaluate.psi` drops NaN on both sides and is deliberately unchanged. The
production-readiness audit measured `days_since_last_contact` at 49.2%
missing on the reference GAM's train months and 37.2% on its out-of-time
months, with PSI 0.0216 ("stable"). That case is reproduced here on
synthetic data with the SAME non-missing distribution on both sides, so PSI
has nothing to see and the monitor must still fire; the same shape runs
through `monitor_model` on logged predictions to prove the wiring.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ml.pipeline import evaluate as ev
from app.ml.pipeline import monitor as mon
from app.ml.pipeline.missingness import (ACTION_ON_BREACH, MISSINGNESS_ABS_FLOOR,
                                         MISSINGNESS_ABS_THRESHOLD, MISSINGNESS_REL_THRESHOLD,
                                         breached_features, missingness_report, rates)
from app.ml.pipeline.outcomes import OUTCOME_DEFINITION_VERSION, OutcomeStatus
from app.models.base import Base
from app.models.model_prediction import ModelPrediction
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

BASELINE_RATE, CURRENT_RATE = 0.492, 0.372          # the audited case


def _column(n: int, missing_rate: float, seed: int) -> np.ndarray:
    """Identical non-missing distribution on every call; only the NaN share
    differs, so PSI (which drops NaN) sees nothing."""
    rng = np.random.default_rng(seed)
    x = rng.integers(1, 120, size=n).astype(float)
    x[rng.random(n) < missing_rate] = np.nan
    return x


@pytest.fixture
def frames():
    b = pd.DataFrame({"days_since_last_contact": _column(50_000, BASELINE_RATE, 1),
                      "overdue_amount": np.random.default_rng(3).normal(size=50_000)})
    c = pd.DataFrame({"days_since_last_contact": _column(20_000, CURRENT_RATE, 2),
                      "overdue_amount": np.random.default_rng(4).normal(size=20_000)})
    return b, c


# ---------------------------------------------------------------------------
# the audited case: PSI blind, the monitor sees it
# ---------------------------------------------------------------------------

def test_psi_is_blind_to_the_audited_case_and_the_monitor_is_not(frames):
    b, c = frames
    psi = ev.psi(b.days_since_last_contact, c.days_since_last_contact)
    assert psi < 0.05, "PSI would have caught this on its own; the fixture is wrong"
    rows = missingness_report(b, c, ["days_since_last_contact", "overdue_amount"])
    r = {x["feature"]: x for x in rows}["days_since_last_contact"]
    assert abs(r["baseline_absent_rate"] - BASELINE_RATE) < 0.01
    assert abs(r["current_absent_rate"] - CURRENT_RATE) < 0.01
    assert r["abs_delta"] < -0.10 and r["rel_delta"] < -0.20
    assert r["breached"] is True and r["action"] == ACTION_ON_BREACH
    assert r["threshold_abs"] == MISSINGNESS_ABS_THRESHOLD and r["threshold_rel"] == MISSINGNESS_REL_THRESHOLD
    assert breached_features(rows) == ["days_since_last_contact"]
    o = {x["feature"]: x for x in rows}["overdue_amount"]
    assert o["breached"] is False and o["action"] == "—" and o["baseline_absent_rate"] == 0.0


def test_every_required_field_is_reported(frames):
    b, c = frames
    r = missingness_report(b, c, ["days_since_last_contact"])[0]
    for k in ("feature", "n_baseline", "n_current", "baseline_null_rate", "current_null_rate",
              "baseline_absent_rate", "current_absent_rate", "abs_delta", "rel_delta",
              "threshold_abs", "threshold_rel", "breached", "action"):
        assert k in r, k


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------

def test_the_absolute_threshold_fires_on_its_own():
    b = pd.DataFrame({"f": _column(10_000, 0.30, 5)})
    c = pd.DataFrame({"f": _column(10_000, 0.30 + MISSINGNESS_ABS_THRESHOLD + 0.03, 6)})
    assert missingness_report(b, c, ["f"])[0]["breached"]


def test_the_relative_threshold_needs_the_absolute_floor():
    """1% -> 1.4% is +40% relative and must NOT fire; 8% -> 11% (+37%) must."""
    b = pd.DataFrame({"f": _column(40_000, 0.010, 7)})
    c = pd.DataFrame({"f": _column(40_000, 0.014, 8)})
    r = missingness_report(b, c, ["f"])[0]
    assert abs(r["abs_delta"]) < MISSINGNESS_ABS_FLOOR and not r["breached"]
    b = pd.DataFrame({"f": _column(40_000, 0.08, 9)})
    c = pd.DataFrame({"f": _column(40_000, 0.11, 10)})
    r = missingness_report(b, c, ["f"])[0]
    assert r["rel_delta"] > MISSINGNESS_REL_THRESHOLD and r["breached"]


def test_a_stable_feature_does_not_fire():
    b = pd.DataFrame({"f": _column(30_000, 0.40, 11)})
    c = pd.DataFrame({"f": _column(30_000, 0.40, 12)})
    assert not missingness_report(b, c, ["f"])[0]["breached"]


def test_the_direction_is_symmetric():
    """More missing is as much a finding as less: the shape function's
    missing route is applied to a different share of the book either way."""
    b = pd.DataFrame({"f": _column(30_000, 0.20, 13)})
    c = pd.DataFrame({"f": _column(30_000, 0.40, 14)})
    assert missingness_report(b, c, ["f"])[0]["breached"]
    assert missingness_report(c, b, ["f"])[0]["breached"]


def test_none_is_an_absence_for_a_categorical_and_null_is_counted_separately():
    """A product whose field process has not started capturing dispositions
    reads 100% NONE and 0% null: the absent rate sees it, the null rate alone
    would not."""
    b = pd.DataFrame({"latest_disposition": ["WILL_PAY"] * 60 + ["NONE"] * 40})
    c = pd.DataFrame({"latest_disposition": ["NONE"] * 100})
    r = missingness_report(b, c, ["latest_disposition"])[0]
    assert r["baseline_null_rate"] == 0.0 and r["current_null_rate"] == 0.0
    assert r["baseline_absent_rate"] == 0.4 and r["current_absent_rate"] == 1.0
    assert r["breached"]
    null_rate, absent_rate, n = rates([None, "NONE", "WILL_PAY", float("nan")])
    assert (null_rate, absent_rate, n) == (0.5, 0.75, 4)


def test_a_column_that_vanished_is_the_loudest_breach():
    b = pd.DataFrame({"f": _column(100, 0.1, 15)})
    c = pd.DataFrame({"g": _column(100, 0.1, 16)})
    r = missingness_report(b, c, ["f"])[0]
    assert r["breached"] and "absent from the current frame" in r["notes"][0]


def test_too_few_rows_is_not_compared():
    b = pd.DataFrame({"f": _column(30, 0.1, 17)})
    c = pd.DataFrame({"f": _column(30, 0.9, 18)})
    r = missingness_report(b, c, ["f"])[0]
    assert not r["breached"] and "not compared" in r["notes"][0]


def test_the_action_says_what_to_do_and_what_not_to_trust():
    assert "PSI" in ACTION_ON_BREACH and "retrain" in ACTION_ON_BREACH.lower()
    assert "source" in ACTION_ON_BREACH.lower()


def test_evaluate_psi_is_unchanged_by_this_module():
    """The monitor exists BESIDE psi; psi's NaN behaviour is pinned elsewhere
    and this asserts the module does not touch it."""
    import inspect
    from app.ml.pipeline import missingness
    assert "evaluate" not in inspect.getsource(missingness).replace("evaluate.psi` drops", "")


# ---------------------------------------------------------------------------
# wired into monitor_model
# ---------------------------------------------------------------------------

engine = make_engine()
Session = make_session_factory(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db():
    create_schema(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        drop_schema(bind=engine)


def _pred(db, *, case, as_of, prob, outcome, contact):
    row = ModelPrediction(
        id=str(uuid.uuid4()), model_name="recovery_risk", model_version="1.1.0",
        entity_type="case", entity_id=test_id(f"case:{case}"), case_id=None,  # v2: entity_id is a UUID; no case row exists
        as_of_date=as_of,
        probability=prob, is_modelled=True, feature_coverage=1.0,
        features={"dpd": 40.0 + prob * 100, "cibil_score": 600.0, "ptp_kept_ratio": 0.5,
                  "overdue_amount": 5000.0, "days_since_last_contact": contact},
        outcome_baseline={"overdue_amount": 5000.0, "emi_amount": 2500.0, "threshold_ratio": 0.8},
        actual_outcome=outcome, outcome_definition_version=OUTCOME_DEFINITION_VERSION,
        outcome_status=(OutcomeStatus.NOT_RECOVERED.value if outcome else OutcomeStatus.RECOVERED.value),
    )
    db.add(row)


def _calibrated_cohort(db, *, start: date, n: int, contact_missing: float, seed: int):
    rng = np.random.default_rng(seed)
    buckets: dict[float, list[int]] = {}
    for i in range(n):
        buckets.setdefault((i % 100) / 100.0, []).append(i)
    for p, idx in buckets.items():
        n_bad = int(round(p * len(idx)))
        for j, i in enumerate(idx):
            contact = None if rng.random() < contact_missing else float(rng.integers(1, 120))
            _pred(db, case=f"{start.isoformat()}-c{i}", as_of=start + timedelta(days=i % 20),
                  prob=p, outcome=1 if j < n_bad else 0, contact=contact)
    db.commit()


def test_monitor_model_reports_missingness_beside_psi_and_fires_on_the_audited_case(db, monkeypatch):
    """Same score distribution, same outcomes, same non-missing contact
    distribution in both halves of the window; only the missing share moves
    49% -> 37%. PSI stays quiet, the missingness monitor does not."""
    monkeypatch.setattr(mon.DecisionEngine, "get", classmethod(lambda cls, m, v=None: _FakeEngine()))
    today = date.today()
    _calibrated_cohort(db, start=today - timedelta(days=120), n=900, contact_missing=BASELINE_RATE, seed=1)
    _calibrated_cohort(db, start=today - timedelta(days=60), n=900, contact_missing=CURRENT_RATE, seed=2)
    rep = mon.monitor_model(db, "recovery_risk", version="1.1.0", lookback_days=10_000)
    miss = {r["feature"]: r for r in rep.stability["missingness"]}
    assert "days_since_last_contact" in miss and miss["days_since_last_contact"]["breached"]
    assert rep.retrain_recommended and rep.verdict == "retrain_recommended"
    assert any("NO observation" in r and "days_since_last_contact" in r for r in rep.reasons), rep.reasons
    psi = {r["feature"]: r["psi"] for r in rep.stability["per_feature"]}
    assert psi["days_since_last_contact"] < 0.10


def test_monitor_model_stays_quiet_when_missingness_is_stable(db, monkeypatch):
    monkeypatch.setattr(mon.DecisionEngine, "get", classmethod(lambda cls, m, v=None: _FakeEngine()))
    today = date.today()
    _calibrated_cohort(db, start=today - timedelta(days=120), n=900, contact_missing=0.45, seed=3)
    _calibrated_cohort(db, start=today - timedelta(days=60), n=900, contact_missing=0.45, seed=4)
    rep = mon.monitor_model(db, "recovery_risk", version="1.1.0", lookback_days=10_000)
    assert not any(r["breached"] for r in rep.stability["missingness"])
    assert not any("NO observation" in r for r in rep.reasons)


class _FakeEngine:
    """Just enough of a DecisionEngine for the stability block: a selected
    feature list that includes the one whose missingness moves."""
    selected = ["dpd", "cibil_score", "ptp_kept_ratio", "overdue_amount", "days_since_last_contact"]
    version = "1.1.0"
    metadata = {"metrics": {"oot": {"gini": 0.51, "ks": 39.7, "brier": 0.17, "bad_rate": 0.7}}}
