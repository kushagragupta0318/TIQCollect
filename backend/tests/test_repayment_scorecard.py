# ─── CHANGELOG (prototype → product) ───
# New file, 2026-08-21. Covers ml/repayment_scorecard.py — the rules behind
# Customer.risk_score, which until today was dpd/90*60 + (750-cibil)/750*40
# plus random.uniform(-8, 8) of jitter.
#
# The abstention and non-gating tests carry the weight. A scorecard that scores
# an unknown borrower as a bad one turns every new case into a defaulter, and a
# scorecard that can suppress a visit becomes self-fulfilling. Both are silent
# failures — the number still looks reasonable — so each has a test whose
# docstring states the harm.
#
# Same no-DB style as test_eligibility.py: pure functions over SimpleNamespace
# stand-ins, so nothing here needs a database.
from types import SimpleNamespace as NS

from app.core.config import settings
from app.ml.eligibility import _SECURED, _UNSECURED
from app.ml.repayment_scorecard import (
    _DPD_ENTRY, _DPD_FLOOR,
    BAND_LIKELY, FACTOR_CONDUCT, FACTOR_LEGAL_POSTURE, FACTOR_PROMISE_HISTORY,
    FACTOR_DELINQUENCY_DEPTH, FACTOR_SECURITY, LIKELIHOOD_BAND_EDGES, MAX_LIKELIHOOD, MIN_LIKELIHOOD,
    NO_PTP_HISTORY, RISK_BAND_EDGES, TOTAL_WEIGHT, band_for, risk_category_for,
    risk_score_from, score,
)
from app.models.loan import LoanType

# The 15 DPD values seed_data.py:92 can actually produce. Testing the range
# 0..400 would assert behaviour on values that never occur.
SEEDED_DPD = [35, 42, 50, 58, 65, 72, 80, 88, 95, 105, 120, 150, 180, 210, 270]

# Band names paired with the RiskCategory they must mirror.
_MIRROR = {"LIKELY": "LOW", "UNCERTAIN": "MEDIUM",
           "UNLIKELY": "HIGH", "VERY_UNLIKELY": "CRITICAL"}


def features(**over):
    """A mid-book borrower: 75 DPD, some history, nothing remarkable."""
    base = dict(
        dpd=75, cibil_score=610, customer_segment="SALARIED",
        loan_type=LoanType.PERSONAL, emi_amount=18400.0, overdue_amount=73600.0,
        outstanding_principal=520000.0, last_payment_amount=9000.0,
        legal_status="NONE", settlement_status="NONE",
        is_hostile=False, fraud_flag=False, adverse_visit_outcomes=0,
        visits=4, visits_met=3, ptps_resolved=3, ptps_honored=2,
        case_target_amount=48000.0, amount_paid_in_window=20000.0,
    )
    base.update(over)
    return NS(**base)


def _codes(result):
    return {f["code"]: f for f in result["factors"]}


# ── Direction and bands ──────────────────────────────────────────────────────
def test_band_mirrors_risk_category_at_every_integer():
    """risk_score = 100 - likelihood, and the two band sets must agree at every
    point. If anyone nudges one edge without the other, a borrower could be
    LIKELY to repay and CRITICAL risk simultaneously."""
    for likelihood in range(0, 101):
        assert _MIRROR[band_for(likelihood)] == risk_category_for(
            risk_score_from(likelihood)
        ), f"disagreement at likelihood={likelihood}"


def test_band_edges_are_the_exact_mirror_of_the_risk_edges():
    """Read from the constants, not from literals, so the mirror survives a
    deliberate edge change."""
    risk_edges = [edge for edge, _ in RISK_BAND_EDGES]
    likelihood_edges = [edge for edge, _ in LIKELIHOOD_BAND_EDGES]
    assert sorted(likelihood_edges) == sorted(100.0 - e for e in risk_edges)


def test_low_risk_is_reachable():
    """Deliberate product decision, 2026-08-21. seed_data.py:461 floored the
    score at 30 with the comment "minimum MEDIUM since all are NPA", so
    RiskCategory.LOW was unreachable and RiskBadge's LOW branch was dead code.
    A 35-DPD borrower who keeps promises and pays genuinely IS low risk;
    refusing to say so is what made the old number useless."""
    good = score(features(dpd=35, cibil_score=800, ptps_resolved=4,
                          ptps_honored=4, visits=4, visits_met=4,
                          amount_paid_in_window=48000.0,
                          loan_type=LoanType.HOME))
    assert good["band"] == BAND_LIKELY
    assert good["risk_category"] == "LOW"


# ── Delinquency ──────────────────────────────────────────────────────────────
def test_likelihood_is_non_increasing_in_dpd():
    values = [score(features(dpd=d))["likelihood"] for d in SEEDED_DPD]
    assert all(a >= b for a, b in zip(values, values[1:])), values


def test_delinquency_is_penalty_only():
    """v1.1.0. On a collections book every account is delinquent by definition,
    so being 42 days late is not a virtue. v1.0.0 put the neutral point at 90
    DPD and handed +16 to borrowers merely six weeks overdue — which is how 55%
    of the LOW band came to rest on a positive delinquency term, and how 18
    customers reached LOW with no behavioural evidence at all."""
    for dpd in SEEDED_DPD:
        pts = _codes(score(features(dpd=dpd)))[FACTOR_DELINQUENCY_DEPTH]["points"]
        assert pts <= 0, f"dpd={dpd} scored {pts:+}, delinquency must never reward"


def test_delinquency_is_zero_at_the_entry_point_and_full_at_the_floor():
    """Read from the constants, so the scale survives a deliberate edge change."""
    assert _codes(score(features(dpd=int(_DPD_ENTRY))))[
        FACTOR_DELINQUENCY_DEPTH]["points"] == 0.0
    assert _codes(score(features(dpd=int(_DPD_FLOOR))))[
        FACTOR_DELINQUENCY_DEPTH]["points"] == -30.0
    assert _codes(score(features(dpd=400)))[
        FACTOR_DELINQUENCY_DEPTH]["points"] == -30.0     # clamped


def test_low_now_requires_behavioural_evidence():
    """The whole point of the 1.1.0 change. A borrower about whom nothing
    behavioural is known can no longer reach LOW on delinquency alone."""
    blind = score(NS(dpd=42, cibil_score=676, emi_amount=852.0,
                     overdue_amount=1704.0, loan_type=LoanType.AUTO,
                     last_payment_amount=675.0, customer_segment="SELF_EMPLOYED"))
    assert blind["risk_category"] != "LOW"
    assert blind["evidence_coverage"] < settings.REPAYMENT_MIN_COVERAGE_TO_SHOW


def test_coverage_floor_sits_above_the_non_behavioural_ceiling():
    """DERIVED, not chosen. The six always-available factors total 63 of 124 =
    0.508 on their own, so a 0.5 floor could never catch a borrower with zero
    behavioural evidence — 17 of 18 such customers sailed through it on
    2026-08-21. The floor must exceed that ceiling and must not exceed what one
    behavioural factor buys, or it would reject borrowers who DO have evidence."""
    from app.ml.repayment_scorecard import (
        _W_ARREARS, _W_BUREAU, _W_CONTACT, _W_DELINQUENCY, _W_LAST_PAYMENT,
        _W_SECURITY, _W_SEGMENT,
    )
    ceiling = (_W_DELINQUENCY + _W_ARREARS + _W_BUREAU + _W_SECURITY
               + _W_LAST_PAYMENT + _W_SEGMENT) / TOTAL_WEIGHT
    with_one = (ceiling * TOTAL_WEIGHT + _W_CONTACT) / TOTAL_WEIGHT
    floor = settings.REPAYMENT_MIN_COVERAGE_TO_SHOW
    assert ceiling < floor <= with_one, (
        f"floor {floor} must sit in ({ceiling:.4f}, {with_one:.4f}]")


def test_dpd_alone_does_not_determine_the_score():
    """The defect being fixed: the old formula was DPD and CIBIL and nothing
    else, so two borrowers at the same DPD were identical however differently
    they had behaved."""
    payer = features(dpd=95, ptps_resolved=4, ptps_honored=4,
                     visits=4, visits_met=4, amount_paid_in_window=48000.0)
    avoider = features(dpd=95, ptps_resolved=4, ptps_honored=0,
                       visits=4, visits_met=0, amount_paid_in_window=0.0)
    assert score(payer)["likelihood"] > score(avoider)["likelihood"] + 20


# ── Abstention ───────────────────────────────────────────────────────────────
def test_abstains_without_enough_promise_history():
    """Zero kept promises out of zero promises is not a bad payer, it is an
    unknown one. Scoring it as bad makes every brand-new borrower look like a
    defaulter on the day their case is created."""
    thin = score(features(ptps_resolved=0, ptps_honored=0))
    factor = _codes(thin)[FACTOR_PROMISE_HISTORY]
    assert factor["abstained"] is True
    assert factor["reason"] == NO_PTP_HISTORY
    assert "points" not in factor


def test_abstention_threshold_comes_from_settings():
    below = settings.REPAYMENT_MIN_PTPS_FOR_HISTORY - 1
    at = settings.REPAYMENT_MIN_PTPS_FOR_HISTORY
    assert _codes(score(features(ptps_resolved=below, ptps_honored=below)))[
        FACTOR_PROMISE_HISTORY].get("abstained") is True
    assert _codes(score(features(ptps_resolved=at, ptps_honored=at)))[
        FACTOR_PROMISE_HISTORY].get("abstained") is None


def test_coverage_is_not_renormalised():
    """Renormalising over the factors that spoke would make a borrower with one
    factor indistinguishable from one with eleven — a confident-looking number
    resting on nothing. The caller is told the coverage and decides."""
    thin = score(NS(dpd=75))
    full = score(features())
    assert thin["evidence_coverage"] < full["evidence_coverage"]
    assert thin["evidence_coverage"] < 0.5 < full["evidence_coverage"]


def test_coverage_denominator_is_the_total_weight():
    full = score(features(adverse_visit_outcomes=1, legal_status="NOTICE_SENT"))
    assert full["evidence_coverage"] == 1.0
    assert TOTAL_WEIGHT == 124.0


# ── Determinism ──────────────────────────────────────────────────────────────
def test_score_is_deterministic():
    """seed_data.py:460 added random.uniform(-8, 8), so the same borrower scored
    differently on consecutive runs and no score could ever be reproduced from
    its inputs. Nothing here may draw."""
    f = features()
    assert len({score(f)["likelihood"] for _ in range(50)}) == 1


# ── The score gates nothing ──────────────────────────────────────────────────
def test_hostile_and_fraud_penalise_but_never_block():
    """ml/eligibility.py is the only gate. A score that can suppress a visit is
    self-fulfilling: never visited, never pays, score falls further, never
    visited. Confirmed as a product requirement on 2026-08-21."""
    clean, flagged = score(features()), score(
        features(is_hostile=True, fraud_flag=True, adverse_visit_outcomes=2))
    assert flagged["likelihood"] < clean["likelihood"]
    # No verdict of any kind may appear in the output.
    for key in ("blocked", "eligible", "block_reason", "do_not_visit", "suppress"):
        assert key not in flagged


def test_conduct_is_penalty_only():
    """The absence of hostility is not evidence of willingness, so there is no
    upside branch — a clean record abstains rather than earning points."""
    clean = _codes(score(features()))[FACTOR_CONDUCT]
    assert clean["abstained"] is True
    flagged = _codes(score(features(is_hostile=True)))[FACTOR_CONDUCT]
    assert flagged["points"] < 0


def test_worst_and_best_stay_inside_the_bounds():
    """0 and 100 are claims of certainty. This is a scorecard."""
    worst = score(features(
        dpd=400, cibil_score=300, ptps_resolved=5, ptps_honored=0,
        visits=5, visits_met=0, amount_paid_in_window=0.0, emi_amount=1000.0,
        overdue_amount=999999.0, is_hostile=True, fraud_flag=True,
        adverse_visit_outcomes=4, legal_status="SUIT_FILED",
        settlement_status="OFFERED", customer_segment="STUDENT",
        last_payment_amount=1.0, loan_type=LoanType.CREDIT_CARD))
    best = score(features(
        dpd=0, cibil_score=900, ptps_resolved=6, ptps_honored=6,
        visits=6, visits_met=6, amount_paid_in_window=48000.0,
        overdue_amount=0.0, loan_type=LoanType.HOME,
        last_payment_amount=18400.0))
    assert worst["likelihood"] == MIN_LIKELIHOOD
    assert best["likelihood"] <= MAX_LIKELIHOOD


# ── Excluded inputs ──────────────────────────────────────────────────────────
def test_complaints_raised_is_not_a_factor():
    """Customer.complaints_raised is 0 on every row in the database and no code
    path writes it. A zero-variance input dressed as a factor puts a fabricated
    reason in front of an agent — "complaints: 0 (+2 pts)" on every borrower —
    and one fabricated reason is enough to make them distrust the whole panel."""
    result = score(features(complaints_raised=7))
    assert not any("COMPLAINT" in f["code"] for f in result["factors"])
    assert result["likelihood"] == score(features(complaints_raised=0))["likelihood"]


def test_own_outputs_are_not_inputs():
    """Feeding risk_score or bank_risk_score back in would train a model to
    predict itself. In seeded data bank_risk_score is itself a function of dpd
    and cibil (seed_data.py:1398), so it also triple-counts delinquency."""
    polluted = score(features(risk_score=99.0, bank_risk_score=99.0,
                              collection_priority_score=99.0,
                              recovery_potential="LOW"))
    assert polluted["likelihood"] == score(features())["likelihood"]


# ── Double-count corrections ─────────────────────────────────────────────────
def test_legal_posture_is_half_weighted_past_the_npa_line():
    """seed_data.py:1385-1397 only ever sets legal_status for dpd >= 90, so at
    full weight past that line the same delinquency would be counted twice."""
    early = _codes(score(features(dpd=60, legal_status="SUIT_FILED")))[
        FACTOR_LEGAL_POSTURE]
    late = _codes(score(features(dpd=95, legal_status="SUIT_FILED")))[
        FACTOR_LEGAL_POSTURE]
    assert abs(late["points"]) < abs(early["points"])
    assert late["evidence"]["half_weighted_past_npa"] is True


def test_clean_legal_posture_abstains():
    assert _codes(score(features()))[FACTOR_LEGAL_POSTURE]["abstained"] is True


# ── Reuse ────────────────────────────────────────────────────────────────────
def test_security_uses_the_eligibility_sets():
    """Reuses _SECURED/_UNSECURED from ml/eligibility.py rather than
    re-deriving them. Copy-pasted definitions are how the two risk formulas
    drifted apart in the first place."""
    for loan_type in _SECURED:
        assert _codes(score(features(loan_type=loan_type)))[
            FACTOR_SECURITY]["points"] > 0
    for loan_type in _UNSECURED:
        assert _codes(score(features(loan_type=loan_type)))[
            FACTOR_SECURITY]["points"] < 0


def test_business_loans_abstain_on_security():
    """BUSINESS is in neither set in ml/eligibility.py:40-46 — secured or
    unsecured depending on the facility, so calling it either is a guess."""
    assert _codes(score(features(loan_type=LoanType.BUSINESS)))[
        FACTOR_SECURITY]["abstained"] is True


# ── Explanations ─────────────────────────────────────────────────────────────
def test_every_speaking_factor_explains_itself_with_numbers():
    """Under RBI recovery-agent norms a score that deprioritised someone has to
    be explicable. A bare point value is not an explanation."""
    for factor in score(features(adverse_visit_outcomes=1,
                                 legal_status="NOTICE_SENT"))["factors"]:
        if factor.get("abstained"):
            assert factor["reason"]
            continue
        assert factor["summary"]
        assert isinstance(factor["evidence"], dict) and factor["evidence"]
        assert factor["direction"] in ("UP", "DOWN")
        # A factor resting on a QUANTITY must put that quantity in the sentence.
        # Categorical ones (secured/unsecured, income segment) legitimately have
        # no number to quote, so they are exempt rather than forced to invent one.
        numeric = [v for v in factor["evidence"].values()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if numeric:
            assert any(ch.isdigit() for ch in factor["summary"]), factor


def test_accepts_a_mapping_as_well_as_an_object():
    """The analysis script passes dicts and the service passes a dataclass. A
    silent miss would make every factor abstain at once and flatten the whole
    book to 50 — the exact failure this scorecard exists to end."""
    f = features()
    assert score(vars(f))["likelihood"] == score(f)["likelihood"]
