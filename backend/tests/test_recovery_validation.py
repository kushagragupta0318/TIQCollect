# ─── CHANGELOG (prototype → product) ───
# New file, 2026-08-24. Covers ml/recovery_validation.py — the instrument that
# will decide whether the recovery scorecard earns its place.
#
# Written BEFORE the outcomes exist, which is the point: a measuring instrument
# built after seeing the data is a measuring instrument chosen to flatter it.
#
# Three properties carry the weight here, and each has a test whose docstring
# states the harm:
#
#   * RANKING IS SCALE-INVARIANT. If a monotone rescaling of the predictions
#     moved the ranking metrics, they would be mixing in calibration and the
#     whole separation this module exists for would be fiction.
#   * ADMISSIBILITY IS EXHAUSTIVE. Every excluded row must give a reason. A row
#     that silently vanishes is a row that biases the result invisibly.
#   * UNOBSERVABLE IS NOT ZERO. A caseless loan recorded as recovering nothing
#     would flatter exactly the band it hurts most.
#
# No-DB style, matching test_recovery_scorecard.py: pure functions over
# SimpleNamespace stand-ins.
from types import SimpleNamespace as NS

import pytest
from tests._db import test_id

from app.ml.recovery_validation import (
    ADMISSIBLE, BACKFILL, CENSORED, IMMATURE, MIN_ADMISSIBLE_PER_BAND,
    NO_DENOMINATOR, UNOBSERVABLE, VERSION_MISMATCH, EXPECTED_VERSION,
    Observation, calibration_metrics, classify, diagnose,
    factor_residual_correlation, marginal_lift_by_security, mean_with_ci,
    observation_from, pearson, ranking_metrics, spearman,
    summarise_admissibility, top_k_capture,
)

VERSION = EXPECTED_VERSION


def row(**over):
    """A snapshot row that IS admissible at 30 days, unless overridden."""
    base = dict(
        loan_id=test_id("l1"), recovery_potential="HIGH",
        recovery_model_version=VERSION, is_backfill=False,
        recovery_labelled_through_days=30, outcome="NO_PAYMENT",
        recovery_rate_30=0.25, recovery_rate_60=0.40, recovery_rate_90=0.60,
        recovered_amount_30=50_000.0, recovered_amount_60=None,
        recovered_amount_90=None,
        features={"total_outstanding": 500_000.0},
        recovery_contributions={"factors": [
            {"code": "SECURITY", "points": 0.16},
            {"code": "AGEING", "points": -0.05},
            {"code": "LEGAL_POSTURE", "abstained": True, "reason": "NO_LEGAL_ACTION"},
        ]},
    )
    base.update(over)
    return NS(**base)


def obs(band="HIGH", predicted=0.5, realised=0.5, outstanding=1_000_000.0,
        secured="SECURED", **factors):
    return Observation(loan_id="l", band=band, predicted_rate=predicted,
                       realised_rate=realised, outstanding=outstanding,
                       secured=secured, factors=factors)


# ── Admissibility: every exclusion has a reason ──────────────────────────────
def test_a_complete_row_is_admissible():
    assert classify(row(), horizon=30) == ADMISSIBLE


def test_an_unmatured_horizon_is_immature_not_missing():
    """The 60-day amount is NULL and the marker is behind it: the label has not
    been written yet. Treating that as a zero would score the scorecard against
    money that has not had time to arrive."""
    assert classify(row(), horizon=60) == IMMATURE


def test_a_matured_null_is_unobservable_not_zero():
    """THE DISTINCTION THAT MATTERS. Marker at or past the horizon with a NULL
    amount means the ledger could never see this loan — Payment.case_id is NOT
    NULL, so a caseless loan has no reachable payments. Recording it as ₹0 would
    put a fact in the analysis that is not one, and on the 2026-08-24 cohort it
    would bias LOW hardest (50 of 115 caseless loans are LOW against 26 HIGH),
    flattering the scorecard's separation."""
    assert classify(row(recovered_amount_30=None,
                        recovery_labelled_through_days=30),
                    horizon=30) == UNOBSERVABLE


def test_zero_recovered_is_admissible_not_excluded():
    """0.0 is a real observation: money could have arrived and did not. It must
    enter the analysis, or the scorecard is only ever measured on its wins."""
    assert classify(row(recovered_amount_30=0.0), horizon=30) == ADMISSIBLE


def test_censored_rows_are_excluded():
    """A bank recall or approved settlement means no money arrived for a reason
    that is not the borrower's conduct. Counting it as a failed recovery would
    measure the bank's own decisions as the scorecard's error."""
    for outcome in ("SETTLED", "WRITTEN_OFF", "RECALLED", "DECEASED", "CENSORED"):
        assert classify(row(outcome=outcome), horizon=30) == CENSORED, outcome


def test_non_censoring_outcomes_stay_admissible():
    for outcome in ("REPAID", "PARTIAL", "NO_PAYMENT", None, ""):
        assert classify(row(outcome=outcome), horizon=30) == ADMISSIBLE, outcome


def test_a_different_scorecard_version_is_excluded():
    """Pooling versions would average two different scorecards and call the
    result one measurement. v1.0.0 rows exist in principle — the momentum fix
    changed factor behaviour — so this is not hypothetical."""
    assert classify(row(recovery_model_version="recovery-scorecard-1.0.0"),
                    horizon=30) == VERSION_MISMATCH


def test_backfilled_rows_are_excluded():
    """Backfilled features leak the future through Loan.dpd and PTP.status, both
    overwritten in place with no history."""
    assert classify(row(is_backfill=True), horizon=30) == BACKFILL


def test_a_zero_balance_has_no_definable_rate():
    """The rate's denominator is total_outstanding. Zero would divide by zero or,
    worse, silently produce a rate of 0."""
    assert classify(row(features={"total_outstanding": 0.0}),
                    horizon=30) == NO_DENOMINATOR
    assert classify(row(features={}), horizon=30) == NO_DENOMINATOR


def test_version_is_checked_before_anything_else():
    """A wrong-version row must not be reported as merely immature — the reason
    a row was dropped drives what you do about it."""
    assert classify(row(recovery_model_version="other", recovered_amount_30=None,
                        recovery_labelled_through_days=0),
                    horizon=30) == VERSION_MISMATCH


def test_admissibility_summary_accounts_for_every_row():
    """Exclusions before findings, and they must add up. A row that vanishes
    without a reason biases the result invisibly."""
    counts = {ADMISSIBLE: 353, UNOBSERVABLE: 115, CENSORED: 57}
    s = summarise_admissibility(counts)
    assert s["total"] == 525
    assert s["admissible"] == 353
    assert s["excluded"] == 172
    assert abs(s["admissible_share"] - 353 / 525) < 1e-9


# ── Observation construction ────────────────────────────────────────────────
def test_realised_rate_uses_the_frozen_denominator():
    """50,000 recovered against the 500,000 balance AS SCORED, not today's. Using
    the live balance would compare against a denominator that has since moved."""
    o = observation_from(row(), horizon=30)
    assert o.realised_rate == pytest.approx(0.10)
    assert o.predicted_rate == 0.25
    assert o.residual == pytest.approx(-0.15)   # scorecard over-called


def test_security_is_read_from_the_contribution_sign():
    assert observation_from(row(), horizon=30).secured == "SECURED"
    assert observation_from(row(recovery_contributions={"factors": [
        {"code": "SECURITY", "points": -0.16}]}), horizon=30).secured == "UNSECURED"
    assert observation_from(row(recovery_contributions={"factors": [
        {"code": "SECURITY", "abstained": True}]}), horizon=30).secured == "UNDETERMINED"


def test_abstained_factors_are_not_contributions():
    """An abstention is the absence of evidence, not a contribution of zero.
    Including it as 0.0 would drag every factor-residual correlation toward
    nothing."""
    assert "LEGAL_POSTURE" not in observation_from(row(), horizon=30).factors


# ── Ranking is scale-invariant ──────────────────────────────────────────────
def test_ranking_is_unchanged_by_rescaling_the_predictions():
    """THE PROPERTY THE WHOLE MODULE RESTS ON. Halve every prediction and the
    ordering metrics must not move — otherwise they are contaminated by
    calibration and 'the ranking works' would be an untestable claim."""
    base = [obs(band=b, predicted=p, realised=r) for b, p, r in [
        ("HIGH", 0.70, 0.55), ("HIGH", 0.60, 0.48), ("MEDIUM", 0.40, 0.30),
        ("MEDIUM", 0.35, 0.26), ("LOW", 0.15, 0.08), ("LOW", 0.10, 0.05)]]
    halved = [obs(band=o.band, predicted=o.predicted_rate / 2,
                  realised=o.realised_rate) for o in base]

    a, b = ranking_metrics(base), ranking_metrics(halved)
    assert a["spearman"] == pytest.approx(b["spearman"])
    assert a["monotonic"] == b["monotonic"]
    assert a["high_low_lift"] == pytest.approx(b["high_low_lift"])


def test_calibration_does_move_when_predictions_are_rescaled():
    """The mirror image, and the reason both are reported. Rescaling must change
    the calibration numbers — if it did not, calibration would be measuring
    nothing."""
    base = [obs(predicted=0.6, realised=0.3), obs(predicted=0.4, realised=0.2),
            obs(predicted=0.2, realised=0.1)]
    halved = [obs(predicted=o.predicted_rate / 2, realised=o.realised_rate)
              for o in base]
    assert calibration_metrics(base)["bias"] != pytest.approx(
        calibration_metrics(halved)["bias"])


def test_monotonicity_and_lift_detect_a_working_scorecard():
    good = ([obs(band="HIGH", predicted=0.7, realised=0.60)] * 5
            + [obs(band="MEDIUM", predicted=0.4, realised=0.30)] * 5
            + [obs(band="LOW", predicted=0.1, realised=0.10)] * 5)
    rk = ranking_metrics(good)
    assert rk["monotonic"] is True
    assert rk["high_low_lift"] == pytest.approx(6.0)


def test_monotonicity_fails_when_the_order_inverts():
    """The failure this must catch: LOW recovering more than HIGH."""
    bad = ([obs(band="HIGH", realised=0.10)] * 5
           + [obs(band="MEDIUM", realised=0.30)] * 5
           + [obs(band="LOW", realised=0.50)] * 5)
    assert ranking_metrics(bad)["monotonic"] is False


def test_thin_bands_are_named_not_silently_reported():
    """LOW projects to 68 admissible loans against a bar of 100. The report must
    say so rather than print a mean that reads as solid."""
    thin = ([obs(band="HIGH")] * 120 + [obs(band="MEDIUM")] * 150
            + [obs(band="LOW")] * 68)
    assert ranking_metrics(thin)["insufficient_bands"] == ["LOW"]
    assert MIN_ADMISSIBLE_PER_BAND == 100


# ── Statistics ──────────────────────────────────────────────────────────────
def test_mean_with_ci_brackets_the_mean():
    s = mean_with_ci([0.1, 0.2, 0.3, 0.4, 0.5] * 10)
    assert s["mean"] == pytest.approx(0.3)
    assert s["lo"] < 0.3 < s["hi"]
    assert s["n"] == 50 and s["small_sample"] is False


def test_small_samples_are_flagged():
    """A mean from 12 loans is not a finding. The flag exists so the caller can
    refuse to conclude from it."""
    assert mean_with_ci([0.2] * 12)["small_sample"] is True
    assert mean_with_ci([])["n"] == 0


def test_spearman_is_one_for_any_monotone_relationship():
    """Rank correlation, not linear: a perfectly ordered but wildly non-linear
    relationship must score 1.0."""
    x = [1, 2, 3, 4, 5, 6]
    assert spearman(x, [1, 4, 9, 16, 25, 36]) == pytest.approx(1.0)
    assert spearman(x, [36, 25, 16, 9, 4, 1]) == pytest.approx(-1.0)


def test_spearman_handles_ties():
    """Bands produce heavy ties. Without average ranks the correlation is wrong
    in a way that is easy to miss."""
    rho = spearman([1, 1, 2, 2, 3, 3], [1, 1, 2, 2, 3, 3])
    assert rho == pytest.approx(1.0)


def test_correlations_refuse_degenerate_input():
    assert spearman([1, 2], [1, 2]) is None            # too few
    assert pearson([1, 1, 1], [1, 2, 3]) is None       # no variance
    assert spearman([], []) is None


def test_calibration_slope_is_one_when_perfectly_calibrated():
    perfect = [obs(predicted=p, realised=p) for p in (0.1, 0.2, 0.3, 0.4, 0.5)]
    cal = calibration_metrics(perfect)
    assert cal["bias"] == pytest.approx(0.0)
    assert cal["slope"] == pytest.approx(1.0)


def test_a_uniform_overprediction_shows_as_bias_not_slope():
    """The signature of a BASE_RATE error: every prediction 20pp too high, so the
    bias is -0.20 and the slope is still 1.0. This is the pattern that justifies
    an intercept shift and nothing else."""
    over = [obs(predicted=p + 0.20, realised=p) for p in (0.1, 0.2, 0.3, 0.4, 0.5)]
    cal = calibration_metrics(over)
    assert cal["bias"] == pytest.approx(-0.20)
    assert cal["slope"] == pytest.approx(1.0)


def test_over_spread_predictions_show_as_a_slope_below_one():
    """The signature of a WEIGHT-SCALE error: the scorecard is more confident
    about differences than the outcomes justify. Distinct from a bias, and fixed
    with a different lever."""
    spread = [obs(predicted=0.5 + (p - 0.3) * 2, realised=p)
              for p in (0.1, 0.2, 0.3, 0.4, 0.5)]
    assert calibration_metrics(spread)["slope"] < 1.0


# ── Operational lift ────────────────────────────────────────────────────────
def test_top_k_capture_beats_the_baseline_when_ranking_works():
    """Ten loans, and the money sits where the predictions said it would."""
    good = [obs(predicted=0.9, realised=0.9, outstanding=1e6)] * 2 + \
           [obs(predicted=0.1, realised=0.05, outstanding=1e6)] * 8
    cap = top_k_capture(good, k=0.2)
    assert cap["loans_in_top_k"] == 2
    assert cap["captured"] > 0.6 and cap["lift"] > 3.0


def test_top_k_capture_equals_the_baseline_when_ranking_is_useless():
    """Identical predictions carry no information, so the top 20% holds ~20% of
    the money. This is the number that says 'the label does not change the work'."""
    flat = [obs(predicted=0.4, realised=0.4, outstanding=1e6) for _ in range(10)]
    cap = top_k_capture(flat, k=0.2)
    assert cap["captured"] == pytest.approx(0.2)
    assert cap["lift"] == pytest.approx(1.0)


def test_top_k_capture_reports_rather_than_dividing_by_zero():
    """A cohort where nothing was recovered is a real possibility at 30 days."""
    none = [obs(predicted=0.4, realised=0.0, outstanding=1e6) for _ in range(10)]
    cap = top_k_capture(none)
    assert cap["captured"] is None and "note" in cap


# ── Marginal value over collateral ──────────────────────────────────────────
def test_marginal_lift_splits_by_collateral_class():
    """The question SECURITY's dominance forces: does the label still order loans
    inside a collateral class, or is it a proxy for loan_type?"""
    mixed = ([obs(band="HIGH", realised=0.6, secured="SECURED")] * 4
             + [obs(band="LOW", realised=0.2, secured="SECURED")] * 4
             + [obs(band="HIGH", realised=0.3, secured="UNSECURED")] * 4
             + [obs(band="LOW", realised=0.3, secured="UNSECURED")] * 4)
    out = marginal_lift_by_security(mixed)
    assert out["SECURED"]["high_low_lift"] == pytest.approx(3.0)
    # No separation inside unsecured — the label adds nothing there.
    assert out["UNSECURED"]["high_low_lift"] == pytest.approx(1.0)


# ── Factor diagnostics ──────────────────────────────────────────────────────
def test_a_factor_tracking_the_error_is_surfaced_first():
    """A factor whose contribution correlates with the residual is mis-weighted:
    when it speaks, the prediction is wrong in a consistent direction."""
    rows = []
    for i in range(20):
        pts = i / 100.0
        rows.append(obs(predicted=0.4, realised=0.4 + pts, SECURITY=pts, AGEING=-0.05))
    out = factor_residual_correlation(rows)
    assert list(out)[0] == "SECURITY"
    assert out["SECURITY"]["correlation"] > 0.9
    assert out["AGEING"]["correlation"] is None      # no variance


def test_too_few_observations_report_rather_than_correlate():
    out = factor_residual_correlation([obs(SECURITY=0.16)] * 5)
    assert out["SECURITY"]["correlation"] is None
    assert "too few" in out["SECURITY"]["note"]


# ── The diagnosis, which names a lever but never pulls it ────────────────────
def test_good_ranking_with_uniform_bias_points_at_base_rate():
    ranking = {"monotonic": True, "spearman": 0.55}
    calibration = {"bias": -0.20, "bias_spread": 0.01}
    d = diagnose(ranking, calibration)
    assert d["verdict"] == "LEVEL_WRONG_ORDER_RIGHT"
    assert d["candidate_lever"] == "BASE_RATE"


def test_good_ranking_with_band_dependent_bias_points_at_spread():
    d = diagnose({"monotonic": True, "spearman": 0.55},
                 {"bias": -0.20, "bias_spread": 0.30})
    assert d["verdict"] == "SPREAD_WRONG_ORDER_RIGHT"
    assert d["candidate_lever"] == "WEIGHT_SCALE_OR_BAND_EDGES"


def test_poor_ranking_points_at_the_weights():
    d = diagnose({"monotonic": False, "spearman": 0.05},
                 {"bias": 0.0, "bias_spread": 0.0})
    assert d["verdict"] == "ORDER_WRONG"
    assert d["candidate_lever"] == "FACTOR_WEIGHTS"


def test_both_holding_recommends_nothing():
    d = diagnose({"monotonic": True, "spearman": 0.60},
                 {"bias": 0.01, "bias_spread": 0.01})
    assert d["verdict"] == "BOTH_HOLD"
    assert d["candidate_lever"] is None


def test_missing_data_is_not_a_verdict():
    d = diagnose({"monotonic": None, "spearman": None},
                 {"bias": None, "bias_spread": None})
    assert d["verdict"] == "INSUFFICIENT_DATA"


def test_the_diagnosis_never_authorises_a_change():
    """Every verdict, including the ones that name a lever, must say out loud
    that it changes nothing. The scorecard is versioned and its weights are a
    separate approved decision."""
    for ranking, calibration in (
        ({"monotonic": True, "spearman": 0.6}, {"bias": 0.0, "bias_spread": 0.0}),
        ({"monotonic": True, "spearman": 0.6}, {"bias": -0.3, "bias_spread": 0.01}),
        ({"monotonic": False, "spearman": 0.0}, {"bias": -0.3, "bias_spread": 0.4}),
    ):
        assert "REPORT ONLY" in diagnose(ranking, calibration)["action"]
