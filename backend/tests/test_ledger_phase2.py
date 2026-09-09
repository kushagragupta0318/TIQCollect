"""Phase 2 properties: the spec is untouched, the champion is untouched, and
the point-in-time gate is load-bearing.

These do NOT retrain the champion — that takes minutes and belongs in the
validation script. They assert the structural guarantees the Phase 2 result
depends on, executably.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from app.ml.pipeline.config import RECOVERY_RISK
from app.ml.simulation.ledger import LedgerConfig, LedgerSimulator
from app.ml.simulation.ledger.panel import build_panel
from scripts import phase2_ledger_validation as P

# Sized so the SHUFFLE PROBE is meaningful. At 300 borrowers the out-of-time
# slice was ~400 rows, where SE(Gini) is around 0.06 and a permuted-label Gini
# of 0.089 is well inside noise — the probe could not distinguish a leak from
# sampling error, so its 0.05 bar was measuring nothing.
SMALL = LedgerConfig(n_borrowers=900, months=14, seed=5)


@pytest.fixture(scope="module")
def panel():
    return build_panel(LedgerSimulator(SMALL).run(intercept=-4.1562), SMALL)


# ---------------------------------------------------------------------------
# The spec is used verbatim
# ---------------------------------------------------------------------------

def test_the_ledger_run_changes_only_the_version_string():
    """Phase 2 is only interpretable if the model definition is identical. A
    feature list quietly widened to help the new panel would make the comparison
    against 1.1.0 meaningless."""
    ledger_spec = replace(RECOVERY_RISK, version=P.LEDGER_VERSION)
    assert ledger_spec.version != RECOVERY_RISK.version
    for field in ("name", "target", "split_col", "numeric_features",
                  "categorical_features", "train_frac", "valid_frac",
                  "expected_sign", "segment_cols", "forbidden", "gates",
                  "pdo", "base_score", "base_odds"):
        assert getattr(ledger_spec, field) == getattr(RECOVERY_RISK, field), field


def test_replace_does_not_mutate_the_shared_spec_object():
    """`dataclasses.replace` copies. If it mutated, every later import of
    RECOVERY_RISK — including the one the live engine resolves against — would
    carry the experiment's version."""
    before = RECOVERY_RISK.version
    replace(RECOVERY_RISK, version="9.9.9-scratch")
    assert RECOVERY_RISK.version == before == "1.1.0"


def test_the_champion_pointer_still_names_the_committed_model():
    from app.ml.pipeline import registry

    assert registry.resolve_version("recovery_risk", "champion") == "1.1.0"


def test_saving_without_make_champion_leaves_the_pointer_alone(tmp_path,
                                                               monkeypatch):
    """The one guarantee that keeps an experiment from becoming production."""
    from app.ml.pipeline import registry
    from sklearn.dummy import DummyClassifier

    monkeypatch.setattr(registry, "ARTIFACT_ROOT", tmp_path)
    registry.save("toy", "1.0.0", pipeline=DummyClassifier(), metadata={},
                  make_champion=True)
    assert (tmp_path / "toy" / "champion.txt").read_text().strip() == "1.0.0"

    registry.save("toy", "2.0.0-experiment", pipeline=DummyClassifier(),
                  metadata={}, make_champion=False)
    assert (tmp_path / "toy" / "champion.txt").read_text().strip() == "1.0.0"
    assert (tmp_path / "toy" / "2.0.0-experiment").exists()


# ---------------------------------------------------------------------------
# The point-in-time gate is load-bearing
# ---------------------------------------------------------------------------

def test_shuffling_the_label_destroys_all_signal(panel):
    """If structure reached the model through anything but the features, a
    permuted label would still score above zero."""
    probes = P.leakage_probes(panel, RECOVERY_RISK)
    assert abs(probes["gini_on_shuffled_labels"]) < 0.05


def test_future_features_score_far_higher_than_point_in_time_ones(panel):
    """The probe that proves the gate does work. Predicting month m's label from
    month m+1's FEATURES must beat the honest model by a wide margin — if it did
    not, the as_of boundary would be decorative and the honest number was never
    protected by it."""
    probes = P.leakage_probes(panel, RECOVERY_RISK)
    assert probes["future_uplift"] > 0.15, probes


def test_the_honest_reference_is_nowhere_near_perfect(panel):
    """A synthetic book that yields a near-perfect model is a broken book. The
    spec's own `gini_suspicious` gate exists for the same reason."""
    probes = P.leakage_probes(panel, RECOVERY_RISK)
    assert 0.20 < probes["gini_honest_reference"] < RECOVERY_RISK.gates.gini_suspicious


# ---------------------------------------------------------------------------
# The structural finding behind check 8
# ---------------------------------------------------------------------------

def test_dpd_and_arrears_ratio_are_near_duplicates_by_construction(panel):
    """Both derive from the SAME FIFO position, so they are close to the same
    variable — measured r = 0.947 on the full tier.

    This is why check 8 reads 0.89: `Gini(dpd)/Gini(full)` is not measuring DPD
    dominating the model, it is measuring that the selected `arrears_ratio` IS
    essentially DPD. Recorded as a property of the billing spine rather than
    left to be rediscovered as a surprise.
    """
    r = panel.dpd.corr(panel.arrears_ratio)
    assert r > 0.85, r
    # And they really are two views of one quantity, not a coincidence.
    assert np.allclose(panel.arrears_ratio,
                       panel.overdue_amount / np.maximum(panel.emi_amount, 1),
                       atol=0.002)


def test_the_oracle_join_reads_latents_only_from_the_quarantined_table(panel,
                                                                      tmp_path):
    """`information_ceiling` is the ONE deliberate use of the ground truth. With
    no ground-truth file it must decline rather than silently score without it."""
    out = P.information_ceiling(panel, tmp_path, RECOVERY_RISK)
    assert out == {"error": "no ground_truth.parquet"}


# ---------------------------------------------------------------------------
# Check 10, as redefined 2026-09-09
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ceiling(panel, tmp_path_factory):
    """Run the corrected check on a small book with its own ground truth."""
    d = tmp_path_factory.mktemp("ledger")
    led = LedgerSimulator(SMALL).run(intercept=-4.1562)
    led.ground_truth.to_parquet(d / "ground_truth.parquet", index=False)
    return P.information_ceiling(panel, d, RECOVERY_RISK)


def test_the_oracle_is_a_superset_of_what_the_model_sees(ceiling):
    """The defect in the first version of this check, asserted so it cannot
    return: an oracle that does not contain the observable columns is not a
    ceiling, and can score BELOW the thing it is meant to bound."""
    for f in RECOVERY_RISK.numeric_features:
        assert f in ceiling["oracle_columns"], f
    for latent in P.HIDDEN_LATENTS:
        assert latent in ceiling["oracle_columns"], latent
    assert P.TRUE_PROPENSITY in ceiling["oracle_columns"]
    # And the historical view of the latents, whose absence broke it.
    assert any(c.endswith("_mean3") for c in ceiling["oracle_columns"])


def test_the_oracle_dominates_the_observable_model(ceiling):
    """A superset of information cannot do worse. If this fails, the ceiling is
    not a ceiling and every fraction computed from it is meaningless."""
    assert ceiling["gini_oracle"] >= ceiling["gini_observable"]
    assert ceiling["oracle_gap_absolute"] >= 0
    assert 0.0 < ceiling["fraction_of_oracle_captured"] <= 1.0


def test_the_superseded_criterion_is_preserved_not_deleted(ceiling):
    """Auditability: the original check, its value, and why it moved must all
    survive in the output. A validation criterion that changes without leaving a
    record is indistinguishable from one that was fitted to the answer."""
    assert "SUPERSEDED_gini_latents_at_as_of" in ceiling
    assert "SUPERSEDED_gap_vs_latents_only" in ceiling
    # The note must say WHY the definition moved and that the book was not
    # touched — the two facts that separate a corrected criterion from a
    # moved goalpost.
    assert "not an upper bound" in ceiling["SUPERSEDED_note"]
    assert "NOT tuned" in ceiling["SUPERSEDED_note"]


def test_latents_alone_are_not_used_as_the_ceiling(ceiling):
    """The corrected gap must be measured against the JOINT set. On the full
    tier the two differ by an order of magnitude — 0.0949 against 0.0036 — so
    conflating them is not a rounding matter."""
    latents_only_gap = ceiling["SUPERSEDED_gap_vs_latents_only"]
    assert ceiling["oracle_gap_absolute"] != latents_only_gap
    assert ceiling["gini_oracle"] > ceiling["SUPERSEDED_gini_latents_at_as_of"]


def test_the_check_8_ratio_is_never_gated():
    """It compares `dpd` against `arrears_ratio`, which is `dpd` in other units.
    Gating on it would mean tuning the generator until two definitionally
    related quantities pretended not to be."""
    import inspect
    src = inspect.getsource(P.main)
    # Executable part: the constant exists but no gate consumes it.
    assert "DPD_DOMINANCE_MAX" in src
    assert '"DIAGNOSTIC"' in src
    # The gate set is built from entries marked GATED only.
    assert 'c[4] == "GATED"' in src
