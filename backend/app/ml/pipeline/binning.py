# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. Monotonic optimal binning with WOE transform and IV, built
#   on optbinning rather than hand-rolled.
#
#   WHY A LIBRARY HERE, when this repo's habit is explicit readable rules.
#   Coarse classing is a constrained optimisation — maximise information subject
#   to monotonicity, a minimum bin size and a minimum event count — and a
#   hand-rolled version is where subtle bugs live: an off-by-one on the bin
#   edges, a missing bin quietly folded into the lowest bucket, an IV computed
#   with a zero in a denominator. optbinning solves it with a CP solver and
#   handles missing as a first-class bin. The judgement stays here: which
#   constraints, which gates, what to do with a non-monotonic feature.
# ───────────────────────────────────────────────────────────────────────────
"""
Weight of Evidence binning and Information Value.

    WOE(bin) = ln( P(bin | good) / P(bin | bad) )
    IV       = sum over bins of ( P(bin|good) - P(bin|bad) ) * WOE(bin)

SIGN CONVENTION. y = 1 is the RISK event (see config.py). optbinning defines
the event as y = 1, so a bin full of bad accounts gets a NEGATIVE WOE under its
convention. Everything downstream reads WOE through this module, so the
convention only has to be right once — but it is asserted in tests rather than
trusted, because a flipped WOE produces a model that is exactly as accurate and
completely backwards.

MISSING IS A BIN. Not imputed, not dropped. On this book missingness is partly
MNAR (thin files miss bureau pulls far more often), so "no record" carries real
information and gets its own WOE like any other bin.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from optbinning import OptimalBinning


class WOEBinner(BaseEstimator, TransformerMixin):
    """Fit one optimal binning per feature; transform a frame to WOE columns.

    Parameters
    ----------
    numeric, categorical
        Column names, by dtype family.
    monotonic
        "auto_asc_desc" lets the solver pick the direction but forces the bins
        to be ordered. A non-monotonic WOE curve on an ordinal feature is
        almost always noise being fitted, and it makes the resulting scorecard
        impossible to explain: "risk rises with DPD, except between 45 and 60"
        is not a rule anyone can defend.
    min_bin_fraction, min_bin_events
        A bin thinner than this cannot support a stable WOE.
    """

    def __init__(self, numeric: list[str], categorical: list[str],
                 monotonic: str = "auto_asc_desc",
                 min_bin_fraction: float = 0.05,
                 min_bin_events: int = 30,
                 max_n_bins: int = 6):
        self.numeric = list(numeric)
        self.categorical = list(categorical)
        self.monotonic = monotonic
        self.min_bin_fraction = min_bin_fraction
        self.min_bin_events = min_bin_events
        self.max_n_bins = max_n_bins

    # ── fit ─────────────────────────────────────────────────────────────────
    def fit(self, X: pd.DataFrame, y):
        y = np.asarray(y).astype(int)
        self.binners_: dict[str, OptimalBinning] = {}
        self.iv_: dict[str, float] = {}
        self.tables_: dict[str, pd.DataFrame] = {}
        self.monotonic_ok_: dict[str, bool] = {}
        self.failed_: dict[str, str] = {}

        for col in self.numeric + self.categorical:
            if col not in X.columns:
                continue
            is_cat = col in self.categorical
            x = X[col].to_numpy()
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    ob = OptimalBinning(
                        name=col,
                        dtype="categorical" if is_cat else "numerical",
                        solver="cp",
                        monotonic_trend=None if is_cat else self.monotonic,
                        min_prebin_size=max(self.min_bin_fraction / 2, 0.01),
                        min_bin_size=self.min_bin_fraction,
                        min_bin_n_event=self.min_bin_events,
                        max_n_bins=self.max_n_bins,
                    )
                    ob.fit(x, y)
                if ob.status not in ("OPTIMAL", "FEASIBLE"):
                    self.failed_[col] = f"solver status {ob.status}"
                    continue
                table = ob.binning_table.build()
                iv = float(table.loc["Totals", "IV"])
                if not np.isfinite(iv):
                    self.failed_[col] = "non-finite IV"
                    continue
                self.binners_[col] = ob
                self.iv_[col] = iv
                self.tables_[col] = table
                self.monotonic_ok_[col] = self._is_monotonic(table)
            except Exception as exc:                      # pragma: no cover
                self.failed_[col] = f"{type(exc).__name__}: {exc}"

        self.feature_names_ = [f"{c}_woe" for c in self.binners_]
        return self

    @staticmethod
    def _is_monotonic(table: pd.DataFrame) -> bool:
        """Does the event rate move in one direction across the real bins?

        Special and Missing rows are excluded — they are not part of the ordinal
        sequence and a missing bin sitting anywhere on the curve does not make
        the feature non-monotonic.
        """
        # optbinning puts "Special" / "Missing" / "" (Totals) in the Bin COLUMN,
        # not the index — the index is a plain RangeIndex. Filtering by index
        # dropped nothing, so every feature carrying a Missing bin was compared
        # against a 0.0 event rate sitting in the middle of its curve and
        # reported non-monotonic: 37 of 49 features on the first run, including
        # `dpd`, whose event rate is a textbook 0.459 -> 0.925 ascent.
        labels = table["Bin"].astype(str)
        body = table[~labels.isin(("Special", "Missing", ""))]
        rates = pd.to_numeric(body["Event rate"], errors="coerce").dropna().to_numpy()
        if len(rates) < 3:
            return True
        d = np.diff(rates)
        return bool(np.all(d >= -1e-9) or np.all(d <= 1e-9))

    # ── transform ───────────────────────────────────────────────────────────
    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(index=X.index)
        for col, ob in self.binners_.items():
            vals = (X[col].to_numpy() if col in X.columns
                    else np.full(len(X), np.nan, dtype=object))
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                out[f"{col}_woe"] = ob.transform(vals, metric="woe")
        return out[self.feature_names_]

    def get_feature_names_out(self, input_features=None):
        return np.asarray(self.feature_names_, dtype=object)

    # ── reporting ───────────────────────────────────────────────────────────
    def iv_frame(self) -> pd.DataFrame:
        """IV per feature with the classic strength reading, plus the gates.

        The `leakage_suspect` column is not decoration. In credit risk an IV
        above 0.5 on a single feature is overwhelmingly more likely to mean the
        feature knows the answer than that it is unusually good.
        """
        rows = []
        for col, iv in sorted(self.iv_.items(), key=lambda kv: -kv[1]):
            if iv < 0.02:
                strength = "unpredictive"
            elif iv < 0.10:
                strength = "weak"
            elif iv < 0.30:
                strength = "medium"
            elif iv < 0.50:
                strength = "strong"
            else:
                strength = "suspicious"
            rows.append({
                "feature": col,
                "iv": round(iv, 4),
                "strength": strength,
                "n_bins": int(len(self.tables_[col]) - 3),
                "monotonic": self.monotonic_ok_.get(col, False),
                "leakage_suspect": iv > 0.50,
            })
        return pd.DataFrame(rows)

    def bin_table(self, col: str) -> pd.DataFrame:
        """The readable coarse-classing table for one feature."""
        t = self.tables_[col].copy()
        t.insert(0, "feature", col)
        return t
