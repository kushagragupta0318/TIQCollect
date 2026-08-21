# ─── CHANGELOG (prototype → product) ───
# New file, 2026-08-21. Covers services/repayment_service.py — feature
# assembly, the snapshot write policy and the customer rollup.
#
# The point-in-time tests are the reason this file exists. Rows written by this
# service are the training set for a future model, and a leaked future value
# does not fail anything: it makes the model look BETTER than it is in testing
# and worse in production, which is the hardest class of bug to find later.
# Loan.dpd and PTP.status are both overwritten in place with no history, so the
# leak is a live risk rather than a theoretical one.
#
# Same no-DB style as test_fraud_service.py: the service is constructed once at
# module scope with db=None and the pure methods are called directly.
import pathlib
import re
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from app.core.config import settings
from app.ml import repayment
from app.ml.repayment_scorecard import band_for, risk_category_for
from app.models.repayment_snapshot import SOURCE_SCORECARD
from app.models.loan import LoanType
from app.models.ptp import PTPStatus
from app.models.visit import VisitOutcome
from app.services.repayment_service import (
    _FORBIDDEN_FEATURE_KEYS, RepaymentService, ScoreOutcome,
)

SVC = RepaymentService(db=None)

AS_OF = date(2026, 8, 21)


def loan(**over):
    base = dict(
        id="loan-1", customer_id="cust-1", dpd=75, loan_type=LoanType.PERSONAL,
        emi_amount=18400.0, overdue_amount=73600.0,
        outstanding_principal=520000.0, last_payment_amount=9000.0,
        legal_status="NONE", settlement_status="NONE",
    )
    base.update(over)
    return NS(**base)


def customer(**over):
    base = dict(
        id="cust-1", cibil_score=610, customer_segment="SALARIED",
        is_hostile=False, fraud_flag=False,
    )
    base.update(over)
    return NS(**base)


def visit(*, days_ago=5, met=True, outcome=VisitOutcome.PTP):
    when = datetime.combine(AS_OF - timedelta(days=days_ago),
                            datetime.min.time(), tzinfo=timezone.utc)
    return NS(check_in_time=when, customer_met=met, outcome=outcome)


def ptp(*, days_ago=10, status=PTPStatus.HONORED, updated_days_ago=10):
    return NS(
        committed_date=AS_OF - timedelta(days=days_ago),
        status=status,
        updated_at=datetime.combine(AS_OF - timedelta(days=updated_days_ago),
                                    datetime.min.time(), tzinfo=timezone.utc),
    )


def payment(*, days_ago=5, amount=10000.0, status="PENDING_VERIFICATION"):
    return NS(
        payment_date=datetime.combine(AS_OF - timedelta(days=days_ago),
                                      datetime.min.time(), tzinfo=timezone.utc),
        amount=amount, status=status,
    )


def case(**over):
    base = dict(id="case-1", target_amount=48000.0, collected_amount=0.0)
    base.update(over)
    return NS(**base)


def build(**kw):
    return SVC.build_features(loan(), customer(), AS_OF, **kw)


# ── Point-in-time correctness ────────────────────────────────────────────────
def test_visits_after_as_of_are_excluded():
    """A visit that happens tomorrow cannot have informed a score computed
    today. Including it inflates measured accuracy and destroys real accuracy."""
    f = build(visits=[visit(days_ago=5), visit(days_ago=-3)])
    assert f["visits"] == 1


def test_visit_on_the_as_of_day_itself_is_included():
    """The boundary is inclusive of as_of and exclusive of the day after —
    an off-by-one here silently drops a day of evidence from every row."""
    assert build(visits=[visit(days_ago=0)])["visits"] == 1


def test_evidence_older_than_the_window_is_excluded():
    old = settings.REPAYMENT_BEHAVIOUR_WINDOW_DAYS + 1
    assert build(visits=[visit(days_ago=old)])["visits"] == 0
    assert build(ptps=[ptp(days_ago=old)])["ptps_resolved"] == 0


def test_payments_are_filtered_on_date_not_status():
    """Payment.status is current-state. A payment recorded today and verified
    next week must count as it was KNOWN today — filtering on VERIFIED would
    let next week's verification leak backwards into today's score."""
    f = build(payments=[payment(status="PENDING_VERIFICATION", amount=5000.0),
                        payment(status="VERIFIED", amount=5000.0)],
              cases=[case()])
    assert f["amount_paid_in_window"] == 10000.0
    assert f["_payment_status_at_scoring"] == "ANY"


def test_backfill_drops_promises_resolved_after_the_as_of_date():
    """PTP.status is mutated in place with no history, so a promise made before
    as_of but broken after it reads as BROKEN when rebuilt later. Forward runs
    cannot leak; backfills can, so they drop those rows and record how many."""
    leaky = ptp(days_ago=3, status=PTPStatus.BROKEN, updated_days_ago=-4)
    forward = SVC.build_features(loan(), customer(), AS_OF, ptps=[leaky])
    back = SVC.build_features(loan(), customer(), AS_OF, ptps=[leaky],
                              is_backfill=True)
    assert forward["ptps_resolved"] == 1
    assert back["ptps_resolved"] == 0
    assert back["_ptps_excluded_stale"] == 1


def test_as_of_is_recorded_on_every_row():
    """A snapshot whose own as_of is not in its features cannot be audited."""
    assert build()["_as_of"] == AS_OF.isoformat()


# ── Label leakage ────────────────────────────────────────────────────────────
def test_no_forbidden_key_reaches_the_features():
    f = build(visits=[visit()], cases=[case()])
    assert _FORBIDDEN_FEATURE_KEYS & set(f) == set()


def test_leakage_raises_rather_than_logs():
    """A leaked label does not fail a test or degrade a metric — it makes the
    model look better than it is. Loud beats logged."""
    for key in sorted(_FORBIDDEN_FEATURE_KEYS):
        polluted = dict(build(), **{key: 99.0})
        with pytest.raises(ValueError, match="leakage"):
            SVC._raise_if_leaked(polluted)


# ── Behavioural aggregation ──────────────────────────────────────────────────
def test_only_resolved_promises_count():
    """ACTIVE and RESCHEDULED promises are still in play. Counting them as
    failures marks a borrower down for a promise not yet due."""
    f = build(ptps=[ptp(status=PTPStatus.ACTIVE),
                    ptp(status=PTPStatus.RESCHEDULED),
                    ptp(status=PTPStatus.HONORED),
                    ptp(status=PTPStatus.BROKEN)])
    assert f["ptps_resolved"] == 2
    assert f["ptps_honored"] == 1


def test_partially_honored_counts_as_kept():
    f = build(ptps=[ptp(status=PTPStatus.PARTIALLY_HONORED)])
    assert f["ptps_resolved"] == 1 and f["ptps_honored"] == 1


def test_not_available_is_not_adverse_conduct():
    """Failing to find someone is a contactability fact, and that factor
    already counts it. Charging it to conduct as well penalises one event
    twice."""
    f = build(visits=[visit(met=False, outcome=VisitOutcome.NOT_AVAILABLE),
                      visit(met=False, outcome=VisitOutcome.ADDRESS_ISSUE),
                      visit(met=True, outcome=VisitOutcome.RTP)])
    assert f["visits"] == 3 and f["visits_met"] == 1
    assert f["adverse_visit_outcomes"] == 1


def test_target_is_none_when_there_are_no_cases():
    """None makes the payment factor abstain. Zero would make it score the
    borrower as having paid nothing of nothing."""
    assert build()["case_target_amount"] is None
    assert build(cases=[case()])["case_target_amount"] == 48000.0


# ── Snapshot write policy ────────────────────────────────────────────────────
def outcome(likelihood=60.0, as_of=AS_OF):
    """Band and category are DERIVED, never hardcoded — a fixture whose score
    and label disagree tests nothing real and hides the bug it looks like."""
    risk = 100.0 - likelihood
    return ScoreOutcome(
        loan_id="loan-1", customer_id="cust-1", case_id=None, as_of=as_of,
        likelihood=likelihood, risk_score=risk, band=band_for(likelihood),
        risk_category=risk_category_for(risk), evidence_coverage=0.8,
        model_version="scorecard-1.0.0", source="SCORECARD",
        features={}, factors=[],
    )


def snapshot(likelihood=60.0, days_ago=1):
    return NS(likelihood=likelihood, as_of_date=AS_OF - timedelta(days=days_ago),
              scored_at=None)


def test_first_score_is_always_written():
    assert SVC.should_snapshot(outcome(), None) is True


def test_unchanged_score_is_not_written():
    """Scoring every loan every night would store ~191k near-identical rows a
    year, most of them recording that nothing happened."""
    assert SVC.should_snapshot(outcome(60.0), snapshot(60.0)) is False


def test_movement_past_the_delta_is_written():
    moved = 60.0 + settings.REPAYMENT_SNAPSHOT_MIN_DELTA
    assert SVC.should_snapshot(outcome(moved), snapshot(60.0)) is True


def test_anchor_forces_a_row_through_a_flat_stretch():
    stale = snapshot(60.0, days_ago=settings.REPAYMENT_SNAPSHOT_ANCHOR_DAYS)
    assert SVC.should_snapshot(outcome(60.0), stale) is True


def test_state_change_always_writes():
    """The most valuable row in the table is the score as it stood immediately
    before whatever changed the account."""
    assert SVC.should_snapshot(outcome(60.0), snapshot(60.0),
                               state_changed=True) is True


# ── Customer rollup ──────────────────────────────────────────────────────────
def test_rollup_takes_the_worst_loan_not_the_average():
    """A customer with one clean loan and one 180-DPD NPA is a 180-DPD problem.
    Averaging lets a small clean loan mask a large defaulted one, and the agent
    is being sent to a person, not to a portfolio."""
    worst = SVC.rollup([outcome(90.0), outcome(12.0), outcome(55.0)])
    assert worst.likelihood == 12.0


def test_rollup_of_nothing_is_none():
    assert SVC.rollup([]) is None


# ── The payload ──────────────────────────────────────────────────────────────
def test_scorecard_never_reports_itself_as_modelled():
    """The analogue of LLMResult.ai_generated: the flag travels with the number
    so a UI cannot present hand-chosen weights as a model output."""
    payload = SVC.to_payload(outcome())
    assert payload["is_modelled"] is False
    assert payload["source"] == "SCORECARD"


def test_thin_evidence_is_reported_as_not_confident():
    thin = ScoreOutcome(
        loan_id="l", customer_id="c", case_id=None, as_of=AS_OF,
        likelihood=60.0, risk_score=40.0, band="UNCERTAIN",
        risk_category="MEDIUM",
        evidence_coverage=settings.REPAYMENT_MIN_COVERAGE_TO_SHOW - 0.01,
        model_version="scorecard-1.0.0", source="SCORECARD",
        features={}, factors=[])
    assert SVC.to_payload(thin)["is_confident"] is False
    assert SVC.to_payload(outcome())["is_confident"] is True


# ── The scorer seam ──────────────────────────────────────────────────────────
def test_scorecard_is_the_active_tier():
    r = repayment.resolved_scorer()
    assert r.source == SOURCE_SCORECARD
    assert r.status == repayment.OK and r.degraded is False


def test_unimplemented_tier_falls_back_visibly(monkeypatch):
    """command_center is named but not built. Asking for it must degrade to the
    scorecard AND say so — a silent fallback is how six AI features spent months
    serving written-in answers that looked like model output."""
    monkeypatch.setattr(settings, "REPAYMENT_SCORER", "command_center")
    r = repayment.resolved_scorer()
    assert r.source == SOURCE_SCORECARD
    assert r.status == repayment.NOT_CONFIGURED
    assert r.degraded is True and r.reason


def test_nonsense_tier_does_not_raise(monkeypatch):
    """This resolves inside a Celery task at 19:45. A typo in an env var must
    not take the nightly run down."""
    monkeypatch.setattr(settings, "REPAYMENT_SCORER", "sorcecard")
    r = repayment.resolved_scorer()
    assert r.source == SOURCE_SCORECARD and r.status == repayment.FELL_BACK


def test_scorecard_never_claims_to_be_modelled(monkeypatch):
    for value in ("scorecard", "command_center", "model", "nonsense", ""):
        monkeypatch.setattr(settings, "REPAYMENT_SCORER", value)
        assert repayment.resolved_scorer().is_modelled is False


def test_health_reports_both_switches():
    """An operator asking "is this thing touching my data?" must not have to
    read the code to find out."""
    h = repayment.health()
    assert h["writes_risk_score"] == settings.REPAYMENT_WRITE_RISK_SCORE
    assert h["reprices_open_cases"] == settings.REPAYMENT_REPRICE_OPEN_CASES
    assert h["active_scorer"] == SOURCE_SCORECARD
    assert h["is_modelled"] is False


# ── The kill switch ──────────────────────────────────────────────────────────
def test_kill_switch_off_writes_nothing(monkeypatch):
    """REPAYMENT_WRITE_RISK_SCORE=false must leave the legacy column exactly
    where it stands. This is the rollback: one env var and a restart."""
    monkeypatch.setattr(settings, "REPAYMENT_WRITE_RISK_SCORE", False)
    cust = NS(risk_score=64.9, risk_category="HIGH")
    wrote = SVC._apply_risk_score(cust, outcome(likelihood=95.0))
    assert wrote is False
    assert cust.risk_score == 64.9 and cust.risk_category == "HIGH"


def test_kill_switch_on_writes_score_and_category_together(monkeypatch):
    """They are one logical value. Letting them diverge would put a green badge
    on a customer whose number says critical, and nothing would raise."""
    monkeypatch.setattr(settings, "REPAYMENT_WRITE_RISK_SCORE", True)
    cust = NS(risk_score=64.9, risk_category="HIGH")
    wrote = SVC._apply_risk_score(cust, outcome(likelihood=95.0))
    assert wrote is True
    assert cust.risk_score == 5.0
    assert cust.risk_category.value == "LOW"


def test_apply_risk_score_is_the_only_write_site():
    """grep -rn "_apply_risk_score" is the whole answer to "what can change
    Customer.risk_score". seed_data.py:458 and ingest_daily.py:96 not having
    that property is how they drifted into two different formulas."""
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in root.rglob("*.py"):
        for num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.risk_score\s*=", line) or re.search(
                    r"\.risk_category\s*=", line):
                if path.name != "repayment_service.py":
                    offenders.append(f"{path.relative_to(root)}:{num}")
    assert offenders == [], f"risk_score written outside the single site: {offenders}"


def test_payload_carries_no_eligibility_verdict():
    """Confirmed as a product requirement 2026-08-21: the score is decision
    support. Allocation and eligibility constraints stay independent of it."""
    payload = SVC.to_payload(outcome(likelihood=2.0))
    for key in ("blocked", "eligible", "do_not_visit", "suppress", "allocatable"):
        assert key not in payload
