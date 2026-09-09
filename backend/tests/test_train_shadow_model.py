# ─── CHANGELOG (prototype → product) ───
# New file, 2026-09-03. Covers ml/train_shadow_model.py after its rewrite.
#
# The bug this file guards against is the one the trainer shipped with: it read
# the LIVE Loan and Customer rows while labelling against a window that had
# already closed, so `total_outstanding` had been reduced by the very payments
# forming the label. Nothing failed. The metrics simply looked excellent.
#
# That class of fault cannot be caught by asserting on a number, because the
# number is what the fault improves. So these tests assert on the SHAPE of the
# pipeline instead: where features come from, that training refuses rather than
# proceeds when the data cannot support it, and that the holdout is
# chronological rather than random.
from datetime import date, timedelta

import pandas as pd
import pytest

from app.ml import train_shadow_model as tsm


# ── The feature contract ─────────────────────────────────────────────────────

def test_eb_is_an_input_feature_not_a_multiplier():
    """The architectural decision, pinned.

    Multiplying an agent-side EB probability by a borrower-side probability
    counts agent skill twice — the fault removed from global_allocator.py on
    the same day. EB belongs in the feature matrix, where the booster can learn
    how much it is worth, including that it is worth nothing.
    """
    assert "eb_shrunk_win" in tsm.FEATURE_COLS
    assert set(tsm.EB_FEATURES) <= set(tsm.FEATURE_COLS)


def test_eb_evidence_count_travels_with_the_estimate():
    """Below min_sample_threshold the adjuster returns the segment prior in
    place of the agent's own rate. Without the count a model cannot tell the
    two apart, and on this book the fallback is the common path."""
    assert "eb_evidence_n" in tsm.FEATURE_COLS


def test_no_feature_is_a_mutable_live_column_alias():
    """agent_id is deliberately absent: a raw agent identifier lets the model
    memorise individuals instead of learning what makes work recoverable, and
    the agent signal already enters properly through eb_shrunk_win."""
    assert "agent_id" not in tsm.FEATURE_COLS
    assert "risk_score" not in tsm.FEATURE_COLS
    assert "recovery_potential" not in tsm.FEATURE_COLS


def test_missing_numbers_become_nan_not_zero():
    """Imputing zero asserts a fact the row does not have — a borrower with an
    unknown CIBIL is not a borrower with a CIBIL of nothing. The booster
    handles NaN natively."""
    # `x != x` is the NaN identity, but `or True` made the line unfailable;
    # removed 2026-09-09. pd.isna below is the check that can actually fail.
    assert pd.isna(tsm._num(None))
    assert pd.isna(tsm._num("NONE"))
    assert tsm._num(True) == 1.0
    assert tsm._num(7) == 7.0


# ── Refusals ─────────────────────────────────────────────────────────────────

def _frame(n: int, labels=None, start=date(2026, 1, 1)) -> pd.DataFrame:
    rows = []
    for i in range(n):
        y = labels[i] if labels is not None else i % 2
        rows.append({
            "snapshot_id": f"s{i}", "loan_id": f"l{i}", "case_id": f"c{i}",
            "as_of_date": str(start + timedelta(days=i)),
            "y_target": y, "outcome": "REPAID" if y else "NO_PAYMENT",
            "outcome_amount": 1000.0 * y,
            "scorecard_baseline_likelihood": 0.6 if y else 0.4,
            "evidence_coverage": 0.8, "has_eb": 1,
            **{c: float(i % 7) for c in tsm.FEATURE_COLS},
        })
    return pd.DataFrame(rows)


def test_refuses_when_there_are_no_labels():
    """The live state as at 2026-09-03: 954 snapshots, 0 outcomes, because the
    oldest is 7 days old against a 30-day horizon. Training on that would mean
    training on nothing."""
    out = tsm.train_and_evaluate_shadow_model(pd.DataFrame())
    assert out["status"] == "insufficient_data"
    assert "no labelled snapshots" in out["reason"]


def test_refuses_below_the_minimum_row_count():
    out = tsm.train_and_evaluate_shadow_model(_frame(10))
    assert out["status"] == "insufficient_data"
    assert out["rows"] == 10


def test_refuses_when_every_label_is_the_same():
    """A single-class frame trains happily and scores perfectly. Refusing is
    the only honest answer."""
    out = tsm.train_and_evaluate_shadow_model(_frame(80, labels=[1] * 80))
    assert out["status"] == "insufficient_data"
    assert "same outcome" in out["reason"]


def test_refuses_rather_than_falling_back_to_a_random_split():
    """When the chronological holdout is single-class the metrics are
    undefined. The temptation is to shuffle — which would put later rows in
    the training set and leak the future. It refuses instead."""
    labels = [1] * 60 + [0] * 40          # sorted by date, so test side is all 0
    out = tsm.train_and_evaluate_shadow_model(_frame(100, labels=labels))
    assert out["status"] == "insufficient_data"
    assert "single-class" in out["reason"]


# ── The happy path ───────────────────────────────────────────────────────────

def test_trains_and_reports_against_the_scorecard_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(tsm, "METADATA_PATH", tmp_path / "meta.json")
    out = tsm.train_and_evaluate_shadow_model(_frame(200))

    assert out["status"] == "shadow_mode_active"
    assert (out["train_samples"] + out["validation_samples"]
            + out["test_samples"]) == 200
    # The baseline is the scorecard's own recorded likelihood, not an invented
    # step function — beating an invention would mean nothing.
    names = [c["name"] for c in out["comparators_test"]]
    assert names == ["A_scorecard", "B_eb_only", "C_borrower_plus_eb"]
    assert (tmp_path / "meta.json").exists()


def test_the_holdout_sits_strictly_after_the_training_rows():
    """Production predicts forward from past information. A random split would
    train on tomorrow to predict yesterday. Asserted across all three slices,
    not just the ends, so a future reordering cannot quietly interleave them."""
    out = tsm.train_and_evaluate_shadow_model(_frame(200))
    assert out["train_date_range"][1] < out["validation_date_range"][0]
    assert out["validation_date_range"][1] < out["test_date_range"][0]


def test_eb_coverage_is_reported_so_a_null_result_can_be_read():
    """Without these counts, "EB did not improve the model" is indistinguishable
    from "EB was missing from most rows"."""
    out = tsm.train_and_evaluate_shadow_model(_frame(200))
    assert out["rows_with_eb"] == 200
    assert "rows_with_eb_evidence_5" in out
    assert "rows_with_eb_evidence_20" in out


def test_censored_outcomes_are_excluded_from_the_label_vocabulary():
    """A bank recall is the bank's decision. Training it as "the borrower did
    not pay" teaches the model to underrate exactly the accounts pulled back
    for good reasons."""
    from app.models.repayment_snapshot import CENSORED_OUTCOMES, POSITIVE_OUTCOMES
    assert not (CENSORED_OUTCOMES & POSITIVE_OUTCOMES)
    assert "SETTLED" in CENSORED_OUTCOMES
    assert "WRITTEN_OFF" in CENSORED_OUTCOMES


def test_all_three_comparators_are_scored_on_the_same_rows():
    """A model evaluated on rows the baseline could not see is not being
    compared with it. EB is absent wherever a snapshot has no agent, so the
    comparison is restricted to rows all three can rank."""
    out = tsm.train_and_evaluate_shadow_model(_frame(200))
    counts = {c["name"]: c["n"] for c in out["comparators_test"]}
    assert len(set(counts.values())) == 1, counts


def test_eb_only_reports_no_brier_score():
    """EB estimates an expected recovery RATE, not the probability of the
    labelled event. A Brier score against a 0/1 payment label would be
    comparing two different questions and would read as EB being badly
    calibrated when it is simply answering something else."""
    out = tsm.train_and_evaluate_shadow_model(_frame(200))
    by_name = {c["name"]: c for c in out["comparators_test"]}
    assert by_name["B_eb_only"]["brier"] is None
    assert by_name["A_scorecard"]["brier"] is not None
    assert by_name["C_borrower_plus_eb"]["brier"] is not None


def test_business_lift_is_reported_against_the_book_base_rate():
    """AUC does not tell a manager anything actionable. "Of the top 10% this
    ranked, what share actually paid" does."""
    out = tsm.train_and_evaluate_shadow_model(_frame(200))
    for c in out["comparators_test"]:
        assert 0.0 <= c["top_10pct_recovery_rate"] <= 1.0
        assert c["top_10pct_lift"] is not None
        assert c["top_20pct_recall"] is not None


def test_results_carry_a_synthetic_warning_banner():
    """These numbers will be pasted into a deck. The caveat travels with them."""
    out = tsm.train_and_evaluate_shadow_model(_frame(200))
    assert "NOT evidence of real-world" in out["SYNTHETIC_WARNING"]
