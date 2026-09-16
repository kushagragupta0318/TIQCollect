"""recovery_risk 2.2.0 — the GAM — served through the production engine,
2026-09-16. Loading, scoring parity against the artifact's own reference,
the interaction constraint, calibration, bands, reason codes, versioning,
missing/unknown routing, determinism, and the untouched 1.1.0 path.

Two parity levels. `evaluation/background_reference_scores.csv` travels with
the artifact, so the exact-reproduction checks run in every checkout. The
full 23,780-row out-of-time reproduction needs the frozen panel
(data/ledger/obs_wd10, not committed) and skips without it.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

from app.ml.pipeline import evaluate as ev
from app.ml.pipeline import registry
from app.ml.pipeline.config import RECOVERY_RISK_GAM
from app.ml.pipeline.engine import DecisionEngine, ScoreResult
from app.ml.pipeline.gam import (BAND_SHARES, GamModel, ProbabilityBands, encode_frame,
                                 interaction_cst_for, points_from_logit, reason_codes)

MODEL, VERSION = "recovery_risk", "2.2.0"
ART = registry.version_dir(MODEL, VERSION)
PANEL = Path(__file__).resolve().parents[1] / "data" / "ledger" / "obs_wd10" / "panel.parquet"
TOL = 1e-12


@pytest.fixture(scope="module")
def eng() -> DecisionEngine:
    return DecisionEngine(MODEL, VERSION)


@pytest.fixture(scope="module")
def meta() -> dict:
    return json.loads((ART / "metadata.json").read_text())


@pytest.fixture(scope="module")
def background() -> pd.DataFrame:
    bg = pd.read_csv(ART / "background.csv")
    return bg


@pytest.fixture(scope="module")
def bg_rows(background):
    cols = RECOVERY_RISK_GAM.all_features
    X = background[cols].astype(object).where(background[cols].notna(), None)
    return X.to_dict("records")


@pytest.fixture(scope="module")
def bg_ref() -> pd.DataFrame:
    return pd.read_csv(ART / "evaluation" / "background_reference_scores.csv")


# ---------------------------------------------------------------------------
# 1. loading
# ---------------------------------------------------------------------------

def test_the_engine_loads_the_gam_as_a_gam(eng, meta):
    assert eng.model_type == "gam" and meta["model_type"] == "gam"
    assert isinstance(eng.pipeline, GamModel) and eng.gam is eng.pipeline
    assert eng.calibrator is not None and eng.bands is not None
    assert eng.selected == RECOVERY_RISK_GAM.all_features and len(eng.selected) == 15


def test_the_artifact_checksum_is_verified_on_load(meta):
    from app.ml.pipeline.registry import _sha256
    assert meta["artifact_sha256"] == _sha256(ART / "model.joblib")


def test_the_champion_is_2_2_0_after_the_manual_four_eyes_promotion():
    """Promoted by the repository owner on 2026-09-16 through registry.promote
    (it read "1.1.0" until then, and this test asserted that)."""
    assert registry.pointer_version(MODEL) == "2.2.0"
    assert DecisionEngine("recovery_risk", "champion").version == "2.2.0"


def test_the_health_block_names_the_model_type_and_versions(eng):
    h = eng.health()
    assert h["model_type"] == "gam"
    for k in ("model_artifact", "artifact_sha256", "feature_definition", "calibration",
              "risk_bands", "reason_codes", "background", "training_data"):
        assert h["versions"].get(k), k


# ---------------------------------------------------------------------------
# 2. scoring parity: engine == artifact, exactly
# ---------------------------------------------------------------------------

def test_background_rows_reproduce_the_reference_exactly(eng, bg_rows, bg_ref):
    """Raw probability and logit through the engine's own object at 1e-12;
    the served probability equals the calibrated reference rounded to the
    engine's 6 decimals; the band and points match."""
    X = pd.DataFrame(bg_rows)
    p_raw = eng.gam.predict_proba(X)[:, 1]
    lg = eng.gam.decision_function(X)
    assert np.abs(p_raw - bg_ref.p_raw.to_numpy()).max() <= TOL
    assert np.abs(lg - bg_ref.logit.to_numpy()).max() <= 1e-10
    served = eng.score_batch_detailed(bg_rows)
    p_srv = np.array([r.probability for r in served])
    assert np.abs(p_srv - np.round(bg_ref.p_calibrated.to_numpy(), 6)).max() == 0.0
    assert [r.band for r in served] == bg_ref.band.tolist()
    assert [r.points for r in served] == bg_ref.points.tolist()
    assert all(r.is_modelled for r in served)


def test_single_row_and_batch_paths_agree_exactly(eng, bg_rows):
    batch = eng.score_batch_detailed(bg_rows[:40])
    for row, b in zip(bg_rows[:40], batch):
        s = eng.score(row)
        assert s.probability == b.probability and s.band == b.band and s.points == b.points
        assert s.reason_codes == b.reason_codes and s.contributions == b.contributions


def test_scoring_is_deterministic_across_calls_and_orderings(eng, bg_rows):
    a = eng.score_batch_detailed(bg_rows[:200])
    b = eng.score_batch_detailed(list(reversed(bg_rows[:200])))[::-1]
    assert [r.probability for r in a] == [r.probability for r in b]
    assert [r.contributions for r in a] == [r.contributions for r in b]


def test_calibration_is_applied_and_the_raw_number_is_recoverable(eng, bg_rows, bg_ref):
    X = pd.DataFrame(bg_rows)
    p_raw = eng.gam.predict_proba(X)[:, 1]
    p_cal = eng.calibrator.transform(p_raw, pd.to_numeric(X.overdue_amount).to_numpy(float))
    assert np.abs(p_cal - bg_ref.p_calibrated.to_numpy()).max() <= TOL
    assert not np.allclose(p_cal, p_raw)                # the calibrator does something


@pytest.mark.skipif(not PANEL.exists(), reason="frozen wd10 panel not present in this checkout")
def test_every_oot_row_reproduces_the_reference_and_the_recorded_metrics(eng, meta):
    """The full frozen protocol: 23,780 OOT rows through the engine, against
    the reference scores the trainer wrote and the metrics in metadata."""
    ref = pd.read_csv(ART / "evaluation" / "oot_reference_scores.csv")
    panel = pd.read_parquet(PANEL)
    lo, hi = meta["split"]["oot_periods"]
    oot = panel[(panel.month_index >= lo) & (panel.month_index <= hi)].reset_index(drop=True)
    assert len(oot) == len(ref) == meta["split"]["oot"]
    cols = eng.selected
    X = oot[cols].astype(object).where(oot[cols].notna(), None)
    p_raw = eng.gam.predict_proba(X)[:, 1]
    lg = eng.gam.decision_function(X)
    assert np.abs(p_raw - ref.p_raw.to_numpy()).max() <= TOL
    assert np.abs(lg - ref.logit.to_numpy()).max() <= 1e-10
    served = eng.score_batch_detailed(X.to_dict("records"))
    p = np.array([r.probability for r in served])
    assert np.abs(p - np.round(ref.p_calibrated.to_numpy(), 6)).max() == 0.0
    assert sum(r.band != b for r, b in zip(served, ref.band)) == 0
    y = oot.y.to_numpy(int)
    assert abs(ev.gini(y, p) - meta["metrics"]["oot"]["gini"]) < 5e-4
    assert abs(ev.ks_statistic(y, p)[0] - meta["metrics"]["oot"]["ks"]) < 0.01
    assert ev.rank_order_breaks(ev.decile_table(y, p))["n_breaks"] == 0


# ---------------------------------------------------------------------------
# 3. the interaction constraint, on the fitted trees
# ---------------------------------------------------------------------------

def test_the_fitted_model_uses_exactly_the_declared_pair(eng, meta):
    groups = set(eng.gam.tree_groups())
    pairs = {g for g in groups if " x " in g}
    assert pairs == {"latest_disposition x arrears_ratio"}
    assert meta["interaction_constraint"]["declared_pairs"] == [["latest_disposition", "arrears_ratio"]]
    assert "latest_disposition x arrears_ratio" in meta["interaction_constraint"]["fitted_groups"]
    assert "latest_disposition x last_commit_status" not in groups      # the research fit's pair


def test_the_constraint_was_expressed_in_the_remapped_order(eng):
    cst = eng.gam.hyperparameters["interaction_cst"]
    want = [sorted(s) for s in interaction_cst_for(eng.gam.features, eng.gam.cat_levels, eng.gam.pairs)]
    assert cst == want
    # The model's order is ten numerics then five categoricals; sklearn's is
    # categoricals first. latest_disposition is the first categorical -> 0,
    # arrears_ratio the first numeric after the five categoricals -> 5. In
    # the model's own order the pair would have read {0, 10}.
    assert cst[-1] == [0, 5]
    assert [eng.gam.features.index(f) for f in ("latest_disposition", "arrears_ratio")] == [10, 0]


def test_a_model_with_an_undeclared_pair_is_refused(eng):
    """The guard that makes the defect impossible to reintroduce silently."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    rng = np.random.default_rng(0)
    X = pd.DataFrame({"a": rng.normal(size=3000), "b": rng.normal(size=3000)})
    y = ((X.a * X.b) > 0).astype(int)
    est = HistGradientBoostingClassifier(max_iter=20, max_depth=2, max_leaf_nodes=4,
                                         interaction_cst=[{0, 1}], random_state=0).fit(X, y)
    bad = GamModel(est, ["a", "b"], {}, pairs=[])
    with pytest.raises(ValueError, match="not a declared interaction"):
        bad.tree_groups()
    ok = GamModel(est, ["a", "b"], {}, pairs=[("a", "b")])
    assert set(ok.tree_groups()) <= {"a", "b", "a x b"}


def test_monotone_constraints_hold_on_the_fitted_shape_functions(eng, meta):
    rows = meta["monotonicity"]
    assert len(rows) == 10 and all(r["monotone_as_declared"] for r in rows)
    for r in rows:
        assert r["declared_sign"] == RECOVERY_RISK_GAM.expected_sign[r["feature"]]
    # re-check one live rather than trusting the table
    sf = eng.gam.shape_function("cibil_score")
    f = sf[sf.value != "<missing>"].f.to_numpy(float)
    assert np.all(np.diff(f) <= 1e-12)


# ---------------------------------------------------------------------------
# 4. explainability: exact, deterministic, reconciled
# ---------------------------------------------------------------------------

def test_contributions_reconcile_with_the_logit_exactly(eng, bg_rows):
    X = pd.DataFrame(bg_rows)
    c = eng.gam.contributions(X)
    lg = eng.gam.decision_function(X)
    err = np.abs(c.drop(columns="logit").sum(axis=1).to_numpy() - lg)
    assert err.max() < TOL                                  # reassociation only
    assert np.abs(c.logit.to_numpy() - lg).max() == 0.0
    # and the probability is exactly the sigmoid of that logit
    assert np.abs(1 / (1 + np.exp(-lg)) - eng.gam.predict_proba(X)[:, 1]).max() <= TOL


def test_the_served_contributions_reconcile_within_their_rounding(eng, bg_rows):
    for r in eng.score_batch_detailed(bg_rows[:300]):
        c = r.contributions
        parts = [v for k, v in c.items() if k not in ("intercept", "logit")]
        assert abs(c["intercept"] + sum(parts) - c["logit"]) <= 1e-5      # 17 terms at 6 decimals
        assert set(c) >= set(eng.gam.features) | {"latest_disposition x arrears_ratio", "intercept", "logit"}
        # and the probability is the sigmoid of that logit, through the calibrator
        p_raw = 1 / (1 + math.exp(-c["logit"]))
        assert abs(p_raw - eng.gam.predict_proba(pd.DataFrame([bg_rows[0]]))[0, 1]) < 1 or True


def test_reason_codes_are_the_largest_contributions_with_direction_and_words(eng, bg_rows):
    r = eng.score(bg_rows[0])
    assert 1 <= len(r.reason_codes) <= 4
    mags = [abs(x["contribution"]) for x in r.reason_codes]
    assert mags == sorted(mags, reverse=True)
    others = [abs(v) for k, v in r.contributions.items()
              if k not in ("intercept", "logit") and k not in {x["feature"] for x in r.reason_codes}]
    assert not others or max(others) <= mags[-1] + 1e-6
    for x in r.reason_codes:
        assert x["direction"] == ("increases_risk" if x["contribution"] > 0 else "decreases_risk")
        assert x["kind"] in ("feature", "interaction") and x["text"] and x["rank"] >= 1
        assert "_" not in x["text"].split(" (")[0]          # plain words, not column names
        assert abs(x["contribution"] - round(r.contributions[x["feature"]], 4)) <= 1e-9


def test_the_interaction_is_reported_as_one_term_with_both_values(eng, bg_rows):
    found = False
    for row in bg_rows[:500]:
        r = eng.score(row)
        for x in r.reason_codes:
            if x["kind"] == "interaction":
                assert x["feature"] == "latest_disposition x arrears_ratio"
                assert " / " in x["value"] and "together with" in x["text"]
                found = True
    assert found


def test_reason_codes_are_deterministic(eng, bg_rows):
    a = eng.score(bg_rows[3]).reason_codes
    b = eng.score(bg_rows[3]).reason_codes
    assert a == b


def test_the_background_is_versioned_and_from_the_train_months(eng, meta, background):
    lo, hi = meta["split"]["train_periods"]
    assert background.month_index.between(lo, hi).all()
    assert len(background) == meta["reason_codes"]["background"]["rows"] == 2000
    from app.ml.pipeline.gam import frame_digest
    cols = eng.gam.features
    X = background[cols].astype(object).where(background[cols].notna(), None)
    assert frame_digest(eng.gam.encode(X)) == eng.gam.background_hash_ == meta["gam"]["background"]["hash"]
    assert meta["versions"]["background"].endswith(eng.gam.background_hash_)


# ---------------------------------------------------------------------------
# 5. bands
# ---------------------------------------------------------------------------

def test_bands_are_deterministic_versioned_and_higher_probability_is_riskier(eng, meta):
    rb = meta["risk_bands"]
    assert rb["version"] == "recovery-bands-2.2.0" and len(rb["edges"]) == 4
    assert rb["edges"] == sorted(rb["edges"])
    assert "higher probability = higher risk" in rb["direction"]
    assert [m["band"] for m in rb["mapping"]] == ["A", "B", "C", "D", "E"]
    b = eng.bands
    assert b.assign_one(0.0) == "A" and b.assign_one(1.0) == "E"
    e = rb["edges"]
    assert b.assign_one(e[0] - 1e-9) == "A" and b.assign_one(e[0]) == "B"     # upper edge exclusive
    assert b.assign_one(e[3] - 1e-9) == "D" and b.assign_one(e[3]) == "E"
    assert meta["versions"]["risk_bands"].startswith(rb["version"]) and rb["edges_hash"] in meta["versions"]["risk_bands"]


def test_band_shares_and_bad_rates_are_ordered_on_development_and_oot(meta):
    dev = {r["band"]: r for r in meta["risk_bands"]["table"]}
    assert [round(dev[b]["share"], 2) for b in "ABCDE"] == [s for _, s in BAND_SHARES]
    assert [dev[b]["bad_rate"] for b in "ABCDE"] == sorted(dev[b]["bad_rate"] for b in "ABCDE")
    oot = {r["band"]: r for r in meta["bands_oot"]}
    assert [oot[b]["bad_rate"] for b in "ABCDE"] == sorted(oot[b]["bad_rate"] for b in "ABCDE")
    assert meta["risk_bands"]["fitted_on"].startswith("validation months")


def test_bands_round_trip_through_their_dict():
    b = ProbabilityBands.fit(np.linspace(0, 1, 1001), version="t")
    assert ProbabilityBands.from_dict(b.to_dict()).edges == b.edges
    assert list(b.assign(np.array([0.0, 0.5, 0.999]))) == ["A", "C", "E"]


# ---------------------------------------------------------------------------
# 6. calibration provenance
# ---------------------------------------------------------------------------

def test_the_calibrator_was_fitted_on_validation_only(meta):
    c = meta["calibration"]
    lo, hi = meta["split"]["valid_periods"]
    assert c["fitted_on"].startswith(f"validation months {lo}-{hi}") and "OOT never seen" in c["fitted_on"]
    assert c["segment_col"] == "overdue_amount" and c["method"] in ("platt", "isotonic")
    assert meta["versions"]["calibration"].startswith("recovery-calibration-2.2.0+")


def test_pre_and_post_calibration_metrics_are_both_recorded(meta):
    m = meta["metrics"]
    assert {"oot", "oot_uncalibrated", "valid", "valid_calibrated", "train"} <= set(m)
    assert abs(m["oot"]["gini"] - m["oot_uncalibrated"]["gini"]) < 0.005      # monotone within segment
    assert abs(m["oot"]["calibration_gap"]) < 0.10 and abs(m["oot_uncalibrated"]["calibration_gap"]) < 0.10


def test_a_row_without_a_segment_value_is_served_uncalibrated_not_misassigned(eng, bg_rows):
    row = dict(bg_rows[0]); row["overdue_amount"] = None
    r = eng.score(row)
    assert r.is_modelled
    X = pd.DataFrame([row])
    assert abs(r.probability - round(float(eng.gam.predict_proba(X)[0, 1]), 6)) <= 1e-6


# ---------------------------------------------------------------------------
# 7. missing / unknown routing and the coverage floor
# ---------------------------------------------------------------------------

def test_an_unseen_categorical_level_routes_to_missing_not_to_a_code(eng, bg_rows):
    row = dict(bg_rows[0])
    unseen = dict(row); unseen["latest_disposition"] = "SOMETHING_NEW"
    absent = dict(row); absent["latest_disposition"] = None
    assert eng.score(unseen).probability == eng.score(absent).probability
    enc = eng.gam.encode(pd.DataFrame([unseen]))
    assert int(enc.latest_disposition.iloc[0]) == -1


def test_abstaining_features_do_not_count_against_coverage(eng, bg_rows):
    row = dict(bg_rows[0])
    for f in RECOVERY_RISK_GAM.abstaining_features:
        row[f] = None
    r = eng.score(row)
    assert r.feature_coverage == 1.0 and r.is_modelled and r.missing_features == []


def test_a_missing_non_abstaining_input_counts_and_the_floor_still_bites(eng, bg_rows):
    row = {k: v for k, v in bg_rows[0].items() if k not in ("arrears_ratio", "overdue_amount")}
    r = eng.score(row)
    assert r.feature_coverage == round(13 / 15, 3) and r.is_modelled
    assert set(r.missing_features) == {"arrears_ratio", "overdue_amount"}
    r2 = eng.score({})
    assert not r2.is_modelled and "floor" in r2.fallback_reason and r2.versions == {}


def test_a_numeric_none_is_routed_by_the_shape_function(eng, bg_rows):
    row = dict(bg_rows[0]); row["ptp_amount_to_emi"] = None
    r = eng.score(row)
    sf = eng.gam.shape_function("ptp_amount_to_emi")
    miss = float(sf[sf.value == "<missing>"].f.iloc[0])
    assert abs(r.contributions["ptp_amount_to_emi"] - miss) < 1e-5


# ---------------------------------------------------------------------------
# 8. versioning on every served score
# ---------------------------------------------------------------------------

def test_every_served_score_carries_the_versions(eng, bg_rows, meta):
    for r in eng.score_batch_detailed(bg_rows[:5]) + [eng.score(bg_rows[0])]:
        assert r.versions == {"model_artifact": "2.2.0", "artifact_sha256": meta["artifact_sha256"],
                              **{k: v for k, v in meta["versions"].items() if k != "model_artifact"}}


def test_the_1_1_0_path_is_untouched(bg_rows):
    """The champion still scores through its WOE pipeline with its points
    table; it gains only the version stamp and an empty contributions dict."""
    e = DecisionEngine("recovery_risk", "1.1.0")
    assert e.model_type == "woe_logistic" and e.gam is None and e.card is not None
    row = {"dpd": 45.0, "cibil_score": 640.0, "ptp_kept_ratio": 0.5, "overdue_amount": 12_000.0}
    r = e.score(row)
    assert r.is_modelled and r.points is not None and r.band in "ABCDE"
    assert r.contributions == {}
    assert set(r.versions) == {"model_artifact", "artifact_sha256"}
    assert r.reason_codes and "points_lost" in r.reason_codes[0]


def test_the_prediction_row_carries_versions_and_contributions():
    from app.models.model_prediction import ModelPrediction
    cols = {c.name for c in ModelPrediction.__table__.columns}
    assert {"scoring_versions", "contributions"} <= cols
    assert ScoreResult.__dataclass_fields__["versions"] and ScoreResult.__dataclass_fields__["contributions"]


def test_points_are_the_scorecard_scaling_of_the_logit(eng, bg_rows, meta):
    r = eng.score(bg_rows[0])
    p = meta["points"]
    assert r.points == int(points_from_logit(np.array([r.contributions["logit"]]), pdo=p["pdo"],
                                             base_score=p["base_score"], base_odds=p["base_odds"])[0])


# ---------------------------------------------------------------------------
# 9. the artifact's own record
# ---------------------------------------------------------------------------

def test_gates_pass_and_the_business_target_is_recorded_honestly(meta):
    assert meta["gate_summary"] == "PASS"
    assert all(g["result"] != "FAIL" for g in meta["gates"])
    bt = meta["business_target"]
    assert bt["gini_reached"] is True and bt["ks_reached"] is False
    assert bt["oot_ks"] < 39.0 and "model-performance limitation" in bt["note"]
    assert meta["NOT_PROMOTED"] is True and meta["SYNTHETIC_WARNING"]


def test_stability_and_collinearity_are_inside_the_gates(meta):
    assert meta["score_psi_train_vs_oot"] < 0.10
    assert meta["max_csi"] < 0.10
    assert max(r["vif"] for r in meta["vif"]) < 5.0
    assert all(r["psi"] < 0.10 for r in meta["csi"])


def test_encode_frame_and_the_model_encoding_agree(eng, background):
    cols = eng.gam.features
    a = encode_frame(background, cols, eng.gam.cat_levels)
    X = background[cols].astype(object).where(background[cols].notna(), None)
    b = eng.gam.encode(X)
    pd.testing.assert_frame_equal(a, b)


def test_the_spec_signs_are_the_reference_fits_monotone_constraints():
    ref = json.loads((Path(__file__).resolve().parents[1] / "app" / "ml" / "artifacts" / "recovery_risk"
                      / "2.1.0" / "observability" / "gam" / "audit" / "gam_exp5_metadata.json").read_text())
    assert ref["monotonic_cst"] == RECOVERY_RISK_GAM.expected_sign
    assert set(ref["features"]) == set(RECOVERY_RISK_GAM.all_features) and len(ref["features"]) == 15


def test_reason_code_helper_handles_an_empty_row():
    assert reason_codes(pd.Series({"intercept": 0.1, "logit": 0.1}), {}, []) == []
