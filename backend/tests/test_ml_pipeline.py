"""Tests for the model-development pipeline and the serving seam.

WHAT THESE ARE FOR. Almost nothing in an ML pipeline fails loudly. A flipped WOE
sign, a feature the simulator and the live adapter compute differently, a
scorecard served without the transformations it was fitted with — every one of
them returns a plausible probability for every borrower and is wrong about all
of them. So the tests here concentrate on the CONTRACTS that produce a confident
wrong answer, not on the arithmetic that produces an exception.

No database and no network, matching the rest of the suite.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from app.ml.pipeline import evaluate as ev
from app.ml.pipeline.binning import WOEBinner
from app.ml.pipeline.config import RECOVERY_RISK, REGISTRY
from app.ml.pipeline.preprocess import ColumnSubset, Preprocessor
from app.ml.pipeline.scorecard import ScoreCard, band_for
from app.ml.pipeline.selection import compute_vif
from app.ml.simulation.book_simulator import (
    SECURED_TYPES, BookSimulator, SimConfig, dpd_bucket_of,
)


# ---------------------------------------------------------------------------
# The one-definition exceptions, asserted rather than trusted
# ---------------------------------------------------------------------------

def test_simulator_dpd_bucket_matches_the_models_definition():
    """book_simulator restates the DPD->bucket rule because it must not import
    `app` (it runs with no database). That is a deliberate exception to the
    one-definition rule, so the two must be proven equal rather than assumed —
    this repo has already found SEVEN copies of this rule, two of which
    disagreed with the enum they claimed to implement."""
    from app.models.loan import dpd_bucket_for

    for dpd in range(0, 401):
        assert dpd_bucket_of(dpd) == dpd_bucket_for(dpd).value, f"disagree at dpd={dpd}"


def test_simulator_secured_types_match_eligibility():
    """Same exception, same reasoning: the simulator keeps its own SECURED set."""
    from app.ml.eligibility import _SECURED

    assert SECURED_TYPES == {t.value for t in _SECURED}


def test_scoring_adapter_imports_the_canonical_secured_set():
    """The live adapter must not carry a third copy."""
    from app.ml.eligibility import _SECURED
    from app.services.ml_scoring_service import SECURED_LOAN_TYPES

    assert SECURED_LOAN_TYPES is _SECURED


# ---------------------------------------------------------------------------
# WOE sign convention — a flipped sign is silent and total
# ---------------------------------------------------------------------------

def test_woe_is_high_for_low_risk():
    """WOE here is ln(good/bad), so a bin full of bads must score NEGATIVE.

    Everything downstream depends on this: the sign check in selection drops
    any feature with a positive coefficient, and the scorecard subtracts
    factor * beta * WOE. Flip the convention and the model is exactly as
    accurate and completely backwards.
    """
    rng = np.random.default_rng(0)
    x = rng.uniform(0, 100, 4000)
    y = (rng.random(4000) < (x / 130.0)).astype(int)   # risk rises with x
    X = pd.DataFrame({"x": x})

    b = WOEBinner(["x"], [], min_bin_events=20).fit(X, y)
    table = b.tables_["x"]
    body = table[~table["Bin"].astype(str).isin(("Special", "Missing", ""))]
    rates = pd.to_numeric(body["Event rate"], errors="coerce")
    woes = pd.to_numeric(body["WoE"], errors="coerce")

    # The riskiest bin must carry the most negative WOE.
    assert woes.iloc[rates.idxmax() - body.index[0]] == pytest.approx(woes.min(), abs=1e-9)
    assert np.corrcoef(rates, woes)[0, 1] < -0.9


def test_binner_gives_missing_its_own_bin_and_never_imputes():
    rng = np.random.default_rng(1)
    x = rng.uniform(0, 100, 3000)
    x[::5] = np.nan                       # 20% missing
    y = (rng.random(3000) < 0.4).astype(int)
    X = pd.DataFrame({"x": x})

    pre = Preprocessor(["x"], []).fit(X)
    Xt = pre.transform(X)
    assert Xt["x"].isna().sum() == 600, "preprocessing must not fill missing"

    b = WOEBinner(["x"], [], min_bin_events=20).fit(Xt, y)
    labels = b.tables_["x"]["Bin"].astype(str).tolist()
    assert "Missing" in labels
    assert b.transform(Xt).notna().all().all(), "every row must map to some WOE"


# ---------------------------------------------------------------------------
# Evaluation primitives
# ---------------------------------------------------------------------------

def test_gini_and_ks_on_a_known_separation():
    y = np.r_[np.ones(500), np.zeros(500)].astype(int)
    perfect = np.r_[np.ones(500), np.zeros(500)]
    assert ev.gini(y, perfect) == pytest.approx(1.0)
    assert ev.ks_statistic(y, perfect)[0] == pytest.approx(100.0)

    rng = np.random.default_rng(3)
    assert abs(ev.gini(y, rng.random(1000))) < 0.12


def test_gini_is_nan_not_zero_on_a_single_class():
    """0.0 would read as 'a useless model'; nan reads as 'not computable'."""
    assert np.isnan(ev.gini(np.ones(50, dtype=int), np.random.random(50)))


def test_decile_table_is_descending_and_detects_rank_order_breaks():
    rng = np.random.default_rng(4)
    n = 5000
    score = rng.random(n)
    y = (rng.random(n) < score).astype(int)      # risk rises with score

    dt = ev.decile_table(y, score)
    assert list(dt.decile) == list(range(1, 11))
    assert dt.bad_rate.iloc[0] > dt.bad_rate.iloc[-1], "decile 1 must be riskiest"
    assert ev.rank_order_breaks(dt)["n_breaks"] == 0

    # A deliberately misordered table must be caught.
    broken = dt.copy()
    broken.loc[broken.index[3], "bad_rate"] = broken.bad_rate.iloc[2] + 0.1
    ro = ev.rank_order_breaks(broken)
    assert ro["n_breaks"] >= 1 and not ro["monotonic"]


def test_psi_is_zero_for_identical_populations_and_large_for_a_shift():
    rng = np.random.default_rng(5)
    a = rng.normal(0, 1, 20000)
    assert ev.psi(a, rng.normal(0, 1, 20000)) < 0.02
    assert ev.psi(a, rng.normal(2.5, 1, 20000)) > 0.25


def test_vif_flags_a_constructed_collinearity():
    rng = np.random.default_rng(6)
    a = rng.normal(size=2000)
    b = rng.normal(size=2000)
    X = pd.DataFrame({"a": a, "b": b, "c": a + b + rng.normal(0, .01, 2000)})
    v = compute_vif(X)
    assert v["c"] > 5.0
    assert compute_vif(X[["a", "b"]]).max() < 2.0


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------

def test_points_are_inverse_to_risk_and_sum_to_the_total():
    card = ScoreCard(["f1_woe", "f2_woe"], np.array([-0.8, -0.5]), 0.4)
    W = pd.DataFrame({"f1_woe": [1.5, -1.5], "f2_woe": [1.0, -1.0]})
    pf = card.points_frame(W)
    assert (pf.sum(axis=1) == card.score(W)).all()
    # Higher WOE (safer borrower) must score MORE points.
    assert card.score(W).iloc[0] > card.score(W).iloc[1]


def test_fitted_bands_span_the_actual_distribution():
    """Fixed cutoffs put the whole book in one band once; bands are fitted now."""
    rng = np.random.default_rng(7)
    card = ScoreCard(["f_woe"], np.array([-0.9]), 0.2)
    W = pd.DataFrame({"f_woe": rng.normal(0, 1, 5000)})
    card.fit_bands(W, (rng.random(5000) < 0.5).astype(int))
    bands = {band_for(p, card.bands) for p in card.score(W)}
    assert len(bands) >= 4, f"bands collapsed to {bands}"


# ---------------------------------------------------------------------------
# The simulator's own properties
# ---------------------------------------------------------------------------

def test_simulated_book_is_point_in_time_and_well_shaped():
    panel = BookSimulator(SimConfig(n_borrowers=400, months=12, seed=11)).run()

    assert {"y", "as_of_date", "month_index", "loan_id"} <= set(panel.columns)
    # No latent may reach the panel — they are the answer, not a feature.
    assert not [c for c in panel.columns if c.startswith("_")]

    # The DPD gradient is the thing a scorecard exists to find; it must be
    # monotonic. It was not, twice: once because payment size scaled with
    # arrears, once because a cured account with nothing due scored as "did not
    # recover".
    order = ["CURRENT", "BUCKET_1", "BUCKET_2", "BUCKET_3", "NPA"]
    rates = panel.groupby("dpd_bucket").y.mean().reindex(order).dropna()
    assert (rates.diff().dropna() > 0).all(), f"non-monotonic DPD gradient: {rates.to_dict()}"

    # The book must not absorb into one bucket, or DPD looks far stronger than
    # it is and the binner has nothing to bin at the low end.
    assert panel.dpd_bucket.value_counts(normalize=True).max() < 0.55


def test_simulator_is_deterministic_for_a_seed():
    a = BookSimulator(SimConfig(n_borrowers=200, months=6, seed=99)).run()
    b = BookSimulator(SimConfig(n_borrowers=200, months=6, seed=99)).run()
    pd.testing.assert_frame_equal(a, b)


def test_difficulty_dial_actually_lowers_achievable_discrimination():
    """The whole point of signal_scale: a synthetic book that yields Gini 0.95
    is not a harder version of a real one, it is a different problem."""
    from sklearn.linear_model import LogisticRegression

    def gini_at(signal, noise):
        cfg = SimConfig(n_borrowers=600, months=12, seed=5,
                        signal_scale=signal, observation_noise=noise)
        p = BookSimulator(cfg).run()
        feats = ["dpd", "arrears_ratio", "paid_ratio_6m", "ptp_kept_ratio"]
        tr, te = p[p.month_index < 9], p[p.month_index >= 9]
        m = LogisticRegression(max_iter=500).fit(tr[feats].fillna(0), tr.y)
        return ev.gini(te.y, m.predict_proba(te[feats].fillna(0))[:, 1])

    assert gini_at(1.0, 1.0) > gini_at(0.5, 2.0) + 0.05


# ---------------------------------------------------------------------------
# Artifacts and the serving seam
# ---------------------------------------------------------------------------

def _artifact(model="recovery_risk"):
    from app.ml.pipeline import registry
    try:
        return registry.version_dir(model, registry.resolve_version(model))
    except FileNotFoundError:
        return None


champion_required = pytest.mark.skipif(
    _artifact() is None,
    reason="no champion artifact; run scripts/train_models.py",
)


@champion_required
def test_champion_artifact_passed_its_gates():
    """A champion pointer must never name a model that failed.

    It did once: contact_risk passed at Gini 0.271, was demoted to 0.190 when
    its features were restricted to what the product can serve, and still
    reported is_champion True because the pointer was only ever written, never
    removed.
    """
    meta = json.loads((_artifact() / "metadata.json").read_text())
    assert meta["gate_summary"] == "PASS"
    assert not any(g["result"] == "FAIL" for g in meta["gates"])


@champion_required
def test_champion_gini_is_in_a_believable_band():
    meta = json.loads((_artifact() / "metadata.json").read_text())
    g = meta["metrics"]["oot"]["gini"]
    gates = meta["spec"]["gates"]
    assert gates["gini_min"] <= g < gates["gini_suspicious"], (
        f"Gini {g} outside the believable band — above the ceiling means a leak, "
        f"not a triumph")


@champion_required
def test_engine_scores_and_reports_is_modelled():
    """On the WOE scorecard artifact (1.1.0) by explicit version: this test
    describes the four-input card, which stopped being the champion on
    2026-09-16. The GAM champion has its own suite (test_gam_serving.py)."""
    from app.ml.pipeline.engine import DecisionEngine

    e = DecisionEngine.get("recovery_risk", "1.1.0")
    assert e is not None
    feats = {f: v for f, v in
             [("dpd", 15), ("cibil_score", 780), ("ptp_kept_ratio", 0.9),
              ("overdue_amount", 4000)]}
    r = e.score(feats)
    assert r.is_modelled and 0.0 <= r.probability <= 1.0
    assert r.feature_coverage == pytest.approx(1.0)


@champion_required
def test_engine_declines_rather_than_guessing_on_sparse_input():
    """A scorecard with two of four inputs is the intercept plus noise. Serving
    that with a plausible-looking probability is worse than serving nothing."""
    from app.ml.pipeline.engine import DecisionEngine

    r = DecisionEngine.get("recovery_risk").score({"dpd": 90})
    assert r.is_modelled is False
    assert r.probability is None
    assert "features were supplied" in r.fallback_reason


@champion_required
def test_riskier_inputs_score_worse():
    from app.ml.pipeline.engine import DecisionEngine

    e = DecisionEngine.get("recovery_risk", "1.1.0")       # the four-input card
    safe = e.score({"dpd": 0, "cibil_score": 800, "ptp_kept_ratio": 1.0,
                    "overdue_amount": 1000})
    risky = e.score({"dpd": 240, "cibil_score": 480, "ptp_kept_ratio": 0.0,
                     "overdue_amount": 90000})
    assert risky.probability > safe.probability
    assert risky.points < safe.points


@champion_required
def test_artifact_checksum_is_verified_on_load(tmp_path):
    """joblib.load executes code, so a modified artifact must be refused."""
    import shutil

    from app.ml.pipeline import registry

    src = _artifact()
    monkey = tmp_path / "recovery_risk" / "1.0.0"
    shutil.copytree(src, monkey)
    (monkey / "model.joblib").write_bytes(b"tampered")

    original_root = registry.ARTIFACT_ROOT
    try:
        registry.ARTIFACT_ROOT = tmp_path
        with pytest.raises(ValueError, match="checksum mismatch"):
            registry.load("recovery_risk", "1.0.0")
    finally:
        registry.ARTIFACT_ROOT = original_root


@champion_required
def test_pipeline_carries_its_own_preprocessing_and_feature_set():
    """The saved bundle must be applicable without the caller reconstructing
    anything — that is the point of pickling a Pipeline rather than an
    estimator."""
    from app.ml.pipeline import registry

    pipe, _ = registry.load("recovery_risk", "1.1.0")     # the scorecard bundle; a GAM is a GamModel
    names = [s[0] for s in pipe.steps]
    assert names == ["preprocess", "woe", "select", "model"]
    assert isinstance(pipe.named_steps["select"], ColumnSubset)


def test_column_subset_refuses_a_frame_missing_fitted_columns():
    cs = ColumnSubset(["a_woe", "b_woe"])
    with pytest.raises(KeyError, match="missing fitted columns"):
        cs.transform(pd.DataFrame({"a_woe": [1.0]}))


# ---------------------------------------------------------------------------
# The training/serving contract
# ---------------------------------------------------------------------------

def test_every_spec_feature_is_produced_by_the_simulator():
    """A feature in a spec that the panel does not carry trains on nothing."""
    panel = BookSimulator(SimConfig(n_borrowers=120, months=4, seed=2)).run()
    for spec in REGISTRY.values():
        missing = [f for f in spec.all_features if f not in panel.columns]
        assert not missing, f"{spec.name}: spec names features the panel lacks: {missing}"


def test_no_spec_uses_a_forbidden_column():
    """The same ban repayment_service._FORBIDDEN_FEATURE_KEYS raises on."""
    for spec in REGISTRY.values():
        assert not set(spec.all_features) & set(spec.forbidden)


# test_selected_features_are_all_servable_by_the_live_adapter USED TO LIVE HERE.
# It grepped ml_scoring_service.py for each feature name and passed whenever the
# string appeared anywhere in the file. That is not proof of anything: the same
# class of check passed while `PlanInProgressError` sat in the wrong function,
# and it would have passed just as happily while build_features raised
# TypeError on the first row. Deleted rather than weakened. The real proof is
# tests/test_ml_scoring_adapter.py::test_adapter_supplies_every_feature_the_
# champion_selected, which builds a loan and calls the adapter.
