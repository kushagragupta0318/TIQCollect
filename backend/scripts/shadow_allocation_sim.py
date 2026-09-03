"""Offline shadow comparison: which ranking would have collected more?

WHY THIS IS NOT THE ALLOCATOR
------------------------------
The obvious experiment is to run GlobalAllocator three times with a different
score in its cost matrix. That is NOT what this does, for two reasons.

First, it would mean editing production routing code to accept an alternative
scorer, which is exactly what this whole piece of work is under instruction not
to do. Second, and more fundamentally, it would not answer the question.
GlobalAllocator solves a bipartite assignment under territory radius, PTP
fatigue, visit caps, capacity and route feasibility. Swapping the score changes
WHICH agent gets a case; it does not change whether the case was worth visiting.
The counterfactual "what would agent B have collected on a case agent A actually
worked" is not in the data and cannot be simulated without assuming the very
model being tested.

So this asks the narrower question that the data CAN answer:

    A manager can only work N cases a day. Of the cases on the book at a given
    date, which N should they send agents to? Rank them three ways, take the
    top N under an identical capacity constraint, and count the money that
    actually arrived in the following 30 days.

Every constraint that is not the ranking is held identical by construction:
the same candidate pool on the same date, the same N, the same realised
outcomes. Territory, language, safety and routing are not modelled here at all
— they are hard filters applied upstream of ranking in the real allocator, and
applying them identically to all three rankings would not change the
comparison.

SYNTHETIC. Run against the synthetic database, these numbers demonstrate that
the ranking machinery works. They are not a forecast of collections uplift.

USAGE
    DATABASE_URL=...fieldops_synth python scripts/shadow_allocation_sim.py
    (or via scripts/run_synthetic_experiment.py, which sets it for you)
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_URL = os.environ.get("DATABASE_URL", "")
if "synth" not in _URL:
    raise SystemExit(
        "refusing to run: DATABASE_URL does not name a synthetic database.\n"
        f"  got: {_URL or '(unset)'}"
    )
os.environ.setdefault("SECRET_KEY", "shadow-sim")
os.environ.setdefault("COMMAND_CENTRE_API_KEY", "shadow-sim")

import numpy as np                                              # noqa: E402
import pandas as pd                                             # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier     # noqa: E402

from app.ml.train_shadow_model import (                         # noqa: E402
    FEATURE_COLS, extract_time_safe_dataset,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capacity", type=float, default=0.20,
                    help="share of the day's open cases an agency can work")
    args = ap.parse_args()

    df = extract_time_safe_dataset()
    if df.empty:
        raise SystemExit("no labelled snapshots; run the generator first")

    df = df.sort_values(by=["as_of_date", "snapshot_id"]).reset_index(drop=True)
    n = len(df)
    i_train, i_val = int(n * 0.60), int(n * 0.80)
    train_df, test_df = df.iloc[:i_train], df.iloc[i_val:]

    # Trained on the training period ONLY, then applied forward — the same
    # discipline as the trainer, so this is not quietly scoring rows the model
    # has seen.
    model = HistGradientBoostingClassifier(
        max_iter=100, learning_rate=0.05, max_depth=4, random_state=42)
    model.fit(train_df[FEATURE_COLS], train_df["y_target"])

    test = test_df[test_df["eb_shrunk_win"].notna()].copy()
    if test.empty:
        raise SystemExit("no test rows carry an EB value")
    test["score_model"] = model.predict_proba(test[FEATURE_COLS])[:, 1]

    rankings = {
        "current (scorecard)": "scorecard_baseline_likelihood",
        "EB only": "eb_shrunk_win",
        "borrower + EB": "score_model",
        "random (control)": None,
    }

    rng = np.random.default_rng(42)
    rows = []
    for label, col in rankings.items():
        worked = 0
        paid = 0
        rupees = 0.0
        # Day by day, exactly as a manager plans: today's open cases, today's
        # capacity. Ranking across the whole test period at once would let a
        # ranker "save" capacity for a better day, which no real operation can.
        for as_of, day in test.groupby("as_of_date"):
            k = max(1, int(round(len(day) * args.capacity)))
            if col is None:
                order = rng.permutation(len(day))
            else:
                order = np.argsort(-day[col].to_numpy(), kind="stable")
            top = day.iloc[order[:k]]
            worked += k
            paid += int(top["y_target"].sum())
            rupees += float(top["outcome_amount"].sum())
        rows.append({
            "ranking": label,
            "cases_worked": worked,
            "cases_that_paid": paid,
            "hit_rate": paid / max(1, worked),
            "rupees_recovered": rupees,
        })

    base = next(r for r in rows if r["ranking"] == "random (control)")
    print("\n" + "=" * 78)
    print("  SHADOW ALLOCATION SIMULATION — SYNTHETIC, NOT PRODUCTION PERFORMANCE")
    print("=" * 78)
    print(f"  test period      : {test['as_of_date'].min()} .. {test['as_of_date'].max()}")
    print(f"  candidate rows   : {len(test)} across "
          f"{test['as_of_date'].nunique()} planning dates")
    print(f"  capacity         : top {args.capacity:.0%} of each day's open cases")
    print("-" * 78)
    print(f"  {'ranking':<22}{'worked':>8}{'paid':>7}{'hit rate':>10}"
          f"{'Rs recovered':>16}{'vs random':>11}")
    for r in rows:
        lift = r["rupees_recovered"] / base["rupees_recovered"] if base["rupees_recovered"] else 0.0
        print(f"  {r['ranking']:<22}{r['cases_worked']:>8}{r['cases_that_paid']:>7}"
              f"{r['hit_rate']:>9.1%}{r['rupees_recovered']:>16,.0f}{lift:>10.2f}x")
    print("-" * 78)
    print("  Ranking only. Agent assignment, territory, routing and capacity")
    print("  per agent are the real allocator's job and are unchanged.")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()
