# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-16 — NEW. A monitor for the one kind of drift PSI cannot see.
#
#   `evaluate.psi` drops NaN on BOTH sides before binning (deliberately, and
#   it is not changed here: every PSI figure in every committed artifact and
#   every monitoring row was computed that way). The production-readiness
#   audit measured what that hides on the reference GAM's own inputs:
#   `days_since_last_contact` went from 49.2% missing on the train months to
#   37.2% on the out-of-time months — a 12-point change in how often the
#   model's shape function is asked to route a missing value — and PSI read
#   0.0216, "stable". Pinned by
#   tests/test_model_monitoring.py::test_psi_cannot_see_missingness_drift_…
#
#   This module reads the missing share directly, per feature, baseline vs
#   current, and says what to do when it moves. It is wired into
#   `monitor.monitor_model`'s stability block beside PSI, never instead of it.
# ───────────────────────────────────────────────────────────────────────────
"""
Feature-level missingness, baseline against current.

    from app.ml.pipeline.missingness import missingness_report

    rows = missingness_report(baseline_df, current_df, features)
    breached = [r for r in rows if r["breached"]]

Two rates per feature, and both are reported because they answer different
questions:

    null_rate     the share that is None / NaN — the adapter had NOTHING
    absent_rate   null OR one of `absent_levels` ("NONE" by default) — the
                  share for which the record holds NO OBSERVATION. For the
                  2.2.0 categoricals "NONE" is a fitted level meaning "never
                  read / no promise / no commitment", so a product whose
                  field process has not started capturing dispositions reads
                  100% NONE and 0% null: the second number sees it, the first
                  does not.

A feature BREACHES when its absent rate moves by more than
MISSINGNESS_ABS_THRESHOLD in absolute terms, or by more than
MISSINGNESS_REL_THRESHOLD relatively with at least MISSINGNESS_ABS_FLOOR of
absolute movement (so a 1% -> 1.4% change on a rare gap does not fire).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

#: Absolute change in the absent share that fires on its own.
MISSINGNESS_ABS_THRESHOLD = 0.10
#: Relative change that fires, provided the absolute move clears the floor.
MISSINGNESS_REL_THRESHOLD = 0.25
MISSINGNESS_ABS_FLOOR = 0.02
#: Below this many rows on either side the comparison is not made.
MIN_ROWS = 50
#: Categorical levels that mean "no observation" rather than a value.
DEFAULT_ABSENT_LEVELS = ("NONE",)

ACTION_ON_BREACH = (
    "Do not read the model's PSI/CSI as evidence of stability for this feature. "
    "Find out WHY the share of rows with no observation moved — an adapter or "
    "schema change, a source table that stopped being written, a field process "
    "that started or stopped capturing the value — and fix the source if it is a "
    "fault. If the change is real (the process genuinely changed), the shape "
    "function's missing route is now applied to a different population than it "
    "was fitted on: re-run the calibration check on recent matured outcomes and "
    "retrain if calibration or discrimination has moved."
)


def _is_null(v) -> bool:
    return v is None or (isinstance(v, float) and np.isnan(v))


def rates(values, absent_levels=DEFAULT_ABSENT_LEVELS) -> tuple[float, float, int]:
    """(null_rate, absent_rate, n) for one column."""
    s = pd.Series(list(values), dtype=object)
    n = int(len(s))
    if n == 0:
        return float("nan"), float("nan"), 0
    null = s.map(_is_null).to_numpy(bool)
    lv = set(absent_levels)
    absent = null | s.map(lambda v: (not _is_null(v)) and str(v) in lv).to_numpy(bool)
    return float(null.mean()), float(absent.mean()), n


@dataclass
class MissingnessRow:
    feature: str
    n_baseline: int
    n_current: int
    baseline_null_rate: float
    current_null_rate: float
    baseline_absent_rate: float
    current_absent_rate: float
    abs_delta: float
    rel_delta: float | None
    threshold_abs: float = MISSINGNESS_ABS_THRESHOLD
    threshold_rel: float = MISSINGNESS_REL_THRESHOLD
    breached: bool = False
    action: str = "—"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        for k in ("baseline_null_rate", "current_null_rate", "baseline_absent_rate",
                  "current_absent_rate", "abs_delta"):
            d[k] = round(float(d[k]), 4) if d[k] == d[k] else None
        d["rel_delta"] = None if self.rel_delta is None else round(float(self.rel_delta), 4)
        return d


def missingness_report(baseline: pd.DataFrame, current: pd.DataFrame, features,
                       *, abs_threshold: float = MISSINGNESS_ABS_THRESHOLD,
                       rel_threshold: float = MISSINGNESS_REL_THRESHOLD,
                       abs_floor: float = MISSINGNESS_ABS_FLOOR,
                       absent_levels=DEFAULT_ABSENT_LEVELS) -> list[dict]:
    """One row per feature. A feature absent from a frame is reported as a
    breach with an explicit note, because a column that vanished is the most
    complete form of missingness there is."""
    out = []
    for f in features:
        notes = []
        if f not in baseline.columns or f not in current.columns:
            side = "baseline" if f not in baseline.columns else "current"
            row = MissingnessRow(f, int(len(baseline)), int(len(current)),
                                 float("nan"), float("nan"), float("nan"), float("nan"),
                                 float("nan"), None, abs_threshold, rel_threshold,
                                 breached=True, action=ACTION_ON_BREACH,
                                 notes=[f"column absent from the {side} frame"])
            out.append(row.to_dict())
            continue
        b_null, b_abs, nb = rates(baseline[f], absent_levels)
        c_null, c_abs, nc = rates(current[f], absent_levels)
        if nb < MIN_ROWS or nc < MIN_ROWS:
            notes.append(f"fewer than {MIN_ROWS} rows on one side; not compared")
            out.append(MissingnessRow(f, nb, nc, b_null, c_null, b_abs, c_abs, float("nan"), None,
                                      abs_threshold, rel_threshold, notes=notes).to_dict())
            continue
        abs_delta = c_abs - b_abs
        rel_delta = (abs_delta / b_abs) if b_abs > 0 else (None if abs_delta == 0 else float("inf"))
        breached = (abs(abs_delta) > abs_threshold
                    or (rel_delta is not None and abs(rel_delta) > rel_threshold
                        and abs(abs_delta) > abs_floor))
        if rel_delta is not None and np.isinf(rel_delta):
            notes.append("baseline had no absent rows; relative change undefined")
        out.append(MissingnessRow(f, nb, nc, b_null, c_null, b_abs, c_abs, abs_delta,
                                  None if (rel_delta is None or np.isinf(rel_delta)) else rel_delta,
                                  abs_threshold, rel_threshold, breached=bool(breached),
                                  action=ACTION_ON_BREACH if breached else "—",
                                  notes=notes).to_dict())
    return out


def breached_features(report: list[dict]) -> list[str]:
    return [r["feature"] for r in report if r.get("breached")]
