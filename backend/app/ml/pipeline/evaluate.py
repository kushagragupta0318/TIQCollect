# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. The evaluation suite a credit-risk function expects: KS,
#   Gini, a decile table in descending score order with bad rate and lift, PSI,
#   calibration, segment stability — and gates that FAIL rather than report.
#
#   THIS FILE IS WHY THE PROJECT NEEDED ONE. Before it, the only metrics in the
#   tree were ROC-AUC, PR-AUC and Brier in ml/train_shadow_model.py, and the one
#   end-to-end run on record reported a scorecard at ROC-AUC 1.0000 — Gini 1.00
#   — on a 40-row test set, next to a trained model at 0.4825. Nothing in the
#   codebase was capable of saying that the first number was the alarming one.
# ───────────────────────────────────────────────────────────────────────────
"""
Model evaluation: discrimination, rank order, stability, calibration.

CONVENTIONS, ASSUMED THROUGHOUT AND FIXED IN config.py
------------------------------------------------------
    y = 1            the RISK event (bad)
    score            higher = riskier
    decile 1         the riskiest tenth
    bad rate         must fall monotonically from decile 1 to decile 10

A "rank-order break" is a decile whose bad rate is HIGHER than the decile above
it. Breaks in the top five are counted separately because that is the end of the
book the business acts on: a model that misorders deciles 8 and 9 has made an
academic error, one that misorders 1 and 2 has sent the wrong agents to the
wrong doors.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score


# ---------------------------------------------------------------------------
# Headline discrimination
# ---------------------------------------------------------------------------

def gini(y_true, score) -> float:
    """2 * AUC - 1. Undefined on a single-class sample; returns nan, not 0.0."""
    y = np.asarray(y_true).astype(int)
    if len(np.unique(y)) < 2:
        return float("nan")
    return float(2 * roc_auc_score(y, np.asarray(score)) - 1)


def ks_statistic(y_true, score) -> tuple[float, float]:
    """Kolmogorov-Smirnov separation, as a percentage, and the cutoff score.

    Max over thresholds of |cumulative bad rate - cumulative good rate|.
    Computed on the full sorted sample rather than on deciles: a decile-level KS
    is bounded by the granularity of the deciles and systematically understates
    a well-separated model.
    """
    y = np.asarray(y_true).astype(int)
    s = np.asarray(score, dtype=float)
    if len(np.unique(y)) < 2:
        return float("nan"), float("nan")
    order = np.argsort(-s)
    y, s = y[order], s[order]
    cum_bad = np.cumsum(y) / max(y.sum(), 1)
    cum_good = np.cumsum(1 - y) / max((1 - y).sum(), 1)
    d = np.abs(cum_bad - cum_good)
    i = int(np.argmax(d))
    return float(d[i] * 100.0), float(s[i])


# ---------------------------------------------------------------------------
# The decile table — the headline artifact
# ---------------------------------------------------------------------------

def decile_table(y_true, score, n_bands: int = 10) -> pd.DataFrame:
    """Bands in DESCENDING score order: band 1 is the riskiest.

    Uses rank-based cuts rather than qcut on the score, because a score with
    ties (very common once a scorecard rounds to integer points) makes qcut
    return unequal and sometimes empty bins, and an empty band silently breaks
    the monotonicity read.
    """
    y = np.asarray(y_true).astype(int)
    s = np.asarray(score, dtype=float)
    n = len(y)
    order = np.argsort(-s, kind="mergesort")
    band = np.empty(n, dtype=int)
    edges = (np.arange(n) * n_bands) // n
    band[order] = edges + 1

    df = pd.DataFrame({"band": band, "y": y, "score": s})
    total_bad, total_good, base = y.sum(), (1 - y).sum(), y.mean()

    rows = []
    for b in range(1, n_bands + 1):
        g = df[df.band == b]
        if g.empty:
            continue
        bad, cnt = int(g.y.sum()), int(len(g))
        rows.append({
            "decile": b,
            "count": cnt,
            "events": bad,
            "non_events": cnt - bad,
            "bad_rate": bad / cnt,
            "lift": (bad / cnt) / base if base else np.nan,
            "min_score": float(g.score.min()),
            "max_score": float(g.score.max()),
            "mean_score": float(g.score.mean()),
        })
    out = pd.DataFrame(rows)
    out["cum_events"] = out.events.cumsum()
    out["cum_count"] = out["count"].cumsum()
    out["cum_bad_rate"] = out.cum_events / out.cum_count
    out["cum_pct_events"] = out.cum_events / max(total_bad, 1)
    out["cum_pct_non_events"] = out.non_events.cumsum() / max(total_good, 1)
    out["ks"] = (out.cum_pct_events - out.cum_pct_non_events).abs() * 100
    # Per-band WOE, so the table reads like a scorecard's own bin table.
    with np.errstate(divide="ignore", invalid="ignore"):
        pe = out.events / max(total_bad, 1)
        pn = out.non_events / max(total_good, 1)
        out["woe"] = np.log(np.where(pn > 0, pn, np.nan) / np.where(pe > 0, pe, np.nan))
    return out


def rank_order_breaks(dt: pd.DataFrame) -> dict:
    """Where the bad rate rises going down the table — i.e. the order is wrong."""
    r = dt.bad_rate.to_numpy()
    breaks = [int(dt.decile.iloc[i]) for i in range(1, len(r)) if r[i] > r[i - 1] + 1e-12]
    return {
        "breaks": breaks,
        "n_breaks": len(breaks),
        "n_breaks_top5": sum(1 for b in breaks if b <= 5),
        "monotonic": len(breaks) == 0,
    }


# ---------------------------------------------------------------------------
# Stability
# ---------------------------------------------------------------------------

def psi(expected, actual, n_bins: int = 10) -> float:
    """Population Stability Index between a reference and a new sample.

    Bin edges come from the EXPECTED (development) sample only. Deriving them
    from the combined data would let the new population move the ruler it is
    being measured against, which is how a drifting book reports itself stable.
    """
    e = pd.to_numeric(pd.Series(expected), errors="coerce").dropna().to_numpy()
    a = pd.to_numeric(pd.Series(actual), errors="coerce").dropna().to_numpy()
    if len(e) == 0 or len(a) == 0:
        return float("nan")
    qs = np.linspace(0, 100, n_bins + 1)
    edges = np.unique(np.percentile(e, qs))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    e_pct = np.histogram(e, bins=edges)[0] / len(e)
    a_pct = np.histogram(a, bins=edges)[0] / len(a)
    eps = 1e-6
    e_pct, a_pct = np.clip(e_pct, eps, None), np.clip(a_pct, eps, None)
    return float(np.sum((a_pct - e_pct) * np.log(a_pct / e_pct)))


def psi_frame(dev: pd.DataFrame, oot: pd.DataFrame, cols: list[str],
              warn: float = 0.10, fail: float = 0.25) -> pd.DataFrame:
    rows = []
    for c in cols:
        if c not in dev.columns or c not in oot.columns:
            continue
        v = psi(dev[c], oot[c])
        rows.append({
            "feature": c, "psi": round(v, 4),
            "verdict": ("stable" if v < warn else
                        "watch" if v < fail else "shifted"),
        })
    return pd.DataFrame(rows).sort_values("psi", ascending=False)


def calibration_table(y_true, prob, n_bands: int = 10) -> pd.DataFrame:
    """Predicted versus observed, by predicted-probability band."""
    y = np.asarray(y_true).astype(int)
    p = np.asarray(prob, dtype=float)
    n = len(y)
    order = np.argsort(p, kind="mergesort")
    band = np.empty(n, dtype=int)
    band[order] = ((np.arange(n) * n_bands) // n) + 1
    df = pd.DataFrame({"band": band, "y": y, "p": p})
    out = df.groupby("band").agg(count=("y", "size"), observed=("y", "mean"),
                                 predicted=("p", "mean")).reset_index()
    out["gap"] = out.predicted - out.observed
    return out


def segment_performance(y_true, score, segments: pd.Series) -> pd.DataFrame:
    """Gini within each level of a segment. A model dead in one segment is not
    a model that works with a caveat — it is two models, one of which failed."""
    df = pd.DataFrame({"y": np.asarray(y_true).astype(int),
                       "s": np.asarray(score, dtype=float),
                       "seg": segments.to_numpy()})
    rows = []
    for seg, g in df.groupby("seg"):
        rows.append({"segment": seg, "n": int(len(g)),
                     "bad_rate": round(float(g.y.mean()), 4),
                     "gini": round(gini(g.y, g.s), 4) if g.y.nunique() > 1 else np.nan})
    return pd.DataFrame(rows).sort_values("n", ascending=False)


# ---------------------------------------------------------------------------
# The full read, plus gates
# ---------------------------------------------------------------------------

def evaluate(y_true, score, prob=None, *, label: str = "") -> dict:
    dt = decile_table(y_true, score)
    ro = rank_order_breaks(dt)
    ks, ks_cut = ks_statistic(y_true, score)
    g = gini(y_true, score)
    out = {
        "label": label,
        "n": int(len(y_true)),
        "bad_rate": float(np.mean(y_true)),
        "gini": round(g, 4),
        "auc": round((g + 1) / 2, 4),
        "ks": round(ks, 2),
        "ks_cutoff": round(ks_cut, 4) if np.isfinite(ks_cut) else None,
        "ks_decile": int(dt.ks.idxmax() + 1) if len(dt) else None,
        "top_decile_bad_rate": round(float(dt.bad_rate.iloc[0]), 4) if len(dt) else None,
        "top_decile_lift": round(float(dt.lift.iloc[0]), 3) if len(dt) else None,
        # LIFT IS BOUNDED BY THE BAD RATE, and a threshold that ignores that is
        # meaningless. If every account in the top decile is bad, lift is
        # 1 / base_rate and no higher: on this book (bad rate 0.70) the ceiling
        # is 1.43, so a "suspicious above 5.0" gate can never fire, while on a
        # 5% book a lift of 1.35 would be close to worthless. The share of the
        # achievable maximum is the quantity that means the same thing on both.
        "max_achievable_lift": round(float(1.0 / np.mean(y_true)), 3)
        if np.mean(y_true) > 0 else None,
        # NOTE THE IDENTITY, because the name would otherwise oversell it:
        # lift x base_rate IS the top decile's bad rate. It is reported under a
        # second name because that is the bad-rate-invariant reading of lift —
        # "what share of the riskiest tenth is actually bad", which means the
        # same thing on a 5% book and a 70% one, where raw lift does not.
        "lift_capture": round(float(dt.lift.iloc[0] * np.mean(y_true)), 4)
        if len(dt) and np.mean(y_true) > 0 else None,
        "bottom_decile_bad_rate": round(float(dt.bad_rate.iloc[-1]), 4) if len(dt) else None,
        "rank_order": ro,
        "decile_table": dt,
    }
    if prob is not None:
        p = np.asarray(prob, dtype=float)
        out["brier"] = round(float(brier_score_loss(y_true, p)), 5)
        out["mean_predicted"] = round(float(p.mean()), 4)
        out["calibration_gap"] = round(float(p.mean() - np.mean(y_true)), 4)
        out["calibration_table"] = calibration_table(y_true, p)
    return out


def check_gates(ev: dict, gates, *, train_gini: float | None = None,
                max_psi: float | None = None,
                min_segment_gini: float | None = None) -> pd.DataFrame:
    """Every gate as a row: name, observed, threshold, PASS/WARN/FAIL.

    Returned as a frame rather than raised, so the model document can show the
    whole picture; train.py decides what to do with a FAIL. `gini_suspicious` is
    the one that most needs to be visible next to the others — a model failing
    it looks, on every other line, excellent.
    """
    g, ks, ro = ev["gini"], ev["ks"], ev["rank_order"]
    rows = [
        ("gini_min", g, f">= {gates.gini_min}", "PASS" if g >= gates.gini_min else "FAIL"),
        ("gini_in_target_band", g,
         f"{gates.gini_target_low}-{gates.gini_target_high}",
         "PASS" if gates.gini_target_low <= g <= gates.gini_target_high else "WARN"),
        ("gini_not_suspicious", g, f"< {gates.gini_suspicious}",
         "PASS" if g < gates.gini_suspicious else "FAIL"),
        ("ks_min", ks, f">= {gates.ks_min}", "PASS" if ks >= gates.ks_min else "FAIL"),
        ("ks_not_suspicious", ks, f"< {gates.ks_suspicious}",
         "PASS" if ks < gates.ks_suspicious else "FAIL"),
        ("rank_order_breaks_total", ro["n_breaks"], f"<= {gates.max_breaks_total}",
         "PASS" if ro["n_breaks"] <= gates.max_breaks_total else "FAIL"),
        ("rank_order_breaks_top5", ro["n_breaks_top5"], f"<= {gates.max_breaks_top5}",
         "PASS" if ro["n_breaks_top5"] <= gates.max_breaks_top5 else "FAIL"),
        ("top_decile_lift_capture", ev.get("lift_capture"),
         f">= {gates.lift_capture_min} (of max {ev.get('max_achievable_lift')}x)",
         "PASS" if (ev.get("lift_capture") or 0) >= gates.lift_capture_min else "FAIL"),
        ("top_decile_lift_not_suspicious", ev.get("lift_capture"),
         f"< {gates.lift_capture_suspicious}",
         "PASS" if (ev.get("lift_capture") or 0) < gates.lift_capture_suspicious else "FAIL"),
    ]
    if train_gini is not None:
        gap = train_gini - g
        rows.append(("train_test_gini_gap", round(gap, 4),
                     f"< {gates.max_train_test_gini_gap}",
                     "PASS" if gap < gates.max_train_test_gini_gap else "FAIL"))
    if max_psi is not None:
        rows.append(("max_feature_psi", round(max_psi, 4), f"< {gates.psi_fail}",
                     "PASS" if max_psi < gates.psi_warn else
                     "WARN" if max_psi < gates.psi_fail else "FAIL"))
    if min_segment_gini is not None:
        rows.append(("min_segment_gini", round(min_segment_gini, 4),
                     f">= {gates.segment_gini_min}",
                     "PASS" if min_segment_gini >= gates.segment_gini_min else "FAIL"))
    return pd.DataFrame(rows, columns=["gate", "observed", "threshold", "result"])
