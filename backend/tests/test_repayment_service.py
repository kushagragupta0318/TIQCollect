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
from tests._db import test_id
from app.services.repayment_service import (
    _FORBIDDEN_FEATURE_KEYS, RepaymentService, ScoreOutcome,
)

SVC = RepaymentService(db=None)

AS_OF = date(2026, 8, 21)


def loan(**over):
    base = dict(
        id=test_id("loan-1"), customer_id=test_id("cust-1"), dpd=75, loan_type=LoanType.PERSONAL,
        emi_amount=18400.0, overdue_amount=73600.0,
        outstanding_principal=520000.0, last_payment_amount=9000.0,
        legal_status="NONE", settlement_status="NONE",
        # Added 2026-08-24 alongside the recovery scorecard. A stand-in for a
        # Loan must carry what a Loan carries — a stub that omits a real column
        # makes the builder look tolerant of missing data when the model can
        # never actually present it, and hides the omission until something
        # reads the column for real.
        total_outstanding=592000.0, penal_charges=12000.0, npa_flag=False,
        last_payment_date=date(2026, 7, 4),
    )
    base.update(over)
    return NS(**base)


def customer(**over):
    base = dict(
        id=test_id("cust-1"), cibil_score=610, customer_segment="SALARIED",
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
    base = dict(id=test_id("case-1"), target_amount=48000.0, collected_amount=0.0)
    base.update(over)
    return NS(**base)


_UNSET = object()


def build(**kw):
    """Features for the default loan and customer, unless one is passed in.

    `loan=` / `customer=` were added 2026-08-24 so the recovery tests can vary the
    LOAN rather than only its behavioural history — collateral, penal charges and
    the balance all live on the loan, and none of them could be reached before.
    """
    ln = kw.pop("loan", None) or loan()
    cust = kw.pop("customer", _UNSET)
    if cust is _UNSET:
        cust = customer()
    return SVC.build_features(ln, cust, AS_OF, **kw)


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
def recovery_outcome(likelihood=60.0, as_of=AS_OF, potential="MEDIUM", rate_90=0.42,
                     total_outstanding=592000.0):
    """An outcome that DID produce a recovery label.

    The plain outcome() below deliberately carries none, so every pre-recovery
    call site keeps testing exactly what it always tested. This one is for the
    clauses that only fire once a label exists.
    """
    risk = 100.0 - likelihood
    return ScoreOutcome(
        loan_id=test_id("loan-1"), customer_id=test_id("cust-1"), case_id=None, as_of=as_of,
        likelihood=likelihood, risk_score=risk, band=band_for(likelihood),
        risk_category=risk_category_for(risk), evidence_coverage=0.8,
        model_version="scorecard-1.0.0", source="SCORECARD",
        features={"total_outstanding": total_outstanding}, factors=[],
        recovery_potential=potential, recovery_rate_30=round(rate_90 * 0.3, 4),
        recovery_rate_60=round(rate_90 * 0.6, 4), recovery_rate_90=rate_90,
        recovery_speed_index=0.5, recovery_evidence_coverage=0.77,
        recovery_model_version="recovery-scorecard-1.0.0",
        recovery_source="SCORECARD", recovery_factors=[{"code": "AGEING"}],
        recovery_speed_reasons=["collateral realisation takes quarters"],
    )


def outcome(likelihood=60.0, as_of=AS_OF):
    """Band and category are DERIVED, never hardcoded — a fixture whose score
    and label disagree tests nothing real and hides the bug it looks like."""
    risk = 100.0 - likelihood
    return ScoreOutcome(
        loan_id=test_id("loan-1"), customer_id=test_id("cust-1"), case_id=None, as_of=as_of,
        likelihood=likelihood, risk_score=risk, band=band_for(likelihood),
        risk_category=risk_category_for(risk), evidence_coverage=0.8,
        model_version="scorecard-1.0.0", source="SCORECARD",
        features={}, factors=[],
    )


def snapshot(likelihood=60.0, days_ago=1, recovery_rate_90=0.42,
             recovery_potential="MEDIUM"):
    """A stand-in for an EXISTING snapshot row.

    The recovery fields default to populated — the common case once this feature
    has been running. Pass recovery_rate_90=None to model a row written before it
    shipped, which is what test_null_recovery_forces_a_new_row exercises.
    """
    return NS(likelihood=likelihood, as_of_date=AS_OF - timedelta(days=days_ago),
              scored_at=None, recovery_rate_90=recovery_rate_90,
              recovery_potential=recovery_potential)


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


# ── The four feature keys the recovery scorecard needs (2026-08-24) ──────────
def test_recovery_feature_keys_are_present():
    """The recovery scorecard reads these four. Absent, every one of its ratio
    factors abstains at once and the whole book scores at the base rate — a
    uniform label that looks plausible and says nothing."""
    f = build()
    assert f["total_outstanding"] == 592000.0
    assert f["penal_charges"] == 12000.0
    assert f["npa_flag"] is False
    assert "days_since_last_payment" in f


def test_new_keys_did_not_disturb_the_repayment_scorecard():
    """Purely additive. The repayment factors read by name and ignore extras, so
    SCORECARD_VERSION must NOT need a bump — asserted, because bumping it would
    silently split the training set across two model_versions."""
    from app.ml.repayment_scorecard import SCORECARD_VERSION
    from app.ml.repayment_scorecard import score as repayment_score
    added = {"total_outstanding", "penal_charges", "npa_flag",
             "days_since_last_payment"}
    before = repayment_score({k: v for k, v in build().items() if k not in added})
    after = repayment_score(build())
    assert before["likelihood"] == after["likelihood"]
    assert after["model_version"] == SCORECARD_VERSION


def test_recency_comes_from_the_ledger_when_the_ledger_has_it():
    """The ledger is bounded by as_of earlier in the builder, so it cannot leak.
    Loan.last_payment_date can."""
    f = build(payments=[payment(days_ago=3)])
    assert f["days_since_last_payment"] == 3
    assert f["_payment_recency_source"] == "LEDGER"


def test_recency_falls_back_to_the_loan_column_only_for_a_past_date():
    """The column is windowless, so it still answers for a borrower who last paid
    longer ago than the behaviour window. Worth keeping as a fallback."""
    f = build(loan=loan(last_payment_date=date(2026, 6, 1)))
    assert f["days_since_last_payment"] == (AS_OF - date(2026, 6, 1)).days
    assert f["_payment_recency_source"] == "LOAN_COLUMN"


def test_recency_refuses_a_loan_column_date_after_as_of():
    """THE LEAK THIS GUARDS. Loan.last_payment_date is overwritten in place by
    ingest with no history, exactly like Loan.dpd — so on a backfill it reports a
    payment that had not happened yet on as_of. Accepting it would teach a model
    that money already known to have arrived predicts money arriving; the
    measured accuracy would be excellent and worthless."""
    f = build(loan=loan(last_payment_date=date(2026, 9, 15)))   # after AS_OF
    assert f["days_since_last_payment"] is None
    assert f["_payment_recency_source"] == "LOAN_COLUMN_REFUSED_FUTURE"


def test_recency_is_none_when_nothing_was_ever_paid():
    """None, not zero. Zero days would read as "paid today"."""
    f = build(loan=loan(last_payment_date=None))
    assert f["days_since_last_payment"] is None
    assert f["_payment_recency_source"] == "NONE_ON_RECORD"


def test_recovery_outputs_are_forbidden_as_features():
    """The ban runs both ways: the recovery scorecard must not read the repayment
    likelihood, and nothing may read the recovery rates back in. Feeding either
    into the other collapses the pair into one number wearing two labels."""
    for key in ("recovery_rate_90", "recovery_potential", "speed_index",
                "expected_recoverable_amount"):
        assert key in _FORBIDDEN_FEATURE_KEYS
        with pytest.raises(ValueError, match="Label leakage"):
            SVC._raise_if_leaked({"dpd": 75, key: 0.4})


# ── The NULL -> computed rule ────────────────────────────────────────────────
def test_null_recovery_forces_a_new_row_even_on_a_flat_likelihood():
    """WITHOUT THIS CLAUSE THE FEATURE WRITES NOTHING on a stable book. Every row
    written before recovery shipped has NULL recovery fields, as does every loan
    the nightly demo feed creates. If the likelihood has not moved, no other
    clause fires, no row is written, and the prediction is silently discarded —
    the feature would appear to work while persisting nothing at all."""
    previous = snapshot(likelihood=60.0, days_ago=1, recovery_rate_90=None,
                        recovery_potential=None)
    assert SVC.should_snapshot(recovery_outcome(likelihood=60.0), previous) is True


def test_a_populated_recovery_row_is_not_rewritten_for_nothing():
    """The counterpart. If NULL -> computed were implemented as "always write",
    the table would gain a row per loan per night and the write policy would be
    defeated."""
    previous = snapshot(likelihood=60.0, days_ago=1, recovery_rate_90=0.42,
                        recovery_potential="MEDIUM")
    assert SVC.should_snapshot(
        recovery_outcome(likelihood=60.0, potential="MEDIUM"), previous) is False


def test_recovery_band_move_is_written_on_a_flat_likelihood():
    """The label a manager acted on has changed. Banded rather than thresholded on
    the rate: the label is what surfaces, and a three-bucket value moves far more
    slowly than a 0.1-precision likelihood."""
    previous = snapshot(likelihood=60.0, days_ago=1, recovery_potential="LOW")
    assert SVC.should_snapshot(
        recovery_outcome(likelihood=60.0, potential="HIGH"), previous) is True


def test_an_outcome_without_recovery_leaves_the_policy_untouched():
    """A caller that produced no recovery score must not start forcing rows."""
    previous = snapshot(likelihood=60.0, days_ago=1, recovery_rate_90=None,
                        recovery_potential=None)
    assert SVC.should_snapshot(outcome(likelihood=60.0), previous) is False


# ── Persistence, both branches ───────────────────────────────────────────────
def test_persist_writes_recovery_on_a_new_row():
    row = SVC.persist(recovery_outcome(potential="HIGH", rate_90=0.61), None)
    assert row.recovery_potential == "HIGH"
    assert row.recovery_rate_90 == 0.61
    assert row.recovery_model_version == "recovery-scorecard-1.0.0"
    assert row.recovery_contributions["factors"] == [{"code": "AGEING"}]


def test_persist_refreshes_recovery_on_a_same_day_rescore():
    """MISSING THIS BRANCH IS A SILENT STALENESS BUG. persist() updates in place
    when the row is today's; writing only the insert path would refresh the
    likelihood and leave yesterday's recovery label beside it, nothing raising."""
    existing = NS(as_of_date=AS_OF, scored_at=None, likelihood=1.0,
                  recovery_potential="LOW", recovery_rate_90=0.10)
    SVC.persist(recovery_outcome(as_of=AS_OF, potential="HIGH", rate_90=0.61),
                existing)
    assert existing.recovery_potential == "HIGH"
    assert existing.recovery_rate_90 == 0.61


def test_persist_does_not_erase_a_label_when_none_was_computed():
    """Guarded on has_recovery, so a caller that produced no recovery score leaves
    the existing label alone rather than nulling it."""
    existing = NS(as_of_date=AS_OF, scored_at=None, likelihood=1.0,
                  recovery_potential="HIGH", recovery_rate_90=0.61)
    SVC.persist(outcome(as_of=AS_OF), existing)
    assert existing.recovery_potential == "HIGH"


# ── The gate ─────────────────────────────────────────────────────────────────
def test_recovery_label_is_not_written_while_the_gate_is_shut():
    """RECOVERY_WRITE_LABEL defaults False and stays False until a dry run and a
    distribution review are done. The snapshot is written regardless — that split
    is what lets the manager surface show computed labels while the shared Loan
    column is left alone."""
    assert settings.RECOVERY_WRITE_LABEL is False
    ln = loan(recovery_potential=None)
    assert SVC._apply_recovery_label(ln, recovery_outcome(potential="HIGH")) is False
    assert ln.recovery_potential is None


def test_recovery_label_writes_when_the_gate_is_opened(monkeypatch):
    from app.models.loan import RecoveryPotential
    monkeypatch.setattr(settings, "RECOVERY_WRITE_LABEL", True)
    ln = loan(recovery_potential=None)
    assert SVC._apply_recovery_label(ln, recovery_outcome(potential="HIGH")) is True
    assert ln.recovery_potential == RecoveryPotential.HIGH


def test_recovery_label_write_is_idempotent(monkeypatch):
    """Returns False when the label has not moved, so the run counter reports rows
    actually changed rather than rows visited."""
    from app.models.loan import RecoveryPotential
    monkeypatch.setattr(settings, "RECOVERY_WRITE_LABEL", True)
    ln = loan(recovery_potential=RecoveryPotential.HIGH)
    assert SVC._apply_recovery_label(ln, recovery_outcome(potential="HIGH")) is False


def test_apply_recovery_label_is_the_only_write_site():
    """The counterpart of test_apply_risk_score_is_the_only_write_site. This column
    had TWO writers before today, both random and separately weighted
    (seed_data.py:481-509 and scripts/add_recovery_potential.py), disagreeing with
    each other while nothing read either."""
    # Anchored on `loan.` and excluding `==`. RepaymentSnapshot has a column of
    # the same name, and persist() assigning to it is a different act entirely —
    # writing the frozen record, not the live column another system may read.
    pattern = re.compile(r"loan\.recovery_potential\s*=(?!=)")
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in root.rglob("*.py"):
        for num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line) and path.name != "repayment_service.py":
                offenders.append(f"{path.relative_to(root)}:{num}")
    assert offenders == [], (
        f"recovery_potential written outside the single site: {offenders}")


# ── The two scores must be able to disagree ──────────────────────────────────
def _both(**over):
    """Score one loan through BOTH scorecards, from one feature dict."""
    from app.ml.recovery_scorecard import score as rec_score
    from app.ml.repayment_scorecard import score as rep_score
    f = build(**over)
    return rep_score(f), rec_score(f)


def test_unlikely_to_pay_can_still_be_high_to_recover():
    """THE MOST USEFUL THING THIS PAIR SAYS. A hostile borrower who breaks
    promises on a GOLD loan is a poor bet on willingness and a good bet on money —
    the collateral pays whether they cooperate or not. A recovery label derived
    from the repayment likelihood could never produce this row, which is exactly
    why it is computed independently."""
    rep, rec = _both(
        loan=loan(loan_type=LoanType.GOLD, dpd=85, overdue_amount=40000.0,
                  total_outstanding=800000.0, penal_charges=0.0,
                  last_payment_date=date(2026, 8, 14), last_payment_amount=18400.0),
        customer=customer(cibil_score=330, is_hostile=True, fraud_flag=True),
        visits=[visit(days_ago=d, met=True, outcome=VisitOutcome.RTP)
                for d in (2, 5, 9)],
        ptps=[ptp(days_ago=d, status=PTPStatus.BROKEN, updated_days_ago=d)
              for d in (10, 25, 40)],
        payments=[payment(days_ago=7)],
    )
    assert rep["likelihood"] < 40.0, rep["likelihood"]
    assert rec["recovery_potential"] == "HIGH", rec["recovery_rate_90"]


def test_likely_to_pay_can_still_be_low_to_recover():
    """The other direction, and the one that stops the label being read as a
    politer spelling of the repayment score. A cooperative borrower on an
    unsecured card — keeps promises, always in, strong bureau — is genuinely
    LIKELY to pay. What they can realistically produce is still a sliver of the
    balance: nothing secures it, 92% of it is already overdue, and ₹95,000 of the
    demand is penal charges they never borrowed.

    Willing and worth little is a real category, and spreading field effort as if
    it were the same as willing-and-worth-a-lot is the misallocation this label
    exists to correct."""
    rep, rec = _both(
        loan=loan(loan_type=LoanType.CREDIT_CARD, dpd=60, overdue_amount=480000.0,
                  total_outstanding=520000.0, penal_charges=95000.0,
                  npa_flag=True, settlement_status="NEGOTIATING",
                  last_payment_date=date(2026, 8, 11), last_payment_amount=6000.0),
        customer=customer(cibil_score=780, customer_segment="SALARIED"),
        visits=[visit(days_ago=d, met=True) for d in (2, 6, 11, 16)],
        ptps=[ptp(days_ago=d, status=PTPStatus.HONORED, updated_days_ago=d)
              for d in (12, 30, 55)],
    )
    # Asserted on the BAND, not a magic number: the claim is "the product would
    # tell an agent this borrower is likely to pay", and the band is what it says.
    assert rep["band"] == "LIKELY", (rep["band"], rep["likelihood"])
    assert rec["recovery_potential"] == "LOW", rec["recovery_rate_90"]


def test_neither_score_is_a_transform_of_the_other():
    """If one were derived from the other, a set of loans would order identically
    (or exactly inverted) under both. Asserting the orderings differ is what keeps
    the second number worth showing at all."""
    def cooperative():
        return dict(
            customer=customer(cibil_score=740),
            visits=[visit(days_ago=d, met=True) for d in (3, 8, 14)],
            ptps=[ptp(days_ago=d, status=PTPStatus.HONORED, updated_days_ago=d)
                  for d in (10, 28, 44)],
        )

    def obstructive():
        return dict(
            customer=customer(cibil_score=340, is_hostile=True),
            visits=[visit(days_ago=d, met=True, outcome=VisitOutcome.RTP)
                    for d in (3, 8, 14)],
            ptps=[ptp(days_ago=d, status=PTPStatus.BROKEN, updated_days_ago=d)
                  for d in (10, 28, 44)],
        )

    # Collateral crossed with conduct. Conduct dominates the likelihood (promise
    # history alone is 20 of 124 there and collateral only 6); collateral is the
    # single largest weight on the recovery side. So the secured-but-obstructive
    # loan and the unsecured-but-cooperative one swap places between the two
    # orderings — which is the property being asserted.
    variants = [
        dict(loan=loan(loan_type=LoanType.GOLD, dpd=45), **cooperative()),
        dict(loan=loan(loan_type=LoanType.GOLD, dpd=210), **obstructive()),
        dict(loan=loan(loan_type=LoanType.CREDIT_CARD, dpd=45), **cooperative()),
        dict(loan=loan(loan_type=LoanType.CREDIT_CARD, dpd=210), **obstructive()),
    ]
    scored = [_both(**v) for v in variants]
    by_likelihood = [i for i, _ in sorted(
        enumerate(scored), key=lambda t: t[1][0]["likelihood"])]
    by_recovery = [i for i, _ in sorted(
        enumerate(scored), key=lambda t: t[1][1]["recovery_rate_90"])]
    assert by_likelihood != by_recovery
    assert by_likelihood != list(reversed(by_recovery))


# ── The outcome denominator (2026-09-03) ─────────────────────────────────────
# _infer_outcome used to divide by every case the loan had ever had. A case is
# ONE collection cycle, so the bar for REPAID grew with every cycle a borrower
# completed, and after a few it could not be cleared at all. These pin the
# corrected denominator: what was still owed on as_of_date.

def _snap(as_of=AS_OF):
    return NS(as_of_date=as_of, loan_id=test_id("loan-1"))


def _pay_on(day, amount):
    return NS(payment_date=datetime.combine(day, datetime.min.time()) + timedelta(hours=12),
              amount=amount, status="VERIFIED", case_id=None)


def test_a_cycle_settled_before_the_score_is_not_in_the_denominator():
    """The regression. Two closed cycles plus one open one used to demand
    3 x 10,000 in 30 days; only the open 10,000 was ever collectable."""
    closed_a = NS(id=test_id("c1"), target_amount=10000.0, status="PAID")
    closed_b = NS(id=test_id("c2"), target_amount=10000.0, status="PAID")
    live = NS(id=test_id("c3"), target_amount=10000.0, status="ASSIGNED")
    payments = {
        test_id("c1"): [_pay_on(AS_OF - timedelta(days=90), 10000.0)],
        test_id("c2"): [_pay_on(AS_OF - timedelta(days=45), 10000.0)],
        test_id("c3"): [_pay_on(AS_OF + timedelta(days=5), 9500.0)],
    }
    outcome, amount = SVC._infer_outcome(_snap(), [closed_a, closed_b, live], payments)

    # 9,500 against the 10,000 actually outstanding is 95% -> REPAID.
    # Under the old denominator it was 9,500/30,000 = 32% -> PARTIAL.
    assert outcome == "REPAID"
    assert amount == pytest.approx(9500.0)


def test_a_part_paid_cycle_contributes_only_its_remainder():
    case = NS(id=test_id("c1"), target_amount=10000.0, status="PARTIALLY_PAID")
    payments = {test_id("c1"): [_pay_on(AS_OF - timedelta(days=10), 7000.0),
                       _pay_on(AS_OF + timedelta(days=3), 2900.0)]}
    outcome, amount = SVC._infer_outcome(_snap(), [case], payments)

    # 2,900 against the 3,000 still owed is 97% -> REPAID.
    assert outcome == "REPAID"
    assert amount == pytest.approx(2900.0)


def test_short_of_the_outstanding_balance_is_still_partial():
    """The fix must not turn every payment into a REPAID."""
    case = NS(id=test_id("c1"), target_amount=10000.0, status="ASSIGNED")
    payments = {test_id("c1"): [_pay_on(AS_OF + timedelta(days=4), 2000.0)]}
    outcome, amount = SVC._infer_outcome(_snap(), [case], payments)
    assert outcome == "PARTIAL"
    assert amount == pytest.approx(2000.0)


def test_the_denominator_is_read_from_the_ledger_not_from_collected_amount():
    """Case.collected_amount is overwritten in place and holds TODAY's total,
    so on a historical row it has already absorbed the label's own payments.
    Setting it to a contradictory value must change nothing."""
    honest = NS(id=test_id("c1"), target_amount=10000.0, status="ASSIGNED", collected_amount=0.0)
    lying = NS(id=test_id("c1"), target_amount=10000.0, status="ASSIGNED", collected_amount=9999.0)
    payments = {test_id("c1"): [_pay_on(AS_OF - timedelta(days=5), 6000.0),
                       _pay_on(AS_OF + timedelta(days=5), 3800.0)]}

    assert (SVC._infer_outcome(_snap(), [honest], payments)
            == SVC._infer_outcome(_snap(), [lying], payments))


def test_payments_on_the_as_of_day_count_as_already_collected():
    """Same boundary as everywhere else: as_of belongs to the past. A payment
    on the day reduces the outstanding balance; it is not a future recovery."""
    case = NS(id=test_id("c1"), target_amount=10000.0, status="ASSIGNED")
    payments = {test_id("c1"): [_pay_on(AS_OF, 4000.0),
                       _pay_on(AS_OF + timedelta(days=1), 5800.0)]}
    outcome, amount = SVC._infer_outcome(_snap(), [case], payments)

    # The as_of-day 4,000 is not in `received`...
    assert amount == pytest.approx(5800.0)
    # ...but it did reduce the target to 6,000, so 5,800 is 97% -> REPAID.
    assert outcome == "REPAID"


def test_a_fully_settled_loan_with_no_further_money_is_not_repaid():
    """Nothing outstanding and nothing received is NO_PAYMENT, not a division
    by zero and not a free REPAID."""
    case = NS(id=test_id("c1"), target_amount=10000.0, status="ASSIGNED")
    payments = {test_id("c1"): [_pay_on(AS_OF - timedelta(days=20), 10000.0)]}
    outcome, amount = SVC._infer_outcome(_snap(), [case], payments)
    assert outcome == "NO_PAYMENT"
    assert amount is None


# ── Which case a snapshot is about (2026-09-07) ─────────────────────────────
# score_loan used to set case_id with `next((c.id for c in cases), None)` over a
# list built with no status filter and no ORDER BY — an arbitrary case, and on a
# loan that has re-delinquented, usually a closed one. Measured on a synthetic
# book: 77.2% of snapshots named a case whose last visit was a median of 130
# days earlier.

def test_case_as_of_picks_the_most_recently_created_case():
    from datetime import date, datetime, timezone
    from app.services.repayment_service import _case_as_of
    from types import SimpleNamespace

    def case(cid, created):
        return SimpleNamespace(
            id=cid, created_at=datetime(*created, tzinfo=timezone.utc))

    cases = [case("old", (2026, 1, 5)), case("newest", (2026, 6, 1)),
             case("middle", (2026, 3, 10))]
    assert _case_as_of(cases, date(2026, 8, 1)) == "newest"
    # Order of the input must not matter — that was the whole defect.
    assert _case_as_of(list(reversed(cases)), date(2026, 8, 1)) == "newest"


def test_case_as_of_never_looks_forward():
    """A case created after the scoring date did not exist then. Picking it
    would attach the snapshot to something the scorer could not have seen."""
    from datetime import date, datetime, timezone
    from app.services.repayment_service import _case_as_of
    from types import SimpleNamespace

    cases = [
        SimpleNamespace(id=test_id("existed"), created_at=datetime(2026, 3, 1, tzinfo=timezone.utc)),
        SimpleNamespace(id=test_id("future"), created_at=datetime(2026, 9, 1, tzinfo=timezone.utc)),
    ]
    assert _case_as_of(cases, date(2026, 5, 1)) == test_id("existed")


def test_case_as_of_degrades_to_the_old_behaviour_without_timestamps():
    """A book with no usable created_at must keep working rather than lose the
    snapshot-to-case link entirely."""
    from datetime import date
    from app.services.repayment_service import _case_as_of
    from types import SimpleNamespace

    cases = [SimpleNamespace(id=test_id("first"), created_at=None),
             SimpleNamespace(id=test_id("second"), created_at=None)]
    assert _case_as_of(cases, date(2026, 5, 1)) == test_id("first")
    assert _case_as_of([], date(2026, 5, 1)) is None
