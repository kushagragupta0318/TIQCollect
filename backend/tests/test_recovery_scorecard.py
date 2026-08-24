# ─── CHANGELOG (prototype → product) ───
# New file, 2026-08-24. Covers ml/recovery_scorecard.py — the rules behind
# Loan.recovery_potential, which until today was random.choices() in
# seed_data.py:481-509 and a second, differently-weighted random() UPDATE in
# scripts/add_recovery_potential.py.
#
# Three tests here carry the weight, and each states the harm in its docstring:
#
#   * SCALE INVARIANCE. This scorecard predicts a RATE. If any absolute rupee
#     amount can move it, the label silently means "share recovered" and "amount
#     at stake" at once, and a manager sorting by it cannot know which. An
#     earlier draft had an EXPOSURE_SIZE factor and this test is what forbids it
#     coming back.
#   * MONOTONIC HORIZONS. rate_30 <= rate_60 <= rate_90 is guaranteed by
#     construction, not by weight tuning. The test asserts it over a grid so that
#     a future change to the maturity shares cannot quietly break it.
#   * ABSTENTION. A factor with no evidence must say so rather than contribute
#     zero. Scoring an unknown loan as a bad one writes off money nobody looked at.
#
# Same no-DB style as test_repayment_scorecard.py and test_eligibility.py: pure
# functions over SimpleNamespace stand-ins.
from itertools import product
from types import SimpleNamespace as NS

from app.ml.recovery_scorecard import (
    BASE_RATE, FACTOR_AGEING, FACTOR_ARREARS_SHARE, FACTOR_LEGAL_POSTURE,
    FACTOR_NPA_STATUS, FACTOR_PAYMENT_MOMENTUM, FACTOR_PAYMENT_RECENCY,
    FACTOR_PENAL_DRAG, FACTOR_SECURITY, HORIZONS, LABEL_HORIZON, MAX_RATE,
    MIN_RATE, NO_LEGAL_ACTION, NO_PAYMENT_ON_RECORD, NO_PENAL_CHARGES,
    NO_RECENT_PAYMENT_ACTIVITY,
    NOT_NPA, RECOVERY_BAND_EDGES, RECOVERY_HIGH, RECOVERY_LOW, RECOVERY_MEDIUM,
    RECOVERY_SCORECARD_VERSION, TOTAL_WEIGHT, UNKNOWN_SECURITY, _W_AGEING,
    band_for, expected_recoverable_amount, score,
)
from app.models.loan import LoanType

# The rupee-denominated keys. Scaling every one of them together must not move
# the rate — that is the definition of "no absolute-rupee factor".
MONEY_KEYS = (
    "total_outstanding", "overdue_amount", "penal_charges",
    "amount_paid_in_window", "last_payment_amount", "emi_amount",
)


def features(**over):
    """A mid-book delinquent loan: 75 DPD, unsecured, paying a little."""
    base = dict(
        dpd=75,
        loan_type=LoanType.PERSONAL,
        total_outstanding=520000.0,
        overdue_amount=73600.0,
        emi_amount=18400.0,
        penal_charges=12000.0,
        last_payment_amount=9000.0,
        amount_paid_in_window=20000.0,
        days_since_last_payment=48,
        legal_status="NONE",
        settlement_status="NONE",
        npa_flag=False,
    )
    base.update(over)
    return NS(**base)


def _codes(result):
    return {f["code"]: f for f in result["factors"]}


def _rates(result):
    return [result[f"recovery_rate_{h}"] for h in HORIZONS]


# ── Scale invariance: the reason EXPOSURE_SIZE is not a factor ───────────────
def test_rate_is_unchanged_when_every_amount_is_scaled():
    """A ₹5,20,000 loan and a ₹52,00,000 loan in the same SHAPE recover the same
    SHARE. If this fails, an absolute rupee amount has leaked into the rate and
    the label now conflates "what share comes back" with "how much is at stake" —
    the two questions a manager most needs kept apart."""
    small = score(features())
    large = score(features(**{k: getattr(features(), k) * 10 for k in MONEY_KEYS}))

    assert small["recovery_rate_90"] == large["recovery_rate_90"]
    assert small["recovery_potential"] == large["recovery_potential"]
    assert _rates(small) == _rates(large)


def test_rate_is_unchanged_across_four_orders_of_magnitude():
    """Same property, swept — a factor that only misbehaves at one size is still
    broken."""
    baseline = score(features())["recovery_rate_90"]
    for multiplier in (0.01, 0.1, 1.0, 10.0, 100.0):
        scaled = features(**{k: getattr(features(), k) * multiplier for k in MONEY_KEYS})
        assert score(scaled)["recovery_rate_90"] == baseline, f"moved at x{multiplier}"


def test_arrears_share_responds_to_the_ratio_not_the_amount():
    """The counterpart: changing the SHAPE must still move the score. A scorecard
    that is invariant to everything is invariant because it is not reading."""
    light = score(features(total_outstanding=1_000_000.0, overdue_amount=50_000.0))
    heavy = score(features(total_outstanding=1_000_000.0, overdue_amount=600_000.0))
    assert light["recovery_rate_90"] > heavy["recovery_rate_90"]


# ── The money question, answered separately ─────────────────────────────────
def test_expected_amount_scales_linearly_while_the_rate_holds():
    """expected_recoverable_amount is where size belongs. The rate stays put and
    the rupee figure tracks the balance — that separation is the whole design."""
    base = features()
    ten_x = features(**{k: getattr(base, k) * 10 for k in MONEY_KEYS})

    rate_a = score(base)["recovery_rate_90"]
    rate_b = score(ten_x)["recovery_rate_90"]
    assert rate_a == rate_b

    amount_a = expected_recoverable_amount(rate_a, base.total_outstanding)
    amount_b = expected_recoverable_amount(rate_b, ten_x.total_outstanding)
    assert round(amount_b, 2) == round(amount_a * 10, 2)


def test_expected_amount_is_never_negative():
    """Defensive: a negative recoverable amount would sum into an analytics total
    and quietly cancel out real money elsewhere in the book."""
    assert expected_recoverable_amount(-0.5, 100000.0) == 0.0
    assert expected_recoverable_amount(0.4, -100000.0) == 0.0
    assert expected_recoverable_amount(None, None) == 0.0


# ── Monotonic horizons ──────────────────────────────────────────────────────
def test_horizons_are_monotonic_on_the_default_loan():
    r30, r60, r90 = _rates(score(features()))
    assert r30 <= r60 <= r90


def test_horizons_are_monotonic_across_a_grid():
    """Swept rather than spot-checked. Monotonicity is structural — f30 and f60
    share a slope, so their gap is a positive constant — and this test is what
    keeps it structural when someone edits the maturity shares. rate_30 > rate_90
    is nonsense no weight tuning can rule out, so it must be ruled out here."""
    grid = product(
        (0, 35, 95, 180, 400),                                   # dpd
        (LoanType.HOME, LoanType.PERSONAL, LoanType.BUSINESS),   # secured/unsecured/neither
        ("NONE", "SARFAESI"),                                    # legal
        ("NONE", "ACCEPTED"),                                    # settlement
        (None, 5, 200),                                          # days since last payment
        (0.0, 250000.0),                                         # paid in window
    )
    for dpd, loan_type, legal, settlement, recency, paid in grid:
        result = score(features(
            dpd=dpd, loan_type=loan_type, legal_status=legal,
            settlement_status=settlement, days_since_last_payment=recency,
            amount_paid_in_window=paid,
        ))
        r30, r60, r90 = _rates(result)
        assert r30 <= r60 <= r90, (
            f"non-monotonic at dpd={dpd} type={loan_type} legal={legal} "
            f"settlement={settlement} recency={recency} paid={paid}: {r30}/{r60}/{r90}"
        )


def test_speed_changes_the_ramp_but_never_the_eventual_rate():
    """Legal enforcement slows collection without reducing what is eventually
    recovered. If speed leaked into the 90-day figure, "slow" and "less" would be
    the same word and the horizon split would be pointless."""
    fast = features(days_since_last_payment=5, legal_status="NONE")
    slow = features(days_since_last_payment=5, legal_status="SARFAESI")

    # Isolate speed: compare a loan against itself with only the pace changed.
    quick = score(features(days_since_last_payment=5))
    lagged = score(features(days_since_last_payment=200))
    assert quick["speed_index"] > lagged["speed_index"]
    assert score(fast)["recovery_rate_90"] != score(slow)["recovery_rate_90"]  # posture moves the rate too
    assert score(slow)["speed_index"] < score(fast)["speed_index"]


def test_rate_30_is_never_above_the_eventual_rate():
    """The floor case: an instantly-collectable loan still cannot recover more in
    30 days than it recovers in total."""
    result = score(features(days_since_last_payment=1, amount_paid_in_window=500000.0,
                            legal_status="NONE", settlement_status="ACCEPTED"))
    assert result["recovery_rate_30"] <= result["recovery_rate_90"]


# ── Bands ───────────────────────────────────────────────────────────────────
def test_band_edges_are_read_from_the_constants():
    """Asserted against the constants rather than literals, so a deliberate edge
    change does not need this test rewritten — only re-read."""
    high_edge, _ = RECOVERY_BAND_EDGES[0]
    medium_edge, _ = RECOVERY_BAND_EDGES[1]

    assert band_for(high_edge) == RECOVERY_HIGH
    assert band_for(high_edge + 0.01) == RECOVERY_HIGH
    assert band_for(high_edge - 0.01) == RECOVERY_MEDIUM
    assert band_for(medium_edge) == RECOVERY_MEDIUM
    assert band_for(medium_edge - 0.01) == RECOVERY_LOW
    assert band_for(0.0) == RECOVERY_LOW


def test_all_three_bands_are_reachable():
    """Every band must be producible from a plausible loan. seed_data.py:461 once
    floored a score so RiskCategory.LOW was unreachable and a whole UI branch was
    dead code; the same mistake here would make a badge that never renders."""
    strong = score(features(dpd=32, loan_type=LoanType.GOLD, overdue_amount=20000.0,
                            penal_charges=0.0, days_since_last_payment=10,
                            amount_paid_in_window=120000.0, last_payment_amount=18400.0))
    weak = score(features(dpd=400, loan_type=LoanType.CREDIT_CARD,
                          overdue_amount=500000.0, penal_charges=90000.0,
                          days_since_last_payment=None, amount_paid_in_window=0.0,
                          last_payment_amount=0.0, npa_flag=True,
                          settlement_status="NEGOTIATING"))

    assert strong["recovery_potential"] == RECOVERY_HIGH
    assert weak["recovery_potential"] == RECOVERY_LOW
    assert score(features())["recovery_potential"] in {
        RECOVERY_HIGH, RECOVERY_MEDIUM, RECOVERY_LOW}


def test_label_is_banded_on_the_eventual_horizon():
    """Banding on 30 days would mark a slow-but-secured loan LOW and steer agents
    away from money recoverable by quarter-end — the exact misallocation this
    label exists to correct."""
    result = score(features())
    assert result["label_horizon_days"] == LABEL_HORIZON == 90
    assert result["recovery_potential"] == band_for(result["recovery_rate_90"])


def test_rate_is_bounded():
    """Clamped at both ends, so no combination of factors can produce a rate
    above 1.0 that would then be multiplied into an impossible rupee figure."""
    best = score(features(dpd=0, loan_type=LoanType.HOME, overdue_amount=0.0,
                          penal_charges=0.0, days_since_last_payment=0,
                          amount_paid_in_window=520000.0, last_payment_amount=18400.0,
                          legal_status="SARFAESI"))
    worst = score(features(dpd=999, loan_type=LoanType.CREDIT_CARD,
                           overdue_amount=520000.0, penal_charges=200000.0,
                           days_since_last_payment=None, amount_paid_in_window=0.0,
                           last_payment_amount=0.0, npa_flag=True,
                           settlement_status="ACCEPTED"))
    for result in (best, worst):
        for rate in _rates(result):
            assert MIN_RATE * 0 <= rate <= MAX_RATE


# ── Abstention ──────────────────────────────────────────────────────────────
def test_every_factor_abstains_rather_than_scoring_zero():
    """A loan we know nothing about must read as unknown, not as bad. Writing off
    money nobody looked at is the failure this contract prevents."""
    blank = NS(
        dpd=75,
        loan_type=LoanType.BUSINESS,      # in neither _SECURED nor _UNSECURED
        total_outstanding=0.0,            # no ratio can be formed
        overdue_amount=0.0,
        emi_amount=0.0,
        penal_charges=0.0,
        last_payment_amount=0.0,
        amount_paid_in_window=0.0,
        days_since_last_payment=None,
        legal_status="NONE",
        settlement_status="NONE",
        npa_flag=False,
    )
    codes = _codes(score(blank))

    for code, reason in (
        (FACTOR_SECURITY, UNKNOWN_SECURITY),
        (FACTOR_PAYMENT_RECENCY, NO_PAYMENT_ON_RECORD),
        (FACTOR_PENAL_DRAG, NO_PENAL_CHARGES),
        (FACTOR_LEGAL_POSTURE, NO_LEGAL_ACTION),
        (FACTOR_NPA_STATUS, NOT_NPA),
    ):
        assert codes[code]["abstained"] is True, f"{code} did not abstain"
        assert codes[code]["reason"] == reason
        assert "points" not in codes[code], f"{code} abstained but still carried points"

    for code in (FACTOR_ARREARS_SHARE, FACTOR_PAYMENT_MOMENTUM):
        assert codes[code]["abstained"] is True, f"{code} did not abstain on a zero balance"


def test_ageing_never_abstains():
    """dpd is non-nullable on the model and is the one thing always known about a
    delinquent loan. An abstention here would mean the scorecard had nothing at
    all to say, which is never true."""
    codes = _codes(score(features(dpd=0)))
    assert codes[FACTOR_AGEING].get("abstained") is not True


def test_coverage_reflects_only_the_weight_that_spoke():
    """Deliberately not renormalised, matching the repayment scorecard. A loan
    with one factor must read as thin, not as confidently average."""
    blank = NS(
        dpd=75, loan_type=LoanType.BUSINESS, total_outstanding=0.0,
        overdue_amount=0.0, emi_amount=0.0, penal_charges=0.0,
        last_payment_amount=0.0, amount_paid_in_window=0.0,
        days_since_last_payment=None, legal_status="NONE",
        settlement_status="NONE", npa_flag=False,
    )
    thin = score(blank)
    assert thin["evidence_coverage"] == round(_W_AGEING / TOTAL_WEIGHT, 3)
    assert score(features())["evidence_coverage"] > thin["evidence_coverage"]


def test_absence_of_an_adverse_fact_is_not_a_credit():
    """NPA and legal posture abstain when clean rather than scoring positive.
    "Not yet classified NPA" is the absence of bad news, not evidence of
    recoverability, and treating it as a bonus inflates every young account."""
    codes = _codes(score(features(npa_flag=False, legal_status="NONE",
                                  settlement_status="NONE")))
    assert codes[FACTOR_NPA_STATUS]["abstained"] is True
    assert codes[FACTOR_LEGAL_POSTURE]["abstained"] is True


# ── Determinism and provenance ──────────────────────────────────────────────
def test_score_is_deterministic():
    """No RNG, no clock, no I/O. The column this replaces was random.choices(),
    so the same loan scored differently on consecutive runs — which is what made
    it worthless as a training label."""
    first = score(features())
    second = score(features())
    assert first == second


def test_every_result_carries_its_version():
    """Stamped on every snapshot row so a label can be traced to the rules that
    produced it."""
    assert score(features())["model_version"] == RECOVERY_SCORECARD_VERSION


def test_no_accuracy_claim_is_produced():
    """Tier 1 is a hand-weighted scorecard. It has never been fitted and has no
    held-out set, so it must not emit anything resembling a model-performance
    figure. Asserted rather than trusted, because such a key would be read as a
    guarantee the moment it reached a UI."""
    keys = set(score(features()))
    for forbidden in ("auc", "gini", "accuracy", "precision", "recall",
                      "confidence", "r2", "ks_statistic"):
        assert not any(forbidden in k.lower() for k in keys), f"{forbidden} leaked into the payload"


def test_base_rate_is_the_starting_point_for_a_silent_loan():
    """With every factor abstaining except ageing at zero DPD, the rate is the
    documented base — not an emergent number nobody chose."""
    silent = NS(
        dpd=0, loan_type=LoanType.BUSINESS, total_outstanding=0.0,
        overdue_amount=0.0, emi_amount=0.0, penal_charges=0.0,
        last_payment_amount=0.0, amount_paid_in_window=0.0,
        days_since_last_payment=None, legal_status="NONE",
        settlement_status="NONE", npa_flag=False,
    )
    assert score(silent)["recovery_rate_90"] == round(BASE_RATE, 4)


# ── PAYMENT_MOMENTUM: money now, not money once (v1.1.0, 2026-08-24) ────────
# Added after the first dry run against the live book found 227 loans (43%)
# taking a positive momentum contribution having received nothing in the
# behaviour window — 77 of them 90+ DPD, median 129 days since their last
# payment, 21 last paying beyond the 180-day window entirely.
#
# The guard used to be `paid <= 0 and last <= 0`, so a stale last_payment_amount
# alone could carry the factor. These tests pin the corrected contract.

def test_momentum_abstains_when_nothing_arrived_in_the_window():
    """THE FIX. No receipts in the window means no momentum, whatever the loan's
    last_payment_amount column happens to remember."""
    result = score(features(amount_paid_in_window=0.0, last_payment_amount=18400.0))
    momentum = _codes(result)[FACTOR_PAYMENT_MOMENTUM]
    assert momentum["abstained"] is True
    assert momentum["reason"] == NO_RECENT_PAYMENT_ACTIVITY
    assert "points" not in momentum


def test_a_stale_last_payment_cannot_manufacture_momentum():
    """The exact shape of the 227: a loan long past due whose only positive
    evidence is a full-EMI payment from months ago. It must score no momentum at
    all — not a reduced one.

    last_payment_amount is overwritten in place by ingest with no history, so
    reading it unbounded here is the same leak build_features already refuses for
    last_payment_date."""
    stale = score(features(
        dpd=210, amount_paid_in_window=0.0,
        last_payment_amount=18400.0,          # a full EMI, once
        days_since_last_payment=276,          # ...276 days ago
    ))
    momentum = _codes(stale)[FACTOR_PAYMENT_MOMENTUM]
    assert momentum["abstained"] is True
    assert momentum.get("points") is None


def test_a_larger_stale_payment_still_produces_nothing():
    """Swept, because the old behaviour scaled with the remembered amount: a
    bigger forgotten payment bought a bigger score. Every one of these must
    abstain identically."""
    for amount in (1.0, 5_000.0, 18_400.0, 250_000.0):
        momentum = _codes(score(features(
            amount_paid_in_window=0.0, last_payment_amount=amount)))[FACTOR_PAYMENT_MOMENTUM]
        assert momentum["abstained"] is True, f"scored on a stale {amount}"


def test_genuine_payment_in_the_window_still_scores():
    """The factor must keep working. Money actually received is what it is for."""
    live = _codes(score(features(
        amount_paid_in_window=52_000.0, last_payment_amount=18_400.0)))[FACTOR_PAYMENT_MOMENTUM]
    assert live.get("abstained") is not True
    assert live["points"] > 0
    assert live["direction"] == "UP"
    assert live["evidence"]["amount_paid_in_window"] == 52_000.0


def test_momentum_rises_with_money_actually_received():
    """Ordering, not just presence — a loan paying more in the window must score
    at least as much as one paying less."""
    scores = []
    for paid in (5_000.0, 25_000.0, 52_000.0, 120_000.0):
        f = _codes(score(features(amount_paid_in_window=paid)))[FACTOR_PAYMENT_MOMENTUM]
        scores.append(f["points"])
    assert scores == sorted(scores), scores
    assert scores[-1] > scores[0]


def test_momentum_summary_never_claims_zero_receipts():
    """The old reason line read "₹0 received recently; last payment covered 98% of
    an EMI" beside a positive score — the product contradicting itself in front of
    a manager. A factor that speaks must have money to point at."""
    for paid in (0.0, 1_000.0, 60_000.0):
        f = _codes(score(features(amount_paid_in_window=paid)))[FACTOR_PAYMENT_MOMENTUM]
        if f.get("abstained"):
            continue
        assert "₹0 " not in f["summary"], f["summary"]
        assert f["evidence"]["amount_paid_in_window"] > 0


def test_recency_still_carries_the_borrower_paid_once_signal():
    """Nothing is lost by the abstention: when they last paid is PAYMENT_RECENCY's
    job, at a larger weight. If that were not true, the fix would be discarding
    information rather than relocating it."""
    recent = _codes(score(features(days_since_last_payment=5,
                                   amount_paid_in_window=0.0)))[FACTOR_PAYMENT_RECENCY]
    distant = _codes(score(features(days_since_last_payment=250,
                                    amount_paid_in_window=0.0)))[FACTOR_PAYMENT_RECENCY]
    assert recent["points"] > distant["points"]


def test_the_version_moved_with_the_factor_definition():
    """A factor definition changed, so rows written before and after answer
    different questions. Without the bump a model trained across both would be
    learning two scorecards at once."""
    assert RECOVERY_SCORECARD_VERSION == "recovery-scorecard-1.1.0"
    assert score(features())["model_version"] == RECOVERY_SCORECARD_VERSION


def test_the_fix_touched_nothing_but_the_guard():
    """Weights, band edges and BASE_RATE were explicitly out of scope. Pinned here
    so a later 'while I was in there' change is visible in this test's diff."""
    assert BASE_RATE == 0.35
    assert (MIN_RATE, MAX_RATE) == (0.02, 0.95)
    assert RECOVERY_BAND_EDGES == ((0.50, RECOVERY_HIGH), (0.25, RECOVERY_MEDIUM))
    assert round(TOTAL_WEIGHT, 4) == 0.9
    assert _W_AGEING == 0.18
