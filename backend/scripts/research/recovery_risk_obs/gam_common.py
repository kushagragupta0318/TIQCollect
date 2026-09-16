"""One definition of the GAM's interaction constraint, 2026-09-16.

WHY THIS FILE EXISTS — a defect the production-readiness audit found.

`HistGradientBoostingClassifier` inserts a ColumnTransformer whenever
`categorical_features` is given (gradient_boosting.py:288): `("encoder", ...,
is_categorical_)` then `("numerical", ..., ~is_categorical_)`. Its output puts
EVERY CATEGORICAL FIRST, in original order, then every numeric. sklearn then
remaps `monotonic_cst` into that order (line 566) and **passes
`interaction_cst` through UNREMAPPED** (line 579 -> 887).

So an `interaction_cst` written in the caller's column order is applied to
different features. Measured on the audited 15-feature model: the declared
pair `latest_disposition x arrears_ratio` (original indices 1, 0) was applied
to remapped indices {0, 1} = `latest_disposition x last_commit_status`. The
model stayed a GAM — a set of singletons is permutation-invariant, so "every
tree splits on one feature" survived — but the one allowed pair was not the
declared one.

    as fitted   pair = latest_disposition x last_commit_status  OOT KS 38.84
    as declared pair = latest_disposition x arrears_ratio       OOT KS 38.65

Use `interaction_cst_for()` for every GAM fit; `tests/test_gam_interaction_
constraint.py` fails if sklearn's remapping behaviour changes underneath it.
"""
from __future__ import annotations

# 2026-09-16 (later) — ONE DEFINITION. The three helpers moved into
# app/ml/pipeline/gam.py when the GAM became a production model type; the
# research scripts import them from there so the constraint the trainer uses
# and the constraint the ladder used cannot drift apart.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from app.ml.pipeline.gam import interaction_cst_for, remap_index, remapped_order  # noqa: E402,F401
