# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-03 — extract_time_safe_dataset rewritten. It was neither time-safe
#   nor reading the table its own docstring named.
#
#   IT NEVER TOUCHED repayment_score_snapshots. RepaymentSnapshot was imported
#   and never referenced. Every feature came from the LIVE Loan and Customer
#   rows — `loan.dpd`, `loan.total_outstanding`, `loan.overdue_amount`,
#   `loan.penal_charges` — read as they stand TODAY, and was then labelled
#   against a 30-day window opening at case.allocation_date, in some cases
#   months earlier.
#
#   That is not a subtle leak. `total_outstanding` today has been REDUCED BY
#   THE VERY PAYMENTS THAT FORM THE LABEL, and `dpd` today records whether the
#   borrower cured during the label window. The model was being handed the
#   answer and scored on how well it repeated it. A leak of this kind does not
#   fail anything — it makes the metrics look excellent and the deployment look
#   broken, which is why the snapshot table exists at all.
#
#   Two further faults in the same function:
#     * the maturity gate keyed on Case.allocation_date, which is REWRITTEN in
#       place (scripts/repair_case_timeline.py moves it to the last visit date),
#       so the "as_of" it split on was not stable.
#     * scorecard_baseline_likelihood was a hardcoded three-step function of
#       today's dpd, not the scorecard. The baseline the model had to beat was
#       therefore an invention, and beating it meant nothing.
#
#   Now: one row per labelled snapshot, features taken from the frozen
#   `features` JSON, the baseline taken from the scorecard's own recorded
#   `likelihood`, and the chronological split keyed on as_of_date — the column
#   that cannot move.
#
#   EB enters as a FEATURE (eb_shrunk_win, eb_evidence_n), not as a multiplier.
#   The agent-side estimate and a borrower-side probability are two different
#   quantities; multiplying them counts agent skill twice, which is the fault
#   already removed from services/global_allocator.py on the same day. Given
#   both columns the booster learns how much the agent signal is worth here,
#   and can learn that it is worth nothing.
#
#   The trainer now REFUSES to run without labels rather than training on an
#   empty or unlabelled frame. As at 2026-09-03 that is the live state: 0 of
#   954 snapshots carry an outcome, because the oldest is 7 days old and the
#   horizon is 30. Nothing is wrong; the table is simply too young.
# ───────────────────────────────────────────────────────────────────────────
"""
Time-Safe Borrower Recovery Modeling & Shadow Mode Evaluation Pipeline.
========================================================================
- Target Y: the repayment outcome recorded by the labeller, one horizon after
  the score. POSITIVE_OUTCOMES (REPAID, PARTIAL) are 1, NO_PAYMENT is 0, and
  censored outcomes are dropped rather than counted as failures.
- Sample filter: labelled, non-backfilled snapshots only.
- Features: frozen point-in-time borrower and loan attributes from
  repayment_score_snapshots.features, plus the point-in-time Empirical Bayes
  agent estimate attached by scripts/backfill_eb_features.py.
- Validation: chronological holdout on as_of_date, PR-AUC, Brier, and the
  scorecard's own recorded likelihood as the baseline.

SHADOW ONLY. Nothing here writes a score, and no allocation path reads it.
"""
from __future__ import annotations

import os
import sys
import json
import hashlib
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import precision_recall_curve, auc, brier_score_loss, roc_auc_score

from app.core.database import SessionLocal
from app.models.repayment_snapshot import (
    CENSORED_OUTCOMES, POSITIVE_OUTCOMES, RepaymentSnapshot,
)

MODEL_DIR = Path(__file__).parent / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
METADATA_PATH = MODEL_DIR / "shadow_model_metadata.json"

MIN_TRAINING_ROWS = 50

# Borrower/loan features, read from the FROZEN snapshot JSON — never from the
# live Loan or Customer rows, which have moved on since the score was made.
BORROWER_FEATURES = [
    "cibil_score",
    "dpd",
    "total_outstanding",
    "overdue_amount",
    "penal_charges",
    "emi_amount",
    "outstanding_principal",
    "days_since_last_payment",
    "visits",
    "visits_met",
    "adverse_visit_outcomes",
    "ptps_resolved",
    "ptps_honored",
]

# The agent-side signal. Kept separate so an ablation is one list slice away,
# and so it is obvious at a glance that EB is an INPUT here, never a factor
# applied to the output.
EB_FEATURES = ["eb_shrunk_win", "eb_evidence_n"]

FEATURE_COLS = BORROWER_FEATURES + EB_FEATURES


def _num(value) -> float:
    """Feature JSON carries None, bools and strings. NaN is the right answer for
    a missing number — HistGradientBoostingClassifier handles it natively and
    imputing a zero would assert a fact the row does not have."""
    if value is None or isinstance(value, str):
        return float("nan")
    if isinstance(value, bool):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def extract_time_safe_dataset() -> pd.DataFrame:
    """Labelled snapshots with their frozen point-in-time features."""
    db = SessionLocal()
    try:
        rows = (
            db.query(RepaymentSnapshot)
            .filter(
                RepaymentSnapshot.outcome.isnot(None),
                # Backfilled rows rebuild Loan.dpd and PTP.status from columns
                # overwritten in place, so their features cannot be made honest.
                # models/repayment_snapshot.py documents this as the reason the
                # flag exists; the documented training pull excludes them.
                RepaymentSnapshot.is_backfill.is_(False),
            )
            .order_by(RepaymentSnapshot.as_of_date)
            .all()
        )

        records = []
        censored = 0
        for row in rows:
            if row.outcome in CENSORED_OUTCOMES:
                # A bank recall or an approved settlement is the bank's
                # administrative decision. Labelling it "the borrower did not
                # pay" teaches the model that the bank's choice is the
                # borrower's fault.
                censored += 1
                continue

            feats = row.features or {}
            rec = {
                "snapshot_id": row.id,
                "loan_id": row.loan_id,
                "case_id": row.case_id,
                # as_of_date, NOT case.allocation_date: this column is the
                # point-in-time key and is never rewritten.
                "as_of_date": str(row.as_of_date),
                "y_target": 1 if row.outcome in POSITIVE_OUTCOMES else 0,
                "outcome": row.outcome,
                "outcome_amount": float(row.outcome_amount or 0.0),
                # The scorecard's OWN number, as recorded at the time. This is
                # the thing a model has to beat to be worth deploying.
                "scorecard_baseline_likelihood": float(row.likelihood) / 100.0,
                "evidence_coverage": float(row.evidence_coverage or 0.0),
                "has_eb": 1 if "eb_shrunk_win" in feats else 0,
            }
            for col in FEATURE_COLS:
                rec[col] = _num(feats.get(col))
            records.append(rec)

        df = pd.DataFrame(records)
        if censored:
            print(f"[SHADOW_MODEL] Dropped {censored} censored rows "
                  f"(bank action, not borrower conduct).")
        return df
    finally:
        db.close()


def _refuse(reason: str, **detail) -> dict:
    """Report why training did not happen, rather than training on nothing."""
    payload = {"status": "insufficient_data", "reason": reason, **detail}
    print(f"[SHADOW_MODEL] Not training: {reason}")
    for k, v in detail.items():
        print(f"[SHADOW_MODEL]   {k}: {v}")
    return payload


# ── Evaluation ───────────────────────────────────────────────────────────────
# Three comparators, scored on IDENTICAL rows so the differences mean something:
#
#   A  scorecard      the hand-weighted scorecard's own recorded likelihood.
#                     The incumbent. A model that cannot beat this is not worth
#                     deploying however good its absolute numbers look.
#   B  EB only        the agent-side estimate used alone as a ranker. This is
#                     the baseline that answers the actual question — does
#                     BORROWER information add anything beyond knowing which
#                     agent is on the case?
#   C  borrower + EB  the gradient booster over frozen borrower features plus
#                     shrunk_win and its evidence count.
#
# EB alone predicts a different quantity from A and C — an expected recovery
# RATE rather than a probability of any payment — so its Brier score is not
# comparable and is reported as None. Its RANKING metrics are comparable, and
# ranking is what an allocator actually consumes.
TOP_K_FRACTIONS = (0.10, 0.20)


def _evaluate(name: str, y_true: np.ndarray, scores: np.ndarray,
              *, probability: bool) -> dict:
    """Ranking, calibration and business-lift metrics for one comparator."""
    base_rate = float(y_true.mean())
    precision, recall, _ = precision_recall_curve(y_true, scores)
    out = {
        "name": name,
        "n": int(len(y_true)),
        "roc_auc": round(float(roc_auc_score(y_true, scores)), 4),
        "pr_auc": round(float(auc(recall, precision)), 4),
        "base_rate": round(base_rate, 4),
        # Brier and calibration only where the score claims to BE a probability
        # of the labelled event. Reporting them for a recovery-rate estimate
        # would be comparing two different questions.
        "brier": round(float(brier_score_loss(y_true, scores)), 4) if probability else None,
        "mean_predicted": round(float(scores.mean()), 4) if probability else None,
        "calibration_gap": (round(float(scores.mean() - base_rate), 4)
                            if probability else None),
    }
    # Business ranking: send the top K% of cases and see what share actually
    # paid. Lift is that share over the book's own base rate — "how much better
    # than working the list in any order".
    order = np.argsort(-scores, kind="stable")
    for frac in TOP_K_FRACTIONS:
        k = max(1, int(len(y_true) * frac))
        top = y_true[order[:k]]
        hit = float(top.mean())
        label = f"top_{int(frac * 100)}pct"
        out[f"{label}_recovery_rate"] = round(hit, 4)
        out[f"{label}_lift"] = round(hit / base_rate, 3) if base_rate > 0 else None
        out[f"{label}_recall"] = round(float(top.sum() / max(1, y_true.sum())), 4)
    return out


def train_and_evaluate_shadow_model(df: pd.DataFrame) -> dict:
    """Train on a temporal split and compare three rankers on the holdout."""
    if df.empty:
        return _refuse(
            "no labelled snapshots. The labeller fills `outcome` one horizon "
            "after the score; until a snapshot is older than that horizon "
            "there is nothing to learn from.",
            rows=0,
        )
    if len(df) < MIN_TRAINING_ROWS:
        return _refuse(f"only {len(df)} labelled rows, need {MIN_TRAINING_ROWS}",
                       rows=len(df))
    if df["y_target"].nunique() < 2:
        return _refuse("every labelled row has the same outcome; there is no "
                       "signal to separate", rows=len(df),
                       label_counts=df["y_target"].value_counts().to_dict())

    # TEMPORAL, never random: production predicts forward from past
    # information, so every holdout row must sit strictly after every training
    # row. A random split would let a borrower's later behaviour inform the
    # model that is judged on their earlier behaviour.
    #
    # Three-way. Validation exists so the test set stays untouched: nothing is
    # selected or tuned on test, and if that ever starts happening the split is
    # already here to move it onto.
    df = df.sort_values(by=["as_of_date", "snapshot_id"]).reset_index(drop=True)
    n = len(df)
    i_train, i_val = int(n * 0.60), int(n * 0.80)
    train_df = df.iloc[:i_train]
    val_df = df.iloc[i_train:i_val]
    test_df = df.iloc[i_val:]

    for part, frame in (("train", train_df), ("validation", val_df), ("test", test_df)):
        if frame["y_target"].nunique() < 2:
            return _refuse(
                f"the temporal split leaves the {part} slice single-class, so "
                "ranking metrics are undefined. More history is needed — a "
                "random split would fix the metric by leaking the future.",
                rows=n,
                label_counts={p: f["y_target"].value_counts().to_dict()
                              for p, f in (("train", train_df), ("validation", val_df),
                                           ("test", test_df))},
            )

    model = HistGradientBoostingClassifier(
        max_iter=100, learning_rate=0.05, max_depth=4, random_state=42,
    )
    model.fit(train_df[FEATURE_COLS], train_df["y_target"])

    def comparators(frame: pd.DataFrame) -> list[dict]:
        # Same rows for all three. Where EB is absent there is no agent to
        # estimate, and scoring A and C on rows B cannot see would compare
        # them on different books.
        usable = frame[frame["eb_shrunk_win"].notna()]
        if usable.empty or usable["y_target"].nunique() < 2:
            return []
        y = usable["y_target"].to_numpy()
        return [
            _evaluate("A_scorecard", y,
                      usable["scorecard_baseline_likelihood"].to_numpy(),
                      probability=True),
            _evaluate("B_eb_only", y,
                      usable["eb_shrunk_win"].to_numpy(), probability=False),
            _evaluate("C_borrower_plus_eb", y,
                      model.predict_proba(usable[FEATURE_COLS])[:, 1],
                      probability=True),
        ]

    test_results = comparators(test_df)
    val_results = comparators(val_df)
    if not test_results:
        return _refuse(
            "no test rows carry an EB value, or the EB-bearing test rows are "
            "single-class, so the three comparators cannot be scored on the "
            "same book.", rows=n)

    data_hash = hashlib.sha256(df.to_csv(index=False).encode("utf-8")).hexdigest()[:16]
    metrics = {
        "status": "shadow_mode_active",
        "model_version": "shadow_gbdt_v1.1",
        "trained_at": date.today().isoformat(),
        "dataset_hash": data_hash,
        "SYNTHETIC_WARNING": (
            "If this dataset came from scripts/run_synthetic_experiment.py these "
            "are SYNTHETIC VALIDATION RESULTS demonstrating that the pipeline "
            "works end to end. They are NOT evidence of real-world predictive "
            "performance."
        ),
        "total_labelled_samples": n,
        "train_samples": len(train_df),
        "validation_samples": len(val_df),
        "test_samples": len(test_df),
        "train_date_range": [train_df["as_of_date"].min(), train_df["as_of_date"].max()],
        "validation_date_range": [val_df["as_of_date"].min(), val_df["as_of_date"].max()],
        "test_date_range": [test_df["as_of_date"].min(), test_df["as_of_date"].max()],
        "features": FEATURE_COLS,
        "eb_features": EB_FEATURES,
        "rows_with_eb": int(df["has_eb"].sum()),
        "rows_with_eb_evidence_5": int((df["eb_evidence_n"].fillna(0) >= 5).sum()),
        "rows_with_eb_evidence_20": int((df["eb_evidence_n"].fillna(0) >= 20).sum()),
        "positive_rate": round(float(df["y_target"].mean()), 4),
        "comparators_test": test_results,
        "comparators_validation": val_results,
    }

    with open(METADATA_PATH, "w") as f:
        json.dump(metrics, f, indent=2)

    print("\n" + "=" * 78)
    print("  SHADOW MODE EVALUATION — TEMPORAL HOLDOUT")
    print("=" * 78)
    print(f"  labelled rows {n}   train {len(train_df)} / val {len(val_df)} / test {len(test_df)}")
    print(f"  train {metrics['train_date_range'][0]} .. {metrics['train_date_range'][1]}")
    print(f"  val   {metrics['validation_date_range'][0]} .. {metrics['validation_date_range'][1]}")
    print(f"  test  {metrics['test_date_range'][0]} .. {metrics['test_date_range'][1]}")
    print(f"  rows with EB {metrics['rows_with_eb']}   "
          f"n>=5 {metrics['rows_with_eb_evidence_5']}   "
          f"n>=20 {metrics['rows_with_eb_evidence_20']}")
    print(f"  positive rate {metrics['positive_rate']:.3f}")
    print("-" * 78)
    print(f"  {'comparator':<22}{'ROC':>7}{'PR':>7}{'Brier':>9}"
          f"{'top10%':>9}{'lift':>7}{'top20%':>9}{'lift':>7}")
    for r in test_results:
        brier = f"{r['brier']:.4f}" if r["brier"] is not None else "n/a"
        print(f"  {r['name']:<22}{r['roc_auc']:>7.4f}{r['pr_auc']:>7.4f}{brier:>9}"
              f"{r['top_10pct_recovery_rate']:>9.4f}{r['top_10pct_lift']:>7.2f}"
              f"{r['top_20pct_recovery_rate']:>9.4f}{r['top_20pct_lift']:>7.2f}")
    print("-" * 78)
    print(f"  Metadata: {METADATA_PATH}")
    print("=" * 78 + "\n")

    return metrics


if __name__ == "__main__":
    print("Extracting time-safe historical repayment dataset...")
    df = extract_time_safe_dataset()
    print(f"Extracted {len(df)} labelled snapshot records.")
    train_and_evaluate_shadow_model(df)
