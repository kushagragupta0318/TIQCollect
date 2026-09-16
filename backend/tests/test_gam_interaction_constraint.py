"""The GAM interaction constraint lands where it is declared — 2026-09-16.

A regression test for the defect the production-readiness audit found.

`HistGradientBoostingClassifier` inserts a ColumnTransformer when
`categorical_features` is used, and its output puts every categorical first.
sklearn remaps `monotonic_cst` into that order and does NOT remap
`interaction_cst`, so a constraint written in the caller's column order is
applied to different features. On the audited 15-feature model the declared
pair `latest_disposition x arrears_ratio` was applied to
`latest_disposition x last_commit_status` (OOT KS 38.84 as fitted, 38.65 with
the pair where it was declared).

These tests assert the behaviour rather than the prose: the first fails if
sklearn ever stops reordering (then the helper would be the thing that is
wrong), the second and third prove the helper puts the interaction on the
declared pair and nowhere else.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research" / "recovery_risk_obs"))
from gam_common import interaction_cst_for, remap_index, remapped_order  # noqa: E402

COLS = ["num_a", "cat_x", "num_b", "cat_y"]
CATS = {"cat_x", "cat_y"}


@pytest.fixture(scope="module")
def data():
    rng = np.random.default_rng(0)
    n = 4000
    df = pd.DataFrame({
        "num_a": rng.normal(size=n),
        "cat_x": rng.integers(0, 3, n),
        "num_b": rng.normal(size=n),
        "cat_y": rng.integers(0, 3, n),
    })
    # a genuine interaction between num_a and cat_x, so a model allowed to use
    # it will, and one not allowed to cannot
    logit = 1.2 * df.num_a * (df.cat_x == 1) + 0.6 * df.num_b
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    return df, y


def _fit(df, y, cst):
    return HistGradientBoostingClassifier(
        max_iter=60, learning_rate=0.1, max_leaf_nodes=4, max_depth=2,
        min_samples_leaf=50, random_state=0, early_stopping=False,
        categorical_features=[c in CATS for c in COLS],
        interaction_cst=cst).fit(df[COLS], y)


def _tree_feature_groups(m) -> set[tuple[int, ...]]:
    groups = set()
    for stage in m._predictors:
        for tree in stage:
            nodes = tree.nodes
            internal = nodes[nodes["is_leaf"] == 0]
            groups.add(tuple(sorted(set(int(i) for i in internal["feature_idx"]))))
    return {g for g in groups if len(g) > 1}


def test_sklearn_still_reorders_categoricals_to_the_front(data):
    """The premise of the helper. If this fails, sklearn changed and
    gam_common must be revisited — not the callers."""
    df, y = data
    m = _fit(df, y, None)
    assert list(m.is_categorical_) == [c in CATS for c in COLS], "mask read wrongly"
    # the estimator's own remapped mask puts the categoricals first
    assert list(m._is_categorical_remapped) == [True, True, False, False]
    assert remapped_order(COLS, CATS) == ["cat_x", "cat_y", "num_a", "num_b"]


def test_the_helper_puts_the_interaction_on_the_declared_pair(data):
    df, y = data
    pair = ("num_a", "cat_x")
    m = _fit(df, y, interaction_cst_for(COLS, CATS, [pair]))
    idx = remap_index(COLS, CATS)
    want = tuple(sorted((idx[pair[0]], idx[pair[1]])))
    groups = _tree_feature_groups(m)
    assert groups, "no tree used the allowed pair — the fixture interaction is too weak"
    assert groups == {want}, f"expected only {want}, got {groups}"


def test_the_unremapped_constraint_lands_on_the_wrong_features(data):
    """The defect itself, preserved: indices in the CALLER's order select a
    different pair once sklearn has reordered the columns."""
    df, y = data
    naive = [{i} for i in range(len(COLS))] + [{COLS.index("num_a"), COLS.index("cat_x")}]  # {0, 1}
    m = _fit(df, y, naive)
    idx = remap_index(COLS, CATS)
    declared = tuple(sorted((idx["num_a"], idx["cat_x"])))
    groups = _tree_feature_groups(m)
    if groups:
        assert declared not in groups, "the naive constraint accidentally hit the declared pair"
        assert groups == {(0, 1)}, groups
        assert {remapped_order(COLS, CATS)[i] for i in (0, 1)} == {"cat_x", "cat_y"}


def test_singletons_survive_any_reordering():
    """Why the defect did not stop the model being a GAM: the set of
    singleton constraints is invariant under the column permutation."""
    naive = [{i} for i in range(len(COLS))]
    helped = interaction_cst_for(COLS, CATS, [])
    assert [set(s) for s in naive] == [set(s) for s in helped]
