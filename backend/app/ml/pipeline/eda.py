# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. Exploratory analysis and diagnostic plots, written to disk
#   as part of the artifact rather than looked at once in a notebook.
#
#   THE PLOTS ARE PART OF THE MODEL, not a presentation about it. A WOE curve
#   that is monotonic, a decile bad rate that descends, a calibration line that
#   sits on the diagonal — these are the evidence that the numbers in
#   metadata.json mean what they say. Regenerated on every train, so they can
#   never describe a previous version.
# ───────────────────────────────────────────────────────────────────────────
"""
EDA and diagnostic plotting. Every figure lands in <artifact>/eda or
<artifact>/evaluation as a PNG.

Matplotlib runs on the Agg backend — this executes in a container and in CI with
no display, and importing pyplot against an interactive backend is a classic way
to make a training job hang rather than fail.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import numpy as np                        # noqa: E402
import pandas as pd                       # noqa: E402
import seaborn as sns                     # noqa: E402

sns.set_theme(style="whitegrid", context="notebook")
PALETTE = "#2563eb"
ACCENT = "#dc2626"


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Univariate / data quality
# ---------------------------------------------------------------------------

def univariate_table(df: pd.DataFrame, numeric: list[str],
                     categorical: list[str]) -> pd.DataFrame:
    rows = []
    n = len(df)
    for c in numeric:
        if c not in df:
            continue
        s = pd.to_numeric(df[c], errors="coerce")
        rows.append({
            "feature": c, "type": "numeric", "n": n,
            "missing_pct": round(s.isna().mean() * 100, 2),
            "unique": int(s.nunique()),
            "mean": round(float(s.mean()), 4) if s.notna().any() else None,
            "std": round(float(s.std()), 4) if s.notna().any() else None,
            "p1": round(float(s.quantile(.01)), 4) if s.notna().any() else None,
            "p50": round(float(s.quantile(.50)), 4) if s.notna().any() else None,
            "p99": round(float(s.quantile(.99)), 4) if s.notna().any() else None,
        })
    for c in categorical:
        if c not in df:
            continue
        s = df[c].astype("string")
        top = s.value_counts(normalize=True)
        rows.append({
            "feature": c, "type": "categorical", "n": n,
            "missing_pct": round(s.isna().mean() * 100, 2),
            "unique": int(s.nunique()),
            "mode": top.index[0] if len(top) else None,
            "mode_share": round(float(top.iloc[0]), 4) if len(top) else None,
        })
    return pd.DataFrame(rows)


def plot_missing(df: pd.DataFrame, cols: list[str], out: Path) -> Path | None:
    miss = (df[[c for c in cols if c in df]].isna().mean() * 100).sort_values(ascending=False)
    miss = miss[miss > 0]
    if miss.empty:
        return None
    fig, ax = plt.subplots(figsize=(8, max(2.5, 0.28 * len(miss))))
    ax.barh(miss.index[::-1], miss.values[::-1], color=PALETTE)
    ax.set_xlabel("% missing")
    ax.set_title("Missingness by feature")
    return _save(fig, out)


def plot_target_over_time(df: pd.DataFrame, target: str, time_col: str,
                          out: Path) -> Path:
    g = df.groupby(time_col)[target].agg(["mean", "size"]).reset_index()
    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.plot(g[time_col], g["mean"], marker="o", color=PALETTE)
    ax.set_ylabel("bad rate")
    ax.set_xlabel(time_col)
    ax.set_title("Bad rate over time — the out-of-time window must be seen, not assumed")
    ax.set_ylim(0, 1)
    return _save(fig, out)


def plot_correlation(X: pd.DataFrame, out: Path, title: str = "Correlation matrix") -> Path:
    corr = X.corr()
    fig, ax = plt.subplots(figsize=(max(6, 0.32 * len(corr)), max(5, 0.30 * len(corr))))
    sns.heatmap(corr, cmap="RdBu_r", center=0, vmin=-1, vmax=1,
                square=True, linewidths=.4, cbar_kws={"shrink": .7}, ax=ax)
    ax.set_title(title)
    ax.tick_params(labelsize=7)
    return _save(fig, out)


def plot_iv(iv_frame: pd.DataFrame, out: Path, review_at: float = 0.5) -> Path:
    d = iv_frame.sort_values("iv").tail(30)
    colors = [ACCENT if v > review_at else PALETTE for v in d.iv]
    fig, ax = plt.subplots(figsize=(8, max(3, 0.28 * len(d))))
    ax.barh(d.feature, d.iv, color=colors)
    ax.axvline(review_at, ls="--", c=ACCENT, lw=1)
    ax.axvline(0.02, ls=":", c="gray", lw=1)
    ax.set_xlabel("Information Value")
    ax.set_title(f"IV by feature (red = above {review_at}, flagged for review)")
    return _save(fig, out)


def plot_woe_curves(binner, features: list[str], out_dir: Path,
                    max_plots: int = 12) -> list[Path]:
    """WOE and event rate per bin — the check that the binning is defensible."""
    paths = []
    for raw in features[:max_plots]:
        table = binner.tables_.get(raw)
        if table is None:
            continue
        labels = table["Bin"].astype(str)
        body = table[~labels.isin(("",))].copy()
        body["woe_n"] = pd.to_numeric(body["WoE"], errors="coerce")
        body["er"] = pd.to_numeric(body["Event rate"], errors="coerce")
        body = body[body.woe_n.notna()]
        if body.empty:
            continue
        x = np.arange(len(body))
        fig, ax1 = plt.subplots(figsize=(7, 3.2))
        ax1.bar(x, body.woe_n, color=PALETTE, alpha=.85)
        ax1.set_ylabel("WOE", color=PALETTE)
        ax1.axhline(0, c="black", lw=.8)
        ax2 = ax1.twinx()
        ax2.plot(x, body.er, marker="o", color=ACCENT)
        ax2.set_ylabel("event rate", color=ACCENT)
        ax2.grid(False)
        ax1.set_xticks(x)
        ax1.set_xticklabels([str(b)[:18] for b in body["Bin"]], rotation=30,
                            ha="right", fontsize=7)
        ax1.set_title(f"{raw} — WOE and event rate by bin")
        paths.append(_save(fig, out_dir / f"woe_{raw}.png"))
    return paths


# ---------------------------------------------------------------------------
# Model diagnostics
# ---------------------------------------------------------------------------

def plot_decile(dt: pd.DataFrame, out: Path, title: str) -> Path:
    fig, ax = plt.subplots(figsize=(8, 3.8))
    colors = [ACCENT if i == 0 else PALETTE for i in range(len(dt))]
    ax.bar(dt.decile, dt.bad_rate, color=colors)
    ax.plot(dt.decile, dt.cum_bad_rate, marker="o", color="black", lw=1.4,
            label="cumulative bad rate")
    ax.set_xticks(dt.decile)
    ax.set_xlabel("decile (1 = riskiest)")
    ax.set_ylabel("bad rate")
    ax.set_title(title)
    ax.legend(fontsize=8)
    return _save(fig, out)


def plot_ks(y_true, score, out: Path) -> Path:
    y = np.asarray(y_true).astype(int)
    s = np.asarray(score, dtype=float)
    order = np.argsort(-s)
    y = y[order]
    cum_bad = np.cumsum(y) / max(y.sum(), 1)
    cum_good = np.cumsum(1 - y) / max((1 - y).sum(), 1)
    pct = np.arange(1, len(y) + 1) / len(y) * 100
    d = np.abs(cum_bad - cum_good)
    i = int(np.argmax(d))
    fig, ax = plt.subplots(figsize=(6.5, 4))
    ax.plot(pct, cum_bad * 100, label="cumulative % bads", color=ACCENT)
    ax.plot(pct, cum_good * 100, label="cumulative % goods", color=PALETTE)
    ax.vlines(pct[i], cum_good[i] * 100, cum_bad[i] * 100, color="black", ls="--")
    ax.annotate(f"KS = {d[i]*100:.1f}", (pct[i], (cum_bad[i] + cum_good[i]) * 50),
                xytext=(8, 0), textcoords="offset points", fontsize=10)
    ax.set_xlabel("% of population, riskiest first")
    ax.set_ylabel("%")
    ax.set_title("KS curve")
    ax.legend(fontsize=8)
    return _save(fig, out)


def plot_roc(curves: dict[str, tuple], out: Path) -> Path:
    from sklearn.metrics import roc_curve
    fig, ax = plt.subplots(figsize=(5.6, 5))
    for name, (y, s) in curves.items():
        fpr, tpr, _ = roc_curve(y, s)
        g = 2 * np.trapezoid(tpr, fpr) - 1
        ax.plot(fpr, tpr, label=f"{name} (Gini {g:.3f})")
    ax.plot([0, 1], [0, 1], ls="--", c="gray", lw=1)
    ax.set_xlabel("false positive rate")
    ax.set_ylabel("true positive rate")
    ax.set_title("ROC")
    ax.legend(fontsize=8)
    return _save(fig, out)


def plot_calibration(cal: pd.DataFrame, out: Path) -> Path:
    fig, ax = plt.subplots(figsize=(5.2, 5))
    ax.plot([0, 1], [0, 1], ls="--", c="gray", lw=1, label="perfect")
    ax.plot(cal.predicted, cal.observed, marker="o", color=PALETTE, label="model")
    ax.set_xlabel("mean predicted probability")
    ax.set_ylabel("observed bad rate")
    ax.set_title("Calibration (reliability)")
    ax.legend(fontsize=8)
    return _save(fig, out)


def plot_psi(psi_df: pd.DataFrame, out: Path, warn=.10, fail=.25) -> Path:
    d = psi_df.head(25).sort_values("psi")
    colors = [ACCENT if v >= fail else "#f59e0b" if v >= warn else PALETTE
              for v in d.psi]
    fig, ax = plt.subplots(figsize=(8, max(3, 0.28 * len(d))))
    ax.barh(d.feature, d.psi, color=colors)
    ax.axvline(warn, ls=":", c="gray")
    ax.axvline(fail, ls="--", c=ACCENT)
    ax.set_xlabel("PSI (development vs out-of-time)")
    ax.set_title("Population stability")
    return _save(fig, out)


def plot_score_distribution(dev_score, oot_score, out: Path) -> Path:
    fig, ax = plt.subplots(figsize=(7, 3.6))
    sns.kdeplot(np.asarray(dev_score, dtype=float), ax=ax, label="development",
                color=PALETTE, fill=True, alpha=.25)
    sns.kdeplot(np.asarray(oot_score, dtype=float), ax=ax, label="out-of-time",
                color=ACCENT, fill=True, alpha=.20)
    ax.set_xlabel("scorecard points (higher = safer)")
    ax.set_title("Score distribution — development vs out-of-time")
    ax.legend(fontsize=8)
    return _save(fig, out)
