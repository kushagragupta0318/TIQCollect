"""recovery_risk 2.1.0 — the fresh development, and what it must hold to.

Read from the committed artifact and its V21_REPORT.json, never recomputed.
Three groups:

  1. THE SPEC. A second candidate spec beside 2.0.0, same target, gates and
     forbidden list; six features added, each servable and each in the
     Phase 3 equality harness (tests/test_ledger_phase3_adapter_equality.py).
  2. THE ARTIFACT. Gates pass; at least seven features; every selected
     feature's bins run the way its sign says; VIF, IV, PSI/CSI and
     calibration inside the acceptance bands; the scorecard was kept over
     the GBM challenger by the repo's own margin rule.
  3. THE COMPARISON. 1.1.0, 2.0.0 and 2.1.0 scored on the IDENTICAL
     out-of-time rows through the production path: better than the deployed
     model by the comparison gate, equivalent to 2.0.0 (recorded, not
     hidden), stable month by month and bucket by bucket, and NOT promoted.

The business target (Gini 0.50-0.55, KS 39-42) is asserted here as the
number the artifact actually reached against the target, so a future run
that reaches it flips one test rather than rewrites the file — and so the
shortfall is recorded executably instead of in prose alone.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.ml.pipeline import registry
from app.ml.pipeline.binning import WOEBinner
from app.ml.pipeline.config import (
    CANDIDATE_SPECS, LOGGED_FEATURES, RECOVERY_RISK, RECOVERY_RISK_V2,
    RECOVERY_RISK_V21,
)

V21 = "2.1.0"
ADDED = {"paid_ratio_1m", "pay_amount_cv_6m", "call_answer_rate_3m",
         "intent_rate_6m", "days_since_last_call", "last_visit_outcome"}

#: The business acceptance target the fresh development was run against.
TARGET_GINI = (0.50, 0.55)
TARGET_KS = (39.0, 42.0)


def _dir() -> Path | None:
    d = registry.version_dir("recovery_risk", V21)
    return d if (d / "metadata.json").exists() else None


v21_required = pytest.mark.skipif(
    _dir() is None,
    reason="no 2.1.0 artifact; run scripts/train_recovery_risk_v2 --version 2.1.0")


@pytest.fixture(scope="module")
def meta() -> dict:
    return json.loads((_dir() / "metadata.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report() -> dict:
    p = _dir() / "V21_REPORT.json"
    if not p.exists():
        pytest.skip("no V21_REPORT.json beside the artifact")
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. The spec
# ---------------------------------------------------------------------------

def test_v21_is_a_candidate_spec_beside_2_0_0_and_1_1_0_is_untouched():
    assert RECOVERY_RISK_V21.version == V21
    assert RECOVERY_RISK_V21.name == "recovery_risk"
    assert (RECOVERY_RISK_V21.name, V21) in CANDIDATE_SPECS
    assert (RECOVERY_RISK_V2.name, "2.0.0") in CANDIDATE_SPECS
    assert RECOVERY_RISK.version == "1.1.0"
    assert len(RECOVERY_RISK.all_features) == 33
    assert RECOVERY_RISK_V21.gates == RECOVERY_RISK.gates, "a gate moved"
    assert RECOVERY_RISK_V21.target == RECOVERY_RISK.target
    assert RECOVERY_RISK_V21.forbidden == RECOVERY_RISK.forbidden
    assert RECOVERY_RISK_V21.training_panel == "ledger"


def test_v21_adds_exactly_six_servable_features_to_2_0_0():
    assert set(RECOVERY_RISK_V21.all_features) - set(RECOVERY_RISK_V2.all_features) == ADDED
    assert set(RECOVERY_RISK_V2.all_features) < set(RECOVERY_RISK_V21.all_features)
    assert "last_visit_outcome" in RECOVERY_RISK_V21.categorical_features
    for f in RECOVERY_RISK_V21.numeric_features:
        assert f in RECOVERY_RISK_V21.expected_sign, f
    assert not set(RECOVERY_RISK_V21.all_features) & set(RECOVERY_RISK_V21.forbidden)
    assert set(RECOVERY_RISK_V21.abstaining_features) <= set(RECOVERY_RISK_V21.numeric_features)


def test_the_policy_shaped_recency_features_carry_no_declared_direction():
    """`days_since_last_call` / `_contact` read "contacted recently -> more
    risk" in this world because attempts follow delinquency. Declaring a
    business sign on them would force a trend the data contradicts; the spec
    says 0 and the sign check decides on the fitted coefficient instead."""
    s = RECOVERY_RISK_V21.expected_sign
    assert s["days_since_last_call"] == 0
    assert s["days_since_last_contact"] == 0
    assert s["paid_ratio_1m"] == -1 and s["intent_rate_6m"] == -1
    assert s["call_answer_rate_3m"] == -1


def test_logged_vector_covers_all_three_specs():
    for spec in (RECOVERY_RISK, RECOVERY_RISK_V2, RECOVERY_RISK_V21):
        assert set(LOGGED_FEATURES) >= set(spec.all_features)
    assert len(LOGGED_FEATURES) == len(set(LOGGED_FEATURES))


def test_categorical_psi_is_measured_not_reported_as_shifted():
    """`last_visit_outcome` is the first categorical a recovery model
    selected; PSI used to return NaN on it, which psi_frame labelled
    'shifted'."""
    from app.ml.pipeline.evaluate import psi, psi_frame

    a = pd.Series(["A"] * 50 + ["B"] * 30 + ["C"] * 20)
    assert psi(a, a) == 0.0
    moved = pd.Series(["A"] * 20 + ["B"] * 30 + ["C"] * 50)
    assert psi(a, moved) > 0.10
    unseen = pd.Series(["A"] * 50 + ["B"] * 30 + ["Z"] * 20)
    assert psi(a, unseen) > psi(a, moved)          # an unseen category is drift
    fr = psi_frame(pd.DataFrame({"c": a}), pd.DataFrame({"c": a}), ["c"])
    assert fr.iloc[0]["verdict"] == "stable" and fr.iloc[0]["psi"] == 0.0


# ---------------------------------------------------------------------------
# 2. The artifact
# ---------------------------------------------------------------------------

@v21_required
def test_v21_artifact_passed_every_gate_and_kept_the_scorecard(meta):
    assert meta["gate_summary"] == "PASS"
    assert not any(g["result"] == "FAIL" for g in meta["gates"])
    assert meta["spec"]["version"] == V21
    assert meta["spec"]["training_panel"] == "ledger"
    # The repo's own rule: the GBM must beat the scorecard by the recorded
    # margin to displace it. It did not, so the interpretable form ships.
    assert meta["champion_kind"] == "scorecard"
    assert meta["metrics"]["oot_challenger"]["gini"] < (
        meta["metrics"]["oot"]["gini"] + meta["challenger_margin_required"])


@v21_required
def test_v21_has_at_least_seven_features_including_new_ones(meta):
    sel = meta["selected_features"]
    assert 7 <= len(sel) <= 12, sel
    assert set(sel) <= set(RECOVERY_RISK_V21.all_features)
    assert set(sel) & ADDED, "none of the six fresh candidates was selected"
    assert set(sel) - set(RECOVERY_RISK.all_features), sel


@v21_required
def test_v21_quality_bands(meta):
    """VIF < 5, IV of every selected feature above the spec floor and below
    the review line, score PSI and every selected feature's CSI < 0.10,
    |calibration gap| < 0.03, zero rank-order breaks, OOT not below train by
    more than the gate."""
    d = _dir()
    sel = meta["selected_features"]
    vif = pd.read_csv(d / "evaluation" / "vif.csv")
    assert vif[vif.action == "kept"].vif.max() < 5.0
    iv = pd.read_csv(d / "eda" / "information_value.csv").set_index("feature")
    gates = meta["spec"]["gates"]
    for f in sel:
        assert gates["iv_min"] <= iv.loc[f, "iv"] <= gates["iv_review"], (f, iv.loc[f, "iv"])
        assert bool(iv.loc[f, "monotonic"]), f
    assert meta["score_psi_train_vs_oot"] < 0.10
    psi = pd.read_csv(d / "evaluation" / "psi.csv").set_index("feature")
    for f in sel:
        assert np.isfinite(psi.loc[f, "psi"]), f"{f}: PSI not measured"
        assert psi.loc[f, "psi"] < 0.10, (f, psi.loc[f, "psi"])
    oot = meta["metrics"]["oot"]
    assert abs(oot["calibration_gap"]) < 0.03
    assert oot["rank_order"]["n_breaks"] == 0
    assert oot["gini"] >= meta["metrics"]["train"]["gini"] - gates["max_train_test_gini_gap"]


@v21_required
def test_v21_selected_bins_run_in_the_direction_the_spec_expects(meta):
    bins = pd.read_csv(_dir() / "eda" / "binning_tables.csv")
    signs = RECOVERY_RISK_V21.expected_sign
    checked = 0
    for f in meta["selected_features"]:
        sign = signs.get(f, 0)
        if sign == 0 or f not in bins.feature.unique():
            continue
        body = bins[(bins.feature == f)
                    & ~bins.Bin.astype(str).isin(["Special", "Missing", "Totals", "nan"])]
        rates = pd.to_numeric(body["Event rate"], errors="coerce").dropna().to_numpy()
        if len(rates) < 2:
            continue
        dlt = np.diff(rates)
        ok = bool(np.all(dlt >= -1e-9)) if sign > 0 else bool(np.all(dlt <= 1e-9))
        assert ok, f"{f}: sign {sign} but event rates {rates.round(3)}"
        checked += 1
    assert checked >= 4
    assert meta["binning_trends_forced"] == WOEBinner.trends_from_signs(signs)


@v21_required
def test_v21_records_where_it_stands_against_the_business_target(meta):
    """Recorded, not hidden. The best legitimate model on this world did NOT
    reach Gini 0.50 / KS 39, and V21_REPORT.json carries the gap analysis.
    If a future retrain reaches it, this test is the one to flip."""
    oot = meta["metrics"]["oot"]
    assert oot["gini"] < TARGET_GINI[0], (
        "2.1.0 reached the business target; update this test and the "
        "information-gap section of the report")
    assert oot["ks"] < TARGET_KS[0]
    ci = meta["bootstrap_gini_oot"]
    assert ci["ci_upper_97.5"] < TARGET_GINI[0], (
        "the target is inside the bootstrap interval — no longer a clear miss")


# ---------------------------------------------------------------------------
# 3. The comparison on identical rows
# ---------------------------------------------------------------------------

@v21_required
def test_v21_beat_the_deployed_model_on_identical_rows(report):
    cmp = report["incumbent_comparison"]
    assert cmp["incumbent_version"] == "1.1.0" and cmp["challenger_version"] == V21
    assert cmp["passed"] and cmp["verdict"] == "challenger_better"
    assert cmp["gini_uplift"] > cmp["uplift_tolerance"]
    assert cmp["challenger"]["ks"] >= cmp["incumbent"]["ks"]
    assert cmp["challenger"]["brier"] <= cmp["incumbent"]["brier"]
    assert abs(cmp["challenger"]["calibration_gap"]) <= abs(cmp["incumbent"]["calibration_gap"])
    assert cmp["n_oot"] >= 10_000
    # Better in EVERY DPD bucket, not just on the pooled book.
    for seg in cmp["segment"]:
        assert seg["challenger_gini"] > seg["incumbent_gini"], seg


@v21_required
def test_v21_against_2_0_0_is_recorded_as_equivalent_not_hidden(report):
    """+0.003 Gini is inside sampling error. The comparison module says
    equivalent_keep_incumbent and the report carries that verdict verbatim:
    the fresh development improved on 2.0.0 by feature quality (nine
    features from a wider, cleaner search) and not by discrimination."""
    c2 = report["additional_comparisons"]["2.0.0"]
    assert c2["incumbent_version"] == "2.0.0"
    assert c2["gini_uplift"] > 0
    assert c2["gini_uplift"] <= c2["uplift_tolerance"]
    assert c2["verdict"] == "equivalent_keep_incumbent"
    assert c2["challenger"]["ks"] >= c2["incumbent"]["ks"]


@v21_required
def test_v21_is_stable_across_oot_months_and_buckets(report):
    st = report["stability"]
    assert st[f"monthly_gini_sd_{V21}"] < 0.02
    assert st[f"monthly_gini_min_{V21}"] > 0.40
    assert len(st["by_month"]) >= 6
    for r in st["by_month"]:
        assert r[f"gini_{V21}"] > r["gini_1.1.0"], r
    for r in st["by_bucket"]:
        assert r[f"gini_{V21}"] >= 0.20, r


@v21_required
def test_v21_no_degrade_table_has_no_fail_and_every_check_passed(report):
    assert report["no_degrade_passed"]
    assert all(r["result"] != "FAIL" for r in report["no_degrade"])
    assert report["overall_passed"]
    gated = [c for c in report["checks_7_to_10"] if c["status"] in ("PASS", "FAIL")]
    assert gated and all(c["status"] == "PASS" for c in gated)
    lk = report["leakage"]
    assert abs(lk["gini_on_shuffled_labels"]) < 0.03
    assert lk["future_uplift"] > 0.30


@v21_required
def test_v21_was_not_promoted_by_its_own_training(report):
    assert report["champion_after_run"] == "1.1.0"          # what the run recorded, then
    assert registry.pointer_version("recovery_risk") != V21     # and it never became champion


@v21_required
def test_the_adapter_supplies_every_feature_v21_selected(meta):
    """Structural: every selected feature is a key the adapter always emits
    (LOGGED_FEATURES) and is held equal to the panel by the Phase 3 harness."""
    from tests import test_ledger_phase3_adapter_equality as p3

    covered = set(p3.EXACT) | set(p3.MONEY) | set(p3.RATIO)
    for f in meta["selected_features"]:
        assert f in LOGGED_FEATURES, f
        assert f in covered, f"{f} is not held equal between panel and adapter"
