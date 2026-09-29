# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# New file, 2026-09-29 (P3 D06/R3). services/bank/expected_recovery.py.
from __future__ import annotations

from app.models.loan import DPDBucket, LoanType
from app.services.bank.expected_recovery import VERSION, expected_recovery_inr

_ARGS = dict(overdue_at_placement=10_000.0, exposure_at_placement=100_000.0,
            dpd_bucket=DPDBucket.BUCKET_2, loan_type=LoanType.PERSONAL)


def test_no_modelled_score_abstains_rather_than_guessing():
    """ADR 0005: no prediction yet must never read as a confident zero."""
    assert expected_recovery_inr(prob=None, **_ARGS) is None


def test_multiplies_probability_by_the_overdue_amount_not_full_exposure():
    result = expected_recovery_inr(prob=0.4, **_ARGS)
    assert result == 4_000.0   # 0.4 * 10,000 overdue, nowhere near the 100,000 exposure cap


def test_capped_at_exposure_even_if_the_arithmetic_would_exceed_it():
    """A probability times an arrears figure can never exceed what the loan
    is actually worth, however the two upstream inputs were computed."""
    result = expected_recovery_inr(prob=0.9, overdue_at_placement=200_000.0,
                                   exposure_at_placement=50_000.0,
                                   dpd_bucket=DPDBucket.NPA, loan_type=LoanType.HOME)
    assert result == 50_000.0


def test_zero_probability_is_zero_not_none():
    assert expected_recovery_inr(prob=0.0, **_ARGS) == 0.0


def test_full_probability_returns_the_full_overdue_amount():
    assert expected_recovery_inr(prob=1.0, **_ARGS) == 10_000.0


def test_negative_inputs_are_clamped_not_propagated():
    """A data-quality bug upstream (a negative overdue/exposure) must not
    produce a negative expected recovery."""
    result = expected_recovery_inr(prob=0.5, overdue_at_placement=-500.0,
                                   exposure_at_placement=-100.0,
                                   dpd_bucket=DPDBucket.CURRENT, loan_type=LoanType.AUTO)
    assert result == 0.0


def test_rounds_to_two_decimal_places_for_the_money_column():
    result = expected_recovery_inr(prob=1 / 3, overdue_at_placement=100.0, exposure_at_placement=1_000.0,
                                   dpd_bucket=DPDBucket.BUCKET_1, loan_type=LoanType.GOLD)
    assert result == 33.33


def test_version_is_stamped_for_downstream_score_breakdown():
    assert VERSION == "expected-recovery-1.0.0"


def test_dpd_bucket_and_loan_type_are_accepted_but_do_not_change_v1_0_0s_result():
    """Part of 2b's agreed contract even though v1.0.0 does not read them —
    proves the signature accepts every DPDBucket/LoanType combination without
    the result silently varying by one, which would mean an undocumented
    haircut had crept in."""
    baseline = expected_recovery_inr(prob=0.6, overdue_at_placement=5_000.0, exposure_at_placement=20_000.0,
                                     dpd_bucket=DPDBucket.CURRENT, loan_type=LoanType.HOME)
    for bucket in DPDBucket:
        for loan_type in LoanType:
            assert expected_recovery_inr(prob=0.6, overdue_at_placement=5_000.0, exposure_at_placement=20_000.0,
                                        dpd_bucket=bucket, loan_type=loan_type) == baseline
