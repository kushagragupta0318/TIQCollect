# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-16 — NEW. Production support for the interpretable GAM
#   (recovery_risk 2.2.0): the picklable model object the registry stores and
#   the engine calls, the ONE definition of the interaction constraint, the
#   exact per-tree decomposition that reason codes are built from, the
#   probability bands, and the version stamps a served score carries.
#
#   Two things the production-readiness audit found, and where each landed:
#
#   * `HistGradientBoostingClassifier` reorders columns (categoricals first)
#     when `categorical_features` is given, remaps `monotonic_cst` into that
#     order and passes `interaction_cst` through UNREMAPPED
#     (sklearn 1.6.0 gradient_boosting.py:566 vs :579). The research fit
#     declared `latest_disposition x arrears_ratio` and was given
#     `latest_disposition x last_commit_status`. `interaction_cst_for` below
#     expresses the constraint in the estimator's real index space; the
#     research scripts import it from here, and `GamModel.tree_groups` REFUSES
#     a fitted model whose trees use a pair that was not declared — so the
#     defect cannot recur silently.
#   * The engine could not load a model of this type. `GamModel.predict_proba`
#     takes the adapter's raw vector (string categoricals, None for missing),
#     which is the contract every other artifact honours.
# ───────────────────────────────────────────────────────────────────────────
"""
An exactly-additive model, served and explained.

    logit(x) = b0 + sum_j f_j(x_j) + sum_(a,b) f_ab(x_a, x_b)

Every tree the estimator holds splits on ONE feature (a singleton constraint)
or on one DECLARED pair; the decomposition is therefore a matter of summing
leaf values tree by tree and grouping by the feature set each tree used. It
is exact to floating point, deterministic, and needs no background sample to
be exact — the background (a frozen, hashed slice of the training frame)
only CENTRES the contributions so that "0" means "average borrower", which
is what a reason code has to be relative to.

Categoricals travel as the level's string; the model maps them to the code
the estimator was fitted with (`cat_levels`, in training order) and a level
it has never seen becomes the estimator's missing bin. Numerics travel as
floats, None/NaN routed by each shape function to the side it learned.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

# ── the interaction constraint, once ─────────────────────────────────────────


def remapped_order(cols: Sequence[str], cats: Iterable[str]) -> list[str]:
    """The column order the estimator actually sees: categoricals first (in
    the caller's order), then the numerics (in the caller's order)."""
    cats = set(cats)
    return [c for c in cols if c in cats] + [c for c in cols if c not in cats]


def remap_index(cols: Sequence[str], cats: Iterable[str]) -> dict[str, int]:
    """feature name -> the index the fitted trees and interaction_cst use."""
    return {c: i for i, c in enumerate(remapped_order(cols, cats))}


def interaction_cst_for(cols: Sequence[str], cats: Iterable[str],
                        pairs: Iterable[tuple[str, str]] = ()) -> list[set[int]]:
    """Singletons for every feature plus the declared pairs, expressed in the
    REMAPPED index space so the constraint lands where it is declared."""
    idx = remap_index(cols, cats)
    return [{i} for i in range(len(cols))] + [{idx[a], idx[b]} for a, b in pairs]


# ── hashing helpers, for the version stamps ─────────────────────────────────


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def frame_digest(df: pd.DataFrame) -> str:
    return hashlib.sha256(
        pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()[:16]


# ── the model ────────────────────────────────────────────────────────────────


class GamModel:
    """The object `registry.save` pickles as model.joblib.

    Parameters
    ----------
    estimator     the fitted HistGradientBoostingClassifier
    features      the model's columns, in the order the estimator was fitted
    cat_levels    {categorical: [levels in fitted code order]}
    pairs         the declared interactions
    monotonic     {feature: -1 | 0 | +1}, as passed to the estimator
    """

    def __init__(self, estimator: HistGradientBoostingClassifier, features: Sequence[str],
                 cat_levels: dict[str, list[str]], pairs: Sequence[tuple[str, str]] = (),
                 monotonic: dict[str, int] | None = None,
                 hyperparameters: dict | None = None):
        self.estimator = estimator
        self.features = list(features)
        self.cat_levels = {k: list(v) for k, v in cat_levels.items()}
        self.categorical = [c for c in self.features if c in self.cat_levels]
        self.pairs = [tuple(p) for p in pairs]
        self.monotonic = dict(monotonic or {})
        self.hyperparameters = dict(hyperparameters or {})
        # Filled by `fit_background`; None until then (the model still scores).
        self.background_means_: dict[str, float] | None = None
        self.base_logit_: float | None = None
        self.background_hash_: str | None = None
        self.background_n_: int | None = None
        self.pair_marginals_: dict[str, dict[str, np.ndarray]] = {}
        self._groups_cache: list[str] | None = None

    # sklearn-compatible surface -------------------------------------------------
    @property
    def classes_(self):
        return self.estimator.classes_

    def encode(self, X: pd.DataFrame) -> pd.DataFrame:
        """Raw adapter values -> the frame the estimator was fitted on.

        Categorical: the string level -> its training code; anything else
        (None, NaN, an unseen level) -> -1, which the estimator's own
        OrdinalEncoder turns into NaN = the missing bin. Numeric: float, with
        None -> NaN. Column order is the model's, whatever the caller's.
        """
        out = pd.DataFrame(index=range(len(X)))
        for c in self.features:
            col = X[c] if c in X.columns else pd.Series([None] * len(X))
            col = col.reset_index(drop=True)
            if c in self.cat_levels:
                s = col.where(col.notna(), None).map(
                    lambda v: v if v is None else (v.value if hasattr(v, "value") else str(v)))
                out[c] = pd.Categorical(s, categories=self.cat_levels[c]).codes.astype(np.int64)
            else:
                out[c] = pd.to_numeric(col, errors="coerce").astype(np.float64)
        return out

    def decision_function(self, X: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.estimator.decision_function(self.encode(X)), dtype=float)

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.estimator.predict_proba(self.encode(X)), dtype=float)

    # the trees, read ------------------------------------------------------------
    def _remapped(self) -> list[str]:
        return remapped_order(self.features, self.cat_levels)

    def tree_groups(self) -> list[str]:
        """One group name per tree: the feature it splits on, or "a x b" for
        a declared pair. Raises if any tree uses an UNDECLARED set — that is
        the interaction defect, and a model carrying it is not the GAM its
        metadata describes."""
        if self._groups_cache is not None:
            return self._groups_cache
        names = self._remapped()
        declared = {frozenset(p) for p in self.pairs}
        groups = []
        for (pred,) in self.estimator._predictors:
            nodes = pred.nodes
            # `is_leaf` is a uint8 field, so `~` would be bitwise; compare instead.
            split = nodes["feature_idx"][nodes["is_leaf"] == 0]
            used = frozenset(names[int(i)] for i in np.unique(split))
            if len(used) == 0:
                groups.append("__constant__")
            elif len(used) == 1:
                groups.append(next(iter(used)))
            elif used in declared:
                a, b = next(p for p in self.pairs if frozenset(p) == used)
                groups.append(f"{a} x {b}")
            else:
                raise ValueError(
                    f"a tree splits on {sorted(used)}, which is not a declared interaction "
                    f"{self.pairs}: this is not the additive model its metadata describes")
        self._groups_cache = groups
        return groups

    def group_names(self) -> list[str]:
        return self.features + [f"{a} x {b}" for a, b in self.pairs]

    def raw_contributions(self, X: pd.DataFrame) -> pd.DataFrame:
        """Per-row, per-group sums of leaf values, UNCENTRED, plus `baseline`.
        `baseline + sum(groups) == decision_function(X)` exactly."""
        est = self.estimator
        Xt = est._preprocess_X(self.encode(X), reset=False)
        known_cat_bitsets, f_idx_map = est._bin_mapper.make_known_categories_bitsets()
        out = {g: np.zeros(len(Xt)) for g in self.group_names()}
        for (pred,), g in zip(est._predictors, self.tree_groups()):
            vals = pred.predict(Xt, known_cat_bitsets=known_cat_bitsets,
                                f_idx_map=f_idx_map, n_threads=1)
            if g == "__constant__":
                out.setdefault("__constant__", np.zeros(len(Xt)))
            out[g] = out.get(g, 0.0) + vals
        df = pd.DataFrame(out)
        df.insert(0, "baseline", float(np.ravel(est._baseline_prediction)[0]))
        return df

    # background centring ----------------------------------------------------------
    def _grid(self, feature: str) -> tuple[list, np.ndarray | None]:
        """Representative values, one per fitted bin (or level), plus missing.
        The trees compare `x <= threshold`, so bin i is (th[i-1], th[i]] and
        its midpoint represents it exactly; `_bin_of` is the matching lookup."""
        if feature in self.cat_levels:
            return list(self.cat_levels[feature]) + [None], None
        th = np.asarray(self.estimator._bin_mapper.bin_thresholds_[self._remapped().index(feature)], dtype=float)
        if len(th) == 0:
            return [0.0, None], th
        reps = ([float(th[0]) - 1.0]
                + [float((th[i - 1] + th[i]) / 2) for i in range(1, len(th))]
                + [float(th[-1]) + 1.0])
        return reps + [None], th

    def _bin_of(self, feature: str, values: pd.Series, th: np.ndarray | None) -> np.ndarray:
        """Index into `_grid(feature)` for each value; the last slot is missing."""
        if feature in self.cat_levels:
            codes = self.encode(values.to_frame(feature))[feature].to_numpy()
            return np.where(codes < 0, len(self.cat_levels[feature]), codes)
        v = pd.to_numeric(values, errors="coerce").to_numpy(float)
        if th is None or len(th) == 0:
            return np.where(np.isnan(v), 1, 0)
        idx = np.searchsorted(th, v, side="left")
        return np.where(np.isnan(v), len(th) + 1, idx)

    def fit_background(self, background: pd.DataFrame) -> "GamModel":
        """Freeze the reference population the contributions are centred on.

        Stores the per-group background means, the hash of the rows, and —
        for every declared pair — the MARGINAL of the pair function over the
        background on each member's own grid, so that a pair tree's main
        effects are attributed to the features they belong to and only the
        residual (what the pair adds beyond the two marginals) is reported as
        the interaction. Boosting lets a pair tree carry a feature's whole
        main effect (this model puts all of `latest_disposition` in its pair
        trees); without the split a reason code would call that an
        interaction, which it is not.
        """
        rc = self.raw_contributions(background)
        self.background_means_ = {g: float(rc[g].mean()) for g in rc.columns if g != "baseline"}
        self.base_logit_ = float(rc.sum(axis=1).mean())
        self.background_hash_ = frame_digest(self.encode(background))
        self.background_n_ = int(len(background))
        self.pair_marginals_ = {}
        for a, b in self.pairs:
            name = f"{a} x {b}"
            tables = {}
            for f in (a, b):
                grid, _ = self._grid(f)
                vals = []
                for g in grid:
                    B = background.copy()
                    B[f] = [g] * len(B)
                    vals.append(float(self.raw_contributions(B)[name].mean()) - self.background_means_[name])
                tables[f] = np.asarray(vals)
            self.pair_marginals_[name] = tables
        return self

    def contributions(self, X: pd.DataFrame) -> pd.DataFrame:
        """Centred contributions: `intercept + sum(groups) == logit` exactly.

        `intercept` is the background's mean logit; a feature's column is its
        singleton trees minus their background mean, plus (for a pair member)
        the pair's marginal on that feature; a pair's column is the residual
        interaction — the pair trees minus their background mean minus both
        marginals. The parts telescope to the logit by construction.
        """
        if self.background_means_ is None:
            raise RuntimeError("fit_background() has not been called on this model")
        rc = self.raw_contributions(X)
        out = pd.DataFrame({g: rc[g] - m for g, m in self.background_means_.items()})
        for a, b in self.pairs:
            name = f"{a} x {b}"
            tables = getattr(self, "pair_marginals_", {}).get(name)
            if not tables:
                continue
            for f in (a, b):
                _, th = self._grid(f)
                col = X[f] if f in X.columns else pd.Series([None] * len(X))
                marg = tables[f][self._bin_of(f, col.reset_index(drop=True), th)]
                out[f] = out[f] + marg
                out[name] = out[name] - marg
        out.insert(0, "intercept", self.base_logit_)
        # The logit the probability was computed from — the estimator's own
        # sum, so `sigmoid(logit) == predict_proba` bit for bit; the parts
        # sum to it within floating-point reassociation (~1e-15).
        out["logit"] = self.decision_function(X)
        return out

    # shape functions ----------------------------------------------------------------
    def shape_function(self, feature: str, grid: Sequence | None = None) -> pd.DataFrame:
        """f_j evaluated on a grid (default: the fitted bin thresholds / the
        levels), centred on the background. The explanation a reviewer reads."""
        if feature in self.cat_levels or grid is None:
            grid, _ = self._grid(feature)
        X = pd.DataFrame({feature: list(grid)})
        for c in self.features:
            if c != feature:
                X[c] = None
        if self.background_means_ is not None:
            f = self.contributions(X)[feature]
        else:
            f = self.raw_contributions(X)[feature]
        return pd.DataFrame({"value": ["<missing>" if v is None else v for v in grid],
                             "f": f.round(6).to_numpy()})

    def to_dict(self) -> dict:
        return {
            "features": self.features, "categorical": self.categorical,
            "cat_levels": self.cat_levels, "pairs": [list(p) for p in self.pairs],
            "monotonic": self.monotonic, "hyperparameters": self.hyperparameters,
            "n_trees": len(self.estimator._predictors),
            "background": {"n": self.background_n_, "hash": self.background_hash_,
                           "base_logit": self.base_logit_},
        }


# ── fitting ─────────────────────────────────────────────────────────────────


def encode_frame(df: pd.DataFrame, features: Sequence[str], cat_levels: dict[str, list[str]]) -> pd.DataFrame:
    """The training-side encoding: identical arithmetic to GamModel.encode,
    written out so the trainer does not need a model to exist yet."""
    X = df[list(features)].copy().reset_index(drop=True)
    for c in features:
        if c in cat_levels:
            X[c] = pd.Categorical(X[c].astype(str), categories=cat_levels[c]).codes.astype(np.int64)
        else:
            X[c] = pd.to_numeric(X[c], errors="coerce").astype(np.float64)
    return X


GAM_HYPERPARAMETERS = dict(learning_rate=0.05, min_samples_leaf=300, l2_regularization=1.0,
                           max_bins=32, random_state=0, early_stopping=False)


def fit_gam(X_tr: pd.DataFrame, y_tr, X_va: pd.DataFrame, y_va, *, features: Sequence[str],
            cat_levels: dict[str, list[str]], signs: dict[str, int],
            pairs: Sequence[tuple[str, str]] = (), max_iter: int = 1500,
            select_iterations=None) -> tuple[GamModel, int, list[float]]:
    """Fit on train, choose the number of trees on validation, refit.

    `select_iterations(staged_scores) -> int` picks the iteration count from
    the per-iteration validation scores; default = argmax validation KS.
    Returns the model, the chosen iteration count and the validation curve.
    """
    from app.ml.pipeline import evaluate as ev

    cats = set(cat_levels)
    cst = interaction_cst_for(features, cats, pairs)
    leaves, depth = (3, 1) if not pairs else (4, 2)
    mono = [0 if c in cats else int(signs.get(c, 0)) for c in features]
    params = dict(GAM_HYPERPARAMETERS, max_leaf_nodes=leaves, max_depth=depth,
                  categorical_features=[c in cats for c in features],
                  monotonic_cst=mono, interaction_cst=cst)
    Xtr = encode_frame(X_tr, features, cat_levels)
    Xva = encode_frame(X_va, features, cat_levels)
    probe = HistGradientBoostingClassifier(**params, max_iter=max_iter).fit(Xtr, y_tr)
    curve = [float(ev.ks_statistic(y_va, q[:, 1])[0]) for q in probe.staged_predict_proba(Xva)]
    it = int(select_iterations(curve)) if select_iterations else int(np.argmax(curve)) + 1
    est = HistGradientBoostingClassifier(**params, max_iter=it).fit(Xtr, y_tr)
    model = GamModel(est, features, cat_levels, pairs=pairs,
                     monotonic={c: m for c, m in zip(features, mono)},
                     hyperparameters={**params, "max_iter": it,
                                      "interaction_cst": [sorted(s) for s in cst]})
    model.tree_groups()          # raises if the fit is not the declared GAM
    return model, it, curve


# ── bands ───────────────────────────────────────────────────────────────────

#: Share of the development book per band, SAFEST FIRST — the same shares the
#: scorecard uses (`scorecard.BAND_SHARES`), so "band A" means the same slice
#: of the book whichever model form produced it.
BAND_SHARES = [("A", 0.15), ("B", 0.20), ("C", 0.25), ("D", 0.25), ("E", 0.15)]


@dataclass
class ProbabilityBands:
    """Bands on P(bad): higher probability = higher risk = later letter.

    `edges` are the upper bounds of A..D on the development distribution; E
    is everything above the last edge. Assignment is a searchsorted, so it is
    deterministic and O(log n), and the table records what each band meant
    on the data it was cut on.
    """
    edges: list[float]
    version: str
    direction: str = "higher probability = higher risk; A safest, E riskiest"
    table: list[dict] = field(default_factory=list)
    fitted_on: str = ""

    @classmethod
    def fit(cls, prob: np.ndarray, y=None, *, version: str, fitted_on: str = "") -> "ProbabilityBands":
        p = np.asarray(prob, dtype=float)
        edges, acc = [], 0.0
        for _, share in BAND_SHARES[:-1]:
            acc += share
            edges.append(float(np.quantile(p, acc)))
        b = cls(edges=edges, version=version, fitted_on=fitted_on)
        labels = b.assign(p)
        rows = []
        for name, _ in BAND_SHARES:
            m = labels == name
            if not m.any():
                continue
            row = {"band": name, "count": int(m.sum()), "share": round(float(m.mean()), 4),
                   "min_probability": round(float(p[m].min()), 6),
                   "max_probability": round(float(p[m].max()), 6)}
            if y is not None:
                row["bad_rate"] = round(float(np.asarray(y)[m].mean()), 4)
            rows.append(row)
        b.table = rows
        return b

    def assign(self, prob) -> np.ndarray:
        p = np.asarray(prob, dtype=float)
        idx = np.searchsorted(np.asarray(self.edges), p, side="right")
        names = np.array([n for n, _ in BAND_SHARES])
        return names[np.clip(idx, 0, len(names) - 1)]

    def assign_one(self, prob: float) -> str:
        return str(self.assign(np.array([prob]))[0])

    def to_dict(self) -> dict:
        # Edges at full precision: the engine loads them from this dict, and
        # a rounded edge would band a probability within 1e-8 of it
        # differently from the trainer's reference.
        return {"version": self.version, "direction": self.direction,
                "edges": [float(e) for e in self.edges],
                "mapping": [{"band": n, "upper_exclusive": (float(self.edges[i]) if i < len(self.edges) else None)}
                            for i, (n, _) in enumerate(BAND_SHARES)],
                "shares_targeted": dict(BAND_SHARES), "table": self.table,
                "fitted_on": self.fitted_on, "edges_hash": _digest(self.edges)}

    @classmethod
    def from_dict(cls, d: dict) -> "ProbabilityBands":
        return cls(edges=[float(e) for e in d["edges"]], version=d["version"],
                   direction=d.get("direction", cls.direction), table=d.get("table", []),
                   fitted_on=d.get("fitted_on", ""))


# ── reason codes ────────────────────────────────────────────────────────────

#: Plain words for each input, for the reason-code text. A feature with no
#: entry is reported under its column name — never silently dropped.
FEATURE_LABELS = {
    "arrears_ratio": "instalments in arrears",
    "cibil_score": "bureau score",
    "no_answer_streak": "consecutive unanswered calls",
    "overdue_amount": "overdue amount",
    "intent_calls_3m": "calls with stated intent to pay (3 months)",
    "calls_3m": "call attempts (3 months)",
    "paid_ratio_3m": "share of instalments paid (3 months)",
    "ptp_amount_to_emi": "size of promises relative to the instalment",
    "days_since_last_contact": "days since the borrower was last met",
    "interest_rate": "interest rate",
    "latest_disposition": "latest stated disposition",
    "last_commit_status": "how the last verbal commitment ended",
    "recent_ptp_status": "how the last promise to pay ended",
    "employment_type": "employment type",
    "disposition_recency_class": "latest disposition and its freshness",
}

REASON_CODE_VERSION = "recovery-reasons-1.0.0"


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "not available"
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:,.0f}"
    return str(v)


def reason_codes(contrib_row: pd.Series, values: dict, pairs: Sequence[tuple[str, str]],
                 top_n: int = 4, min_abs: float = 0.02) -> list[dict]:
    """The largest centred contributions, in either direction, with words.

    Deterministic: sorted by |contribution| descending, ties by name. The
    numbers reconcile with the score exactly — `intercept + sum(all
    contributions) == logit` — and each code carries its own contribution, so
    a reader can add the listed ones back and see what the rest amounts to.
    """
    rows = []
    for g, v in contrib_row.items():
        if g in ("intercept", "logit"):
            continue
        c = float(v)
        if abs(c) < min_abs:
            continue
        if " x " in g:
            a, b = g.split(" x ")
            label = f"{FEATURE_LABELS.get(a, a)} together with {FEATURE_LABELS.get(b, b)}"
            value = f"{_fmt(values.get(a))} / {_fmt(values.get(b))}"
            kind = "interaction"
        else:
            label = FEATURE_LABELS.get(g, g)
            value = _fmt(values.get(g))
            kind = "feature"
        direction = "increases_risk" if c > 0 else "decreases_risk"
        rows.append({"feature": g, "kind": kind, "value": value, "contribution": round(c, 4),
                     "direction": direction,
                     "text": f"{label} ({value}) {'raises' if c > 0 else 'lowers'} "
                             f"the chance of no payment ({c:+.2f} on the log-odds)"})
    rows.sort(key=lambda r: (-abs(r["contribution"]), r["feature"]))
    for i, r in enumerate(rows[:top_n], 1):
        r["rank"] = i
    return rows[:top_n]


# ── points, for display beside the band ─────────────────────────────────────


def points_from_logit(logit, *, pdo: int = 20, base_score: int = 600, base_odds: float = 50.0) -> np.ndarray:
    """The scorecard's scaling applied to the GAM's logit, so `points` reads
    the same way on every recovery model: higher = safer. NOT a scorecard —
    there is no per-feature points table — and the band is cut on the
    calibrated probability, not on this."""
    factor = pdo / np.log(2)
    offset = base_score - factor * np.log(base_odds)
    return np.round(offset - factor * np.asarray(logit, dtype=float)).astype(int)
