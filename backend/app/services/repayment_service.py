# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-21 — New file. The only thing in the codebase that writes a repayment
#   score, and — once ingest_daily.py is cut over — the only thing that writes
#   Customer.risk_score at all.
#
#   One writer is the point. There were two before (seed_data.py:458-464 and
#   ingest_daily.py:96-107), nominally the same formula, and they had already
#   drifted: the seed floored at 30 and could never produce RiskCategory.LOW,
#   ingest floored at 0 and could. Nobody noticed because nothing rendered the
#   column. Three copies of one rule is how that happens.
#
#   The hard part here is not the arithmetic — that is ml/repayment_scorecard.py
#   and it is pure. The hard part is POINT-IN-TIME CORRECTNESS. Every feature
#   must be read as it stood on as_of_date, because these rows are the training
#   set for a future model and a single leaked future value silently inflates
#   its measured accuracy while destroying its real one. Loan.dpd and PTP.status
#   are both overwritten in place with no history, so the leak is a real risk
#   rather than a theoretical one. Hence: no feature query without an as_of
#   bound, and no way to call the builder without passing the date.
#
#   Scoring GRAIN IS THE LOAN, not the customer. dpd, overdue_amount and
#   loan_type are loan-level, and scoring per-customer is exactly what let
#   seed_data.py:1327 score a customer against a dpd_hint belonging to none of
#   their loans. The customer rollup takes their worst loan, because someone
#   with one clean loan and one 180-DPD NPA is a 180-DPD problem.
#
#   The score is DECISION SUPPORT and gates nothing. Eligibility lives in
#   ml/eligibility.py and stays there; a low likelihood must never suppress a
#   visit, or the system becomes self-fulfilling — never visited, never pays,
#   score falls further, never visited.
# ───────────────────────────────────────────────────────────────────────────
"""Score loans, roll up to customers, and freeze the evidence for later."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Iterable

import structlog

from app.core.config import settings
from app.ml.recovery_scorecard import score as recovery_score
from app.ml.repayment_scorecard import (
    SCORECARD_VERSION, risk_category_for, score as scorecard_score,
)
from app.models.case import Case, CaseStatus
from app.models.customer import Customer, RiskCategory
from app.models.loan import Loan, RecoveryPotential
from app.models.payment import Payment, PaymentStatus
from app.models.ptp import PTP, PTPStatus
from app.models.repayment_snapshot import (
    OUTCOME_CENSORED, OUTCOME_NO_PAYMENT, OUTCOME_PARTIAL, OUTCOME_REPAID,
    OUTCOME_SOURCE_INFERRED, OUTCOME_SOURCE_PAYMENT, RepaymentSnapshot,
    SOURCE_SCORECARD, TRIGGER_NIGHTLY,
)
from app.models.visit import Visit, VisitOutcome

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = structlog.get_logger()

# Outcomes that mean the borrower pushed back rather than simply was not in.
# NOT_AVAILABLE and ADDRESS_ISSUE are deliberately absent: failing to find
# someone is a contactability fact (that factor already counts it) and holding
# it against their conduct as well would penalise the same event twice.
_ADVERSE_OUTCOMES = frozenset({
    VisitOutcome.RTP, VisitOutcome.DISPUTE, VisitOutcome.BROKEN_PTP,
})

# A promise is "resolved" once it is no longer in play. ACTIVE and RESCHEDULED
# are still open — counting them as failures would mark a borrower down for a
# promise whose date has not yet arrived.
_RESOLVED_PTP = frozenset({
    PTPStatus.HONORED, PTPStatus.BROKEN,
    PTPStatus.PARTIALLY_HONORED, PTPStatus.EXPIRED,
})
_KEPT_PTP = frozenset({PTPStatus.HONORED, PTPStatus.PARTIALLY_HONORED})

# Case states where "no money arrived" is NOT the borrower declining to pay.
# A recall, a write-off or an administrative close is the bank's own decision;
# a model trained on those as negatives learns to blame the borrower for them.
_CENSORING_STATUSES = frozenset({
    CaseStatus.CLOSED, CaseStatus.WRITTEN_OFF, CaseStatus.ESCALATED,
})

# This system's own outputs. Feeding any of them back in trains a model to
# predict itself, which reads as near-perfect accuracy and is worth nothing.
# bank_risk_score is here because in seeded data it is itself a function of dpd
# and cibil (seed_data.py:1398), so including it triple-counts delinquency; on
# real data it is a partner's model output and belongs to a later tier, never to
# this scorecard.
#
# 2026-08-24 — recovery_potential stays banned, and its siblings joined it. It
# used to be listed because seeded data derived it from dpd; it is now listed
# because WE compute it (ml/recovery_scorecard.py), which is the stronger reason.
# The ban runs both ways by design: the recovery scorecard does not read the
# repayment likelihood either. Two scorecards may share INPUTS — that is normal
# — but neither may eat the other's OUTPUT, or the pair collapses into one
# number wearing two labels, and the disagreement between them (unlikely to pay,
# high to recover) is the most useful thing either of them says.
_FORBIDDEN_FEATURE_KEYS = frozenset({
    "risk_score", "risk_category", "collection_priority_score",
    "bank_risk_score", "recovery_potential", "priority", "allocation_score",
    "recovery_rate_30", "recovery_rate_60", "recovery_rate_90",
    "recovery_band", "expected_recoverable_amount", "speed_index",
})

# Money that counts as ACTUALLY RECOVERED, for the recovery training label.
#
# 2026-08-24. VERIFIED only, approved as the business rule. The label is the
# ground truth a future model is fitted against, so it records money the bank
# confirmed landed — not money an agent receipted that may yet fail. REJECTED
# and REVERSED are precisely the cases where a receipt turned out to be false,
# and PENDING_VERIFICATION is the state they pass through, so counting pending
# money would mean counting some of the failures as successes.
#
# The known cost is a conservative bias where verification lags: a payment made
# on day 29 and verified on day 33 is missing from the 30-day figure. That is
# the right direction for a training label — it understates recovery rather than
# inventing it — and it shrinks at the longer horizons. On the ledger as at
# 2026-08-24 the exposure is 1 pending payment out of 460 (0.2%).
#
# DELIBERATELY NOT APPLIED to _infer_outcome below, which labels the REPAYMENT
# outcome and is out of scope here. The two are allowed to differ: this one
# measures rupees recovered, that one classifies borrower conduct.
_RECOVERED_PAYMENT_STATUSES = frozenset({PaymentStatus.VERIFIED.value})


@dataclass(frozen=True)
class ScoreOutcome:
    """One loan's score plus the frozen inputs behind it."""
    loan_id: str
    customer_id: str
    case_id: str | None
    as_of: date
    likelihood: float
    risk_score: float
    band: str
    risk_category: str
    evidence_coverage: float
    model_version: str
    source: str
    features: dict[str, Any]
    factors: list[dict[str, Any]]

    # ── Recovery potential (2026-08-24) ──────────────────────────────────────
    # A SECOND, INDEPENDENT score carried on the same outcome object, because it
    # is computed from the same point-in-time feature dict on the same loan and
    # splitting it into a parallel object would mean building that dict twice.
    #
    # Independent is the operative word: nothing below is derived from
    # `likelihood` above, and nothing above reads these. The two are free to
    # disagree, and their disagreement — unlikely to pay, high to recover — is
    # the most useful thing the pair says. See ml/recovery_scorecard.py.
    #
    # Defaulted so every existing construction site keeps working and a caller
    # that has not been taught about recovery yet degrades to "no recovery
    # score" rather than to a wrong one.
    recovery_potential: str | None = None
    recovery_rate_30: float | None = None
    recovery_rate_60: float | None = None
    recovery_rate_90: float | None = None
    recovery_speed_index: float | None = None
    recovery_evidence_coverage: float | None = None
    recovery_model_version: str | None = None
    recovery_source: str | None = None
    recovery_factors: list[dict[str, Any]] = field(default_factory=list)
    recovery_speed_reasons: list[str] = field(default_factory=list)

    @property
    def has_recovery(self) -> bool:
        """Whether a recovery label was produced at all.

        Load-bearing for should_snapshot: a row whose recovery fields are NULL
        must be rewritten once a label exists, even when the repayment
        likelihood has not moved a point.
        """
        return self.recovery_rate_90 is not None

    @property
    def is_modelled(self) -> bool:
        """False for a scorecard. The direct analogue of LLMResult.ai_generated
        — it travels with the number so a hand-weighted figure can never be
        rendered as a model output by a caller that forgot to check."""
        return self.source == "MODEL"

    @property
    def is_confident(self) -> bool:
        """Whether enough evidence spoke to show a NUMBER rather than a band."""
        return self.evidence_coverage >= settings.REPAYMENT_MIN_COVERAGE_TO_SHOW


def _end_of(as_of: date):
    """Exclusive upper bound for datetime comparisons against a date."""
    return as_of + timedelta(days=1)


def _parse_iso_date(value: Any) -> date | None:
    """Loan.last_payment_date is a String(10), not a Date. Parse defensively.

    Returns None rather than raising on anything unparseable: a malformed date on
    one loan must not take down the nightly scoring pass for the whole book.
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


class RepaymentService:
    """Scoring, snapshotting and the customer rollup.

    Instantiate with db=None to use the pure helpers in tests — the same
    convention as FraudService.
    """

    def __init__(self, db: "Session | None") -> None:
        self.db = db

    # ── Feature assembly ─────────────────────────────────────────────────────
    def build_features(
        self,
        loan: Loan,
        customer: Customer | None,
        as_of: date,
        *,
        cases: Iterable[Case] = (),
        visits: Iterable[Visit] = (),
        ptps: Iterable[PTP] = (),
        payments: Iterable[Payment] = (),
        is_backfill: bool = False,
    ) -> dict[str, Any]:
        """Features for one loan as they stood on `as_of`.

        There is deliberately no overload without a date. Rows built here are
        the training set, and a builder that could be called without a
        point-in-time bound would eventually be.

        The collections are passed IN rather than queried here so `rescore` can
        bulk-load once for the whole book instead of issuing four queries per
        loan — and so this method stays testable without a database.
        """
        window_start = as_of - timedelta(days=settings.REPAYMENT_BEHAVIOUR_WINDOW_DAYS)
        horizon = _end_of(as_of)

        visits = [
            v for v in visits
            if v.check_in_time is not None
            and v.check_in_time.date() >= window_start
            and v.check_in_time.date() <= as_of
        ]

        # PTP.status is mutated in place and there is no history table, so a
        # promise made before as_of but resolved after it reads with tomorrow's
        # status today. On a forward run that cannot happen — the future has
        # not occurred. On a backfill it certainly can, so those rows are
        # dropped and the count is recorded rather than silently absorbed.
        ptps = [p for p in ptps
                if p.committed_date is not None and window_start <= p.committed_date <= as_of]
        stale_ptps = 0
        if is_backfill:
            fresh = []
            for p in ptps:
                updated = getattr(p, "updated_at", None)
                if updated is not None and updated.date() > as_of:
                    stale_ptps += 1
                    continue
                fresh.append(p)
            ptps = fresh

        # Filtered on payment_date, NOT on status. `status` is current-state: a
        # payment verified next week would leak backwards into a score computed
        # today. What was known on the day is that the money was recorded.
        payments = [
            p for p in payments
            if p.payment_date is not None
            and window_start <= p.payment_date.date() <= as_of
        ]

        resolved = [p for p in ptps if p.status in _RESOLVED_PTP]
        target = sum(float(c.target_amount or 0.0) for c in cases) or None

        # Days since money last arrived — added 2026-08-24 for the recovery
        # scorecard, which needs payment RECENCY where the repayment scorecard
        # only needed payment SIZE.
        #
        # Derived from the payment LEDGER, not from Loan.last_payment_date, and
        # that is not a style preference. That column is overwritten in place by
        # ingest with no history, exactly like Loan.dpd and PTP.status, so on a
        # backfill it reports a payment that had not happened yet on as_of — the
        # precise leak this builder exists to prevent. The ledger rows are
        # already bounded above by as_of a few lines up.
        #
        # The column is still consulted as a FALLBACK, because the ledger is
        # windowed and a borrower who last paid 300 days ago has no row in it.
        # It is accepted only when it does not post-date as_of; a future value is
        # refused and recorded as refused rather than silently dropped.
        ledger_dates = [p.payment_date.date() for p in payments
                        if p.payment_date is not None]
        last_paid_on = max(ledger_dates) if ledger_dates else None
        recency_source = "LEDGER"
        if last_paid_on is None:
            fallback = _parse_iso_date(loan.last_payment_date)
            if fallback is None:
                recency_source = "NONE_ON_RECORD"
            elif fallback <= as_of:
                last_paid_on = fallback
                recency_source = "LOAN_COLUMN"
            else:
                recency_source = "LOAN_COLUMN_REFUSED_FUTURE"
        days_since_last_payment = (
            (as_of - last_paid_on).days if last_paid_on is not None else None
        )

        features: dict[str, Any] = {
            # Loan facts
            "dpd": int(loan.dpd or 0),
            "loan_type": loan.loan_type,
            "emi_amount": float(loan.emi_amount or 0.0),
            "overdue_amount": float(loan.overdue_amount or 0.0),
            "outstanding_principal": float(loan.outstanding_principal or 0.0),
            "last_payment_amount": float(loan.last_payment_amount or 0.0),
            "legal_status": loan.legal_status or "NONE",
            "settlement_status": loan.settlement_status or "NONE",
            # Added 2026-08-24 for ml/recovery_scorecard.py. Purely additive:
            # the repayment scorecard reads features by name through _f() and
            # ignores extras, so no repayment factor changes and
            # SCORECARD_VERSION is deliberately NOT bumped.
            #
            # total_outstanding is the DENOMINATOR of the recovery rate. It is
            # emphatically not a recovery factor — a rate must not be moved by an
            # absolute rupee amount, or the label means "share recovered" and
            # "amount at stake" at the same time. See the changelog header of
            # ml/recovery_scorecard.py.
            "total_outstanding": float(loan.total_outstanding or 0.0),
            "penal_charges": float(loan.penal_charges or 0.0),
            "npa_flag": bool(loan.npa_flag),
            "days_since_last_payment": days_since_last_payment,
            # Customer facts
            "cibil_score": customer.cibil_score if customer else None,
            "customer_segment": customer.customer_segment if customer else None,
            "is_hostile": bool(customer.is_hostile) if customer else False,
            "fraud_flag": bool(customer.fraud_flag) if customer else False,
            # Behaviour, all bounded by as_of
            "visits": len(visits),
            "visits_met": sum(1 for v in visits if v.customer_met),
            "adverse_visit_outcomes": sum(
                1 for v in visits if v.outcome in _ADVERSE_OUTCOMES),
            "ptps_resolved": len(resolved),
            "ptps_honored": sum(1 for p in resolved if p.status in _KEPT_PTP),
            "case_target_amount": target,
            "amount_paid_in_window": round(
                sum(float(p.amount or 0.0) for p in payments), 2),
            # Provenance, so a modeller can audit rather than assume
            "_as_of": as_of.isoformat(),
            "_window_days": settings.REPAYMENT_BEHAVIOUR_WINDOW_DAYS,
            "_horizon_exclusive": horizon.isoformat(),
            "_payment_status_at_scoring": "ANY",
            "_ptps_excluded_stale": stale_ptps,
            # LEDGER / LOAN_COLUMN / LOAN_COLUMN_REFUSED_FUTURE / NONE_ON_RECORD.
            # Carried so a modeller can drop rows whose recency came from the
            # mutable loan column rather than from the ledger, instead of having
            # to assume they are equivalent. They are not.
            "_payment_recency_source": recency_source,
        }

        self._raise_if_leaked(features)
        return features

    @staticmethod
    def _raise_if_leaked(features: dict[str, Any]) -> None:
        """Refuse to hand back a feature set containing this system's own output.

        Loud rather than logged. A leaked label does not fail a test or degrade
        a metric — it makes the model look better than it is in evaluation and
        worse in production, and by then the rows are already written.
        """
        leaked = _FORBIDDEN_FEATURE_KEYS & set(features)
        if leaked:
            raise ValueError(
                f"Label leakage: {sorted(leaked)} must never be a feature. "
                "These are this system's own outputs."
            )

    # ── Scoring ──────────────────────────────────────────────────────────────
    def score_loan(
        self,
        loan: Loan,
        customer: Customer | None,
        as_of: date,
        **kwargs: Any,
    ) -> ScoreOutcome:
        features = self.build_features(loan, customer, as_of, **kwargs)
        result = scorecard_score(
            features,
            min_ptps=settings.REPAYMENT_MIN_PTPS_FOR_HISTORY,
            min_visits=settings.REPAYMENT_MIN_VISITS_FOR_CONTACT,
        )
        cases = kwargs.get("cases") or ()
        case_id = next((c.id for c in cases), None)

        # Recovery potential, from the SAME feature dict. Two scorecards, one
        # point-in-time read of the loan — building the features twice is how the
        # two would eventually disagree about what "dpd as of Tuesday" means.
        #
        # `result` is deliberately not passed in. The recovery scorecard never
        # sees the repayment likelihood; see _FORBIDDEN_FEATURE_KEYS above.
        recovery = recovery_score(features)

        return ScoreOutcome(
            loan_id=loan.id,
            customer_id=loan.customer_id,
            case_id=case_id,
            as_of=as_of,
            likelihood=result["likelihood"],
            risk_score=result["risk_score"],
            band=result["band"],
            risk_category=result["risk_category"],
            evidence_coverage=result["evidence_coverage"],
            model_version=result["model_version"],
            source=SOURCE_SCORECARD,
            features=_jsonable(features),
            factors=result["factors"],
            recovery_potential=recovery["recovery_potential"],
            recovery_rate_30=recovery["recovery_rate_30"],
            recovery_rate_60=recovery["recovery_rate_60"],
            recovery_rate_90=recovery["recovery_rate_90"],
            recovery_speed_index=recovery["speed_index"],
            recovery_evidence_coverage=recovery["evidence_coverage"],
            recovery_model_version=recovery["model_version"],
            recovery_source=SOURCE_SCORECARD,
            recovery_factors=recovery["factors"],
            recovery_speed_reasons=recovery["speed_reasons"],
        )

    # ── Snapshot write policy ────────────────────────────────────────────────
    @staticmethod
    def should_snapshot(
        outcome: ScoreOutcome,
        previous: RepaymentSnapshot | None,
        *,
        state_changed: bool = False,
    ) -> bool:
        """Whether this score is worth a row.

        Scoring every loan every night would store ~191k near-identical rows a
        year, most of them recording that nothing happened. The rows that matter
        are the ones next to a change, plus a periodic anchor so a long flat
        stretch is still represented.
        """
        if previous is None:
            return True
        if state_changed:
            # The most valuable row in the table: the score as it stood
            # immediately before the thing that changed the account.
            return True

        # ── Recovery (2026-08-24) ────────────────────────────────────────────
        # NULL -> computed IS a change, and this clause is not a nicety: without
        # it the recovery label would never be persisted for a loan on a stable
        # book. Every row written before this feature shipped has NULL recovery
        # fields, as does every loan the nightly demo feed creates
        # (workers/tasks/demo_daily_feed.py). If the likelihood has not moved a
        # point, none of the clauses below fire, no row is written, and the
        # prediction is silently discarded — the feature would appear to work
        # while writing nothing at all.
        if outcome.has_recovery and previous.recovery_rate_90 is None:
            return True

        # A band move on the second score deserves a row for the same reason a
        # likelihood move does: the label a manager acted on has changed. Banded
        # rather than thresholded on the rate, because the label is what surfaces
        # and a three-bucket value moves far more slowly than a 0.1-precision
        # likelihood.
        if (outcome.recovery_potential is not None
                and outcome.recovery_potential != previous.recovery_potential):
            return True

        if abs(outcome.likelihood - previous.likelihood) >= settings.REPAYMENT_SNAPSHOT_MIN_DELTA:
            return True
        gap = (outcome.as_of - previous.as_of_date).days
        return gap >= settings.REPAYMENT_SNAPSHOT_ANCHOR_DAYS

    # ── Customer rollup ──────────────────────────────────────────────────────
    @staticmethod
    def rollup(outcomes: Iterable[ScoreOutcome]) -> ScoreOutcome | None:
        """A customer's score is their WORST loan.

        Averaging would let a small clean loan mask a large defaulted one, and
        the field agent is being sent to one person, not to a portfolio.
        """
        worst = None
        for o in outcomes:
            if worst is None or o.likelihood < worst.likelihood:
                worst = o
        return worst

    # ── Persistence ──────────────────────────────────────────────────────────
    def _existing(self, loan_ids: list[str]) -> dict[str, RepaymentSnapshot]:
        """Most recent snapshot per loan, for the delta/anchor decision."""
        if not self.db or not loan_ids:
            return {}
        rows = (
            self.db.query(RepaymentSnapshot)
            .filter(RepaymentSnapshot.loan_id.in_(loan_ids))
            .order_by(RepaymentSnapshot.loan_id,
                      RepaymentSnapshot.as_of_date.desc())
            .all()
        )
        latest: dict[str, RepaymentSnapshot] = {}
        for row in rows:
            latest.setdefault(row.loan_id, row)
        return latest

    def persist(
        self,
        outcome: ScoreOutcome,
        previous: RepaymentSnapshot | None,
        *,
        trigger: str = TRIGGER_NIGHTLY,
        is_backfill: bool = False,
    ) -> RepaymentSnapshot | None:
        """Write or update one snapshot. Returns None when nothing was written.

        A snapshot is immutable once its day has passed: rescoring a past date
        would replace evidence that was true then with evidence that is true
        now, which is the same corruption as a backfill but harder to spot.
        """
        if previous is not None and previous.as_of_date == outcome.as_of:
            if previous.scored_at and previous.scored_at.date() != outcome.as_of:
                logger.warning(
                    "repayment.snapshot.stale_rescore_refused",
                    loan_id=outcome.loan_id, as_of=str(outcome.as_of),
                    originally_scored_at=str(previous.scored_at),
                )
                return None
            previous.likelihood = outcome.likelihood
            previous.risk_score = outcome.risk_score
            previous.band = outcome.band
            previous.risk_category = RiskCategory(outcome.risk_category)
            previous.evidence_coverage = outcome.evidence_coverage
            previous.features = outcome.features
            previous.contributions = {"factors": outcome.factors}
            previous.model_version = outcome.model_version
            previous.source = outcome.source
            # Recovery, on the SAME row. Missing this branch and writing only the
            # insert below is a silent staleness bug: a same-day rescore would
            # refresh the likelihood and leave yesterday's recovery label sitting
            # beside it, and nothing would raise.
            #
            # Guarded on has_recovery so a caller that produced no recovery score
            # leaves the existing label alone rather than erasing it with None.
            if outcome.has_recovery:
                previous.recovery_potential = outcome.recovery_potential
                previous.recovery_rate_30 = outcome.recovery_rate_30
                previous.recovery_rate_60 = outcome.recovery_rate_60
                previous.recovery_rate_90 = outcome.recovery_rate_90
                previous.recovery_speed_index = outcome.recovery_speed_index
                previous.recovery_evidence_coverage = outcome.recovery_evidence_coverage
                previous.recovery_model_version = outcome.recovery_model_version
                previous.recovery_source = outcome.recovery_source
                previous.recovery_contributions = {
                    "factors": outcome.recovery_factors,
                    "speed_reasons": outcome.recovery_speed_reasons,
                }
            return previous

        row = RepaymentSnapshot(
            loan_id=outcome.loan_id,
            customer_id=outcome.customer_id,
            case_id=outcome.case_id,
            as_of_date=outcome.as_of,
            trigger=trigger,
            is_backfill=is_backfill,
            source=outcome.source,
            model_version=outcome.model_version,
            likelihood=outcome.likelihood,
            risk_score=outcome.risk_score,
            band=outcome.band,
            risk_category=RiskCategory(outcome.risk_category),
            evidence_coverage=outcome.evidence_coverage,
            features=outcome.features,
            contributions={"factors": outcome.factors},
            recovery_potential=outcome.recovery_potential,
            recovery_rate_30=outcome.recovery_rate_30,
            recovery_rate_60=outcome.recovery_rate_60,
            recovery_rate_90=outcome.recovery_rate_90,
            recovery_speed_index=outcome.recovery_speed_index,
            recovery_evidence_coverage=outcome.recovery_evidence_coverage,
            recovery_model_version=outcome.recovery_model_version,
            recovery_source=outcome.recovery_source,
            recovery_contributions=(
                {"factors": outcome.recovery_factors,
                 "speed_reasons": outcome.recovery_speed_reasons}
                if outcome.has_recovery else None
            ),
        )
        if self.db:
            self.db.add(row)
        return row

    # ── THE ONLY WRITE TO Customer.risk_score IN THE CODEBASE ────────────────
    def _apply_risk_score(self, customer: Customer, worst: ScoreOutcome) -> bool:
        """Write the rolled-up score onto the legacy column. Returns whether it
        wrote.

        This is the single site. `grep -rn "_apply_risk_score" backend/` finds
        every place Customer.risk_score can change, which is the property that
        seed_data.py:458 and ingest_daily.py:96 did not have — two writers, two
        formulas, silently drifted apart.

        risk_category is written in the SAME breath and never separately. They
        are one logical value; letting them diverge would put a green badge on a
        customer whose number says critical, and nothing would raise.
        """
        if not settings.REPAYMENT_WRITE_RISK_SCORE:
            return False
        customer.risk_score = worst.risk_score
        customer.risk_category = RiskCategory(worst.risk_category)
        return True

    def _apply_recovery_label(self, loan: Loan, outcome: ScoreOutcome) -> bool:
        """Write the computed label onto Loan.recovery_potential. Returns whether
        it wrote.

        THE SINGLE SITE. `grep -rn "_apply_recovery_label" backend/` finds every
        place that column can change. It had two writers before today, both
        random and separately weighted (seed_data.py:481-509 and
        scripts/add_recovery_potential.py), which disagreed with each other and
        with nothing reading either — the same shape of failure as the two
        risk_score writers above.

        GATED, and the gate is CLOSED by default. RECOVERY_WRITE_LABEL=False
        means the label is computed and snapshotted exactly as normal but the
        shared Loan column is left alone. The manager surface reads the snapshot,
        not this column, so the feature is fully reviewable with the gate shut —
        and opening it later is one line with nothing to undo.
        """
        if not settings.RECOVERY_WRITE_LABEL:
            return False
        if not outcome.has_recovery:
            return False
        computed = RecoveryPotential(outcome.recovery_potential)
        if loan.recovery_potential == computed:
            return False
        loan.recovery_potential = computed
        return True

    # ── Orchestration ────────────────────────────────────────────────────────
    def rescore(
        self,
        *,
        as_of: date | None = None,
        loan_ids: list[str] | None = None,
        trigger: str = TRIGGER_NIGHTLY,
        dry_run: bool = False,
        is_backfill: bool = False,
        changed_loan_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        """Score loans, roll up to customers, snapshot, and report.

        `dry_run=True` computes and reports everything and writes nothing — not
        the legacy column, not a snapshot. It is how the distribution shift gets
        reviewed BEFORE it lands, rather than discovered afterwards.

        Nothing here touches Case.priority. See the REPRICE block below.
        """
        from app.ml.repayment import log_resolution, resolved_scorer

        if self.db is None:
            raise RuntimeError("rescore needs a database session")

        as_of = as_of or date.today()
        resolution = resolved_scorer()
        log_resolution(resolution)

        loans, context = self._load(loan_ids)
        by_customer: dict[str, list[ScoreOutcome]] = {}
        before: list[float] = []
        after: list[float] = []
        snapshots_written = 0
        snapshots_skipped = 0
        recovery_labels_written = 0

        latest = self._existing([ln.id for ln in loans])

        for loan in loans:
            bundle = context.get(loan.id, {})
            outcome = self.score_loan(
                loan, loan.customer, as_of,
                cases=bundle.get("cases", ()), visits=bundle.get("visits", ()),
                ptps=bundle.get("ptps", ()), payments=bundle.get("payments", ()),
                is_backfill=is_backfill,
            )
            by_customer.setdefault(loan.customer_id, []).append(outcome)

            if dry_run:
                continue

            # The gate is checked inside, not here, so that a closed gate still
            # scores and still snapshots. That is the whole point of the split:
            # training data accrues from the day this ships, not from the day
            # someone opts in.
            if self._apply_recovery_label(loan, outcome):
                recovery_labels_written += 1

            state_changed = bool(changed_loan_ids and loan.id in changed_loan_ids)
            if self.should_snapshot(outcome, latest.get(loan.id),
                                    state_changed=state_changed):
                self.persist(outcome, latest.get(loan.id), trigger=trigger,
                             is_backfill=is_backfill)
                snapshots_written += 1
            else:
                snapshots_skipped += 1

        customers_written = 0
        for customer_id, outcomes in by_customer.items():
            worst = self.rollup(outcomes)
            customer = context.get("_customers", {}).get(customer_id)
            if worst is None or customer is None:
                continue
            before.append(float(customer.risk_score))
            after.append(worst.risk_score)
            if not dry_run and self._apply_risk_score(customer, worst):
                customers_written += 1

        # ── REPRICE: deliberately not implemented ────────────────────────────
        # Case.priority is written once at case creation (seed_data.py:1505,
        # ingest_daily.py:437) and has never been recomputed for an open case.
        # Doing so is NEW behaviour, not a like-for-like swap, so it is not part
        # of turning the scorer on. The flag is reported rather than silently
        # ignored — a switch that is on and does nothing is how this codebase
        # accumulated its dead features.
        reprice_requested = settings.REPAYMENT_REPRICE_OPEN_CASES
        if reprice_requested:
            logger.warning(
                "repayment.reprice.requested_but_not_implemented",
                detail="REPAYMENT_REPRICE_OPEN_CASES is true but repricing open "
                       "cases is not implemented; Case.priority is unchanged.",
            )

        result = {
            "as_of": as_of.isoformat(),
            "dry_run": dry_run,
            "scorer": resolution.source,
            "model_version": resolution.model_version,
            "scorer_status": resolution.status,
            "loans_scored": len(loans),
            "customers_affected": len(by_customer),
            "customers_written": customers_written,
            "snapshots_written": snapshots_written,
            "snapshots_skipped_no_change": snapshots_skipped,
            # Reported every run so "is the switch on?" is never a guess.
            "write_risk_score_enabled": settings.REPAYMENT_WRITE_RISK_SCORE,
            "recovery_scorer": settings.RECOVERY_SCORER,
            "write_recovery_label_enabled": settings.RECOVERY_WRITE_LABEL,
            "recovery_labels_written": recovery_labels_written,
            "reprice_open_cases_enabled": reprice_requested,
            "reprice_implemented": False,
            "case_priority_rows_touched": 0,
            "distribution": _distribution(before, after),
        }
        logger.info("repayment.rescore.complete", **{
            k: v for k, v in result.items() if k != "distribution"})
        return result

    # ── Labelling ────────────────────────────────────────────────────────────
    def attach_outcomes(self, as_of: date | None = None) -> dict[str, Any]:
        """Fill in what the borrower actually did, one horizon after the score.

        ingest_daily.py already labels from the bank's own `bank_action`, and
        that always wins — it is the bank's word, not our inference. This fills
        the rest in from our payment ledger.

        Only snapshots older than the horizon are touched: labelling a
        three-day-old score NO_PAYMENT would record "did not pay" about a
        borrower who simply has not had time to.
        """
        if self.db is None:
            raise RuntimeError("attach_outcomes needs a database session")

        as_of = as_of or date.today()
        horizon = settings.REPAYMENT_OUTCOME_HORIZON_DAYS
        cutoff = as_of - timedelta(days=horizon)

        due = (
            self.db.query(RepaymentSnapshot)
            .filter(RepaymentSnapshot.outcome.is_(None),
                    RepaymentSnapshot.as_of_date <= cutoff)
            .all()
        )
        if not due:
            return {"examined": 0, "labelled": 0, "by_outcome": {}}

        # One query for every payment that could matter, rather than one per row.
        case_ids = {r.case_id for r in due if r.case_id}
        loan_ids = {r.loan_id for r in due}
        cases_by_loan: dict[str, list[Case]] = {}
        for case in self.db.query(Case).filter(Case.loan_id.in_(loan_ids)).all():
            cases_by_loan.setdefault(case.loan_id, []).append(case)
            case_ids.add(case.id)

        payments_by_case: dict[str, list[Payment]] = {}
        if case_ids:
            for pay in self.db.query(Payment).filter(
                    Payment.case_id.in_(case_ids)).all():
                payments_by_case.setdefault(pay.case_id, []).append(pay)

        counts: dict[str, int] = {}
        labelled = 0
        for row in due:
            outcome, amount = self._infer_outcome(
                row, cases_by_loan.get(row.loan_id, []), payments_by_case)
            if outcome is None:
                continue
            row.outcome = outcome
            row.outcome_amount = amount
            row.outcome_source = OUTCOME_SOURCE_PAYMENT if amount else OUTCOME_SOURCE_INFERRED
            row.outcome_observed_at = as_of
            row.outcome_horizon_days = horizon
            row.feature_age_days = (as_of - row.as_of_date).days
            counts[outcome] = counts.get(outcome, 0) + 1
            labelled += 1

        logger.info("repayment.labeller.complete", examined=len(due),
                    labelled=labelled, by_outcome=counts)
        return {
            "examined": len(due), "labelled": labelled, "by_outcome": counts,
            # Nested rather than a second call site, so the nightly task and
            # every other caller pick the recovery pass up without being edited.
            "recovery": self.attach_recovery_outcomes(as_of=as_of),
        }

    def attach_recovery_outcomes(self, as_of: date | None = None) -> dict[str, Any]:
        """Fill in how much actually came back, at 30, 60 and 90 days.

        The repayment labeller above visits each row ONCE, at one horizon, and
        writes one outcome. This one must revisit: a row scored today cannot know
        its 90-day recovery until 90 days have passed, but its 30-day figure is
        knowable long before that. So rows carry `recovery_labelled_through_days`
        and are picked up again as each horizon matures.

        A row can also mature past several horizons between runs — a backlog, or
        a worker that was down for a fortnight — so every horizon that has come
        due is filled in one pass rather than one per night.

        CENSORED rows are still measured. The amount that arrived is a fact and
        recording it costs nothing; what keeps a bank recall out of the training
        set is `outcome`, which this pass never touches. Deliberately: a row must
        not become uncensored because money happened to arrive after the bank
        pulled the case.
        """
        if self.db is None:
            raise RuntimeError("attach_recovery_outcomes needs a database session")

        as_of = as_of or date.today()
        horizons = sorted(settings.RECOVERY_OUTCOME_HORIZONS)
        if not horizons:
            return {"examined": 0, "labelled": 0, "by_horizon": {}}
        final_horizon = horizons[-1]

        # Broad filter in SQL, exact filter in Python. The precise predicate is
        # `as_of_date <= today - (recovery_labelled_through_days + 30)`, which
        # needs date arithmetic against a COLUMN — expressible in Postgres, not
        # portably so on the SQLite the test suite builds the schema on. The
        # partial index does the narrowing either way; what reaches Python is a
        # small and shrinking set, and each row it labels leaves that index.
        #
        # recovery_rate_90 IS NOT NULL is load-bearing, not defensive: every row
        # written before this feature shipped has no recovery prediction, and
        # retro-labelling those would attach outcomes to a score nobody made.
        due = (
            self.db.query(RepaymentSnapshot)
            .filter(
                RepaymentSnapshot.recovery_rate_90.is_not(None),
                RepaymentSnapshot.recovery_labelled_through_days < final_horizon,
                RepaymentSnapshot.as_of_date <= as_of - timedelta(days=horizons[0]),
            )
            .all()
        )
        if not due:
            return {"examined": 0, "labelled": 0, "by_horizon": {}}

        # One query for every payment that could matter, rather than one per row —
        # same shape as attach_outcomes above.
        loan_ids = {r.loan_id for r in due}
        case_ids: set[str] = {r.case_id for r in due if r.case_id}
        cases_by_loan: dict[str, list[Case]] = {}
        for case in self.db.query(Case).filter(Case.loan_id.in_(loan_ids)).all():
            cases_by_loan.setdefault(case.loan_id, []).append(case)
            case_ids.add(case.id)

        payments_by_case: dict[str, list[Payment]] = {}
        if case_ids:
            for pay in self.db.query(Payment).filter(
                    Payment.case_id.in_(case_ids)).all():
                payments_by_case.setdefault(pay.case_id, []).append(pay)

        by_horizon: dict[int, int] = {}
        labelled = 0
        unobservable_pending = 0   # no case yet, horizons still open — retry later
        unobservable_closed = 0    # no case and every horizon matured — give up
        for row in due:
            todo = self._recovery_horizons_due(row, as_of, horizons)
            if not todo:
                continue

            cases = cases_by_loan.get(row.loan_id, [])

            # ── UNOBSERVABLE, NOT ZERO (2026-08-24) ──────────────────────────
            # Payment.case_id is NOT NULL, so money is reachable only through a
            # case. A loan with no case cannot show a recovery however much the
            # borrower paid — the ledger has nowhere to record it.
            #
            # Writing 0.0 for those would be a lie a model would happily learn:
            # "no case" would become a strong predictor of "recovers nothing",
            # and on the 2026-08-24 cohort it would bias LOW hardest (50 of the
            # 115 caseless loans are LOW, against 26 HIGH), flattering the
            # scorecard's apparent separation.
            #
            # So the amounts stay NULL and the row is RETRIED, because a case can
            # be allocated later and make the remaining horizons observable. Once
            # every horizon has matured with still no case, the marker is
            # advanced so the row leaves the scan index rather than being
            # re-examined nightly forever.
            #
            # The three states are then distinguishable without a new column:
            #   amount NULL, marker <  horizon -> not yet matured
            #   amount NULL, marker >= horizon -> matured but UNOBSERVABLE
            #   amount 0.0                     -> observed, nothing arrived
            if not cases:
                if row.as_of_date + timedelta(days=final_horizon) <= as_of:
                    row.recovery_labelled_through_days = final_horizon
                    unobservable_closed += 1
                else:
                    unobservable_pending += 1
                continue

            for horizon in todo:
                received = self._received_within(
                    row.as_of_date, horizon, cases, payments_by_case)
                setattr(row, f"recovered_amount_{horizon}", received)
                by_horizon[horizon] = by_horizon.get(horizon, 0) + 1

            row.recovery_labelled_through_days = max(todo)
            labelled += 1

        logger.info("recovery.labeller.complete", examined=len(due),
                    labelled=labelled, by_horizon=by_horizon,
                    unobservable_pending=unobservable_pending,
                    unobservable_closed=unobservable_closed)
        return {
            "examined": len(due), "labelled": labelled, "by_horizon": by_horizon,
            # Reported, never folded into the labelled count. A caseless loan is
            # a gap in what the ledger can see, and a validation run that cannot
            # tell it apart from a genuine zero will overstate the scorecard.
            "unobservable_pending": unobservable_pending,
            "unobservable_closed": unobservable_closed,
        }

    @staticmethod
    def _recovery_horizons_due(row, as_of: date, horizons: list[int]) -> list[int]:
        """Which horizons this row can now be labelled for, oldest first.

        A pure decision, separated out so it is testable without a database —
        same convention as _infer_outcome above.

        Returns EVERY matured horizon not yet done, not just the next one. A
        worker that was down for a fortnight, or a row scored during a backlog,
        can cross two boundaries between runs; labelling one per night would then
        take three more nights to catch up and would leave the row sitting in the
        scan index the whole time.
        """
        done_through = row.recovery_labelled_through_days or 0
        return [h for h in horizons
                if h > done_through and row.as_of_date + timedelta(days=h) <= as_of]

    @staticmethod
    def _received_within(start: date, horizon_days: int, cases: list[Case],
                         payments_by_case: dict[str, list[Payment]]) -> float:
        """Money received in the window AFTER the score, never before.

        The question is what came back once the prediction was made, not what had
        already been collected — counting the latter would score the scorecard on
        history it was handed rather than on anything it foresaw.

        The window is half-open, `(start, start + horizon]`, matching
        _infer_outcome: a payment on the scoring date itself was already known
        when the score was computed.

        ONLY VERIFIED MONEY COUNTS — see _RECOVERED_PAYMENT_STATUSES. Until
        2026-08-24 this summed every payment regardless of status, so a REJECTED
        or REVERSED receipt would have been recorded as money recovered. Nothing
        in the ledger had either status at the time, so the defect was latent
        rather than biting, but it would have corrupted the first real reversal
        into a training label that says the borrower paid.

        Status is read as the CURRENT value, and that is correct here in a way it
        would not be in build_features. There, reading current status leaks the
        future backwards into a feature. Here the question is asked after the
        horizon has closed and the answer wanted is what finally happened.
        """
        end = start + timedelta(days=horizon_days)
        received = 0.0
        for case in cases:
            for pay in payments_by_case.get(case.id, []):
                # Tolerates the enum or its plain value, so a stand-in in a test
                # and an ORM row behave identically.
                status = getattr(pay.status, "value", pay.status)
                if status not in _RECOVERED_PAYMENT_STATUSES:
                    continue
                if pay.payment_date and start < pay.payment_date.date() <= end:
                    received += float(pay.amount or 0.0)
        return round(received, 2)

    @staticmethod
    def _infer_outcome(row: RepaymentSnapshot, cases: list[Case],
                       payments_by_case: dict[str, list[Payment]]):
        """(outcome, amount) for one snapshot, or (None, None) to leave it alone.

        Payments are counted in the window AFTER the score, never before — the
        question is what the borrower did next, not what they had already done.
        """
        start = row.as_of_date
        end = start + timedelta(days=settings.REPAYMENT_OUTCOME_HORIZON_DAYS)

        target = 0.0
        received = 0.0
        for case in cases:
            target += float(case.target_amount or 0.0)
            for pay in payments_by_case.get(case.id, []):
                if pay.payment_date and start < pay.payment_date.date() <= end:
                    received += float(pay.amount or 0.0)

        if received > 0:
            if target > 0 and received >= target * settings.REPAYMENT_FULL_RATIO:
                return OUTCOME_REPAID, round(received, 2)
            return OUTCOME_PARTIAL, round(received, 2)

        # Nothing received. Distinguish "chose not to pay" from "the case was
        # taken off the table for a reason that is not the borrower's doing" —
        # the second is CENSORED and must never train as a negative.
        if cases and all(c.status in _CENSORING_STATUSES for c in cases):
            return OUTCOME_CENSORED, None
        return OUTCOME_NO_PAYMENT, None

    def prune_snapshots(self, as_of: date | None = None) -> dict[str, Any]:
        """Age out UNLABELLED snapshots. Labelled rows are never pruned at any
        age — they are the training set, which is the entire point of the table.
        """
        if self.db is None:
            raise RuntimeError("prune_snapshots needs a database session")
        as_of = as_of or date.today()
        cutoff = as_of - timedelta(days=settings.REPAYMENT_SNAPSHOT_RETENTION_DAYS)
        deleted = (
            self.db.query(RepaymentSnapshot)
            .filter(RepaymentSnapshot.outcome.is_(None),
                    RepaymentSnapshot.as_of_date < cutoff)
            .delete(synchronize_session=False)
        )
        logger.info("repayment.snapshots.pruned", deleted=deleted,
                    cutoff=str(cutoff))
        return {"deleted": deleted, "cutoff": cutoff.isoformat()}

    # ── Bulk loading ─────────────────────────────────────────────────────────
    def _load(self, loan_ids: list[str] | None):
        """Loans plus their behavioural history, in a fixed number of queries.

        Per-loan queries over 525 loans would be 2,100 round trips inside the
        19:45 window between ingest and allocation.
        """
        from sqlalchemy.orm import joinedload

        query = self.db.query(Loan).options(joinedload(Loan.customer))
        if loan_ids:
            query = query.filter(Loan.id.in_(loan_ids))
        loans = query.all()
        ids = [ln.id for ln in loans]

        cases = (self.db.query(Case).filter(Case.loan_id.in_(ids)).all()
                 if ids else [])
        case_to_loan = {c.id: c.loan_id for c in cases}
        case_ids = list(case_to_loan)

        def bucket(rows):
            out: dict[str, list] = {}
            for row in rows:
                loan_id = case_to_loan.get(row.case_id)
                if loan_id:
                    out.setdefault(loan_id, []).append(row)
            return out

        visits = bucket(self.db.query(Visit).filter(
            Visit.case_id.in_(case_ids)).all() if case_ids else [])
        ptps = bucket(self.db.query(PTP).filter(
            PTP.case_id.in_(case_ids)).all() if case_ids else [])
        payments = bucket(self.db.query(Payment).filter(
            Payment.case_id.in_(case_ids)).all() if case_ids else [])

        context: dict[str, Any] = {}
        for case in cases:
            context.setdefault(case.loan_id, {}).setdefault("cases", []).append(case)
        for loan_id in ids:
            entry = context.setdefault(loan_id, {})
            entry["visits"] = visits.get(loan_id, [])
            entry["ptps"] = ptps.get(loan_id, [])
            entry["payments"] = payments.get(loan_id, [])
        context["_customers"] = {
            ln.customer_id: ln.customer for ln in loans if ln.customer
        }
        return loans, context

    # ── Reporting ────────────────────────────────────────────────────────────
    @staticmethod
    def to_payload(outcome: ScoreOutcome) -> dict[str, Any]:
        """The shape an API hands out. Mirrors fraud_service's finding dict.

        `is_modelled` and `is_confident` are included rather than left for the
        caller to derive, so a UI cannot accidentally present a thin scorecard
        number as a confident modelled one.
        """
        return {
            "likelihood": outcome.likelihood,
            "risk_score": outcome.risk_score,
            "band": outcome.band,
            "risk_category": outcome.risk_category,
            "source": outcome.source,
            "model_version": outcome.model_version,
            "is_modelled": outcome.is_modelled,
            "is_confident": outcome.is_confident,
            "evidence_coverage": outcome.evidence_coverage,
            "as_of": outcome.as_of.isoformat(),
            "factors": outcome.factors,
        }


def _distribution(before: list[float], after: list[float]) -> dict[str, Any]:
    """Before/after shape of Customer.risk_score, for review before enabling.

    Reported on every run, dry or not, so the shift is a number an operator can
    read rather than something inferred from the customers table afterwards.
    """
    from app.ml.repayment_scorecard import risk_category_for

    def summarise(values: list[float]) -> dict[str, Any]:
        if not values:
            return {"n": 0}
        ordered = sorted(values)
        n = len(ordered)
        bands: dict[str, int] = {}
        for value in ordered:
            band = risk_category_for(value)
            bands[band] = bands.get(band, 0) + 1
        return {
            "n": n,
            "min": round(ordered[0], 1),
            "p25": round(ordered[n // 4], 1),
            "median": round(ordered[n // 2], 1),
            "p75": round(ordered[(3 * n) // 4], 1),
            "max": round(ordered[-1], 1),
            "mean": round(sum(ordered) / n, 1),
            "bands": bands,
        }

    moved = sum(1 for b, a in zip(before, after)
                if risk_category_for(b) != risk_category_for(a))
    return {
        "before": summarise(before),
        "after": summarise(after),
        "risk_category_changed": moved,
        "risk_category_changed_pct": (
            round(moved / len(before) * 100, 1) if before else 0.0),
    }


def _jsonable(features: dict[str, Any]) -> dict[str, Any]:
    """Enums and dates are not JSON. Converted here rather than at the column,
    so what is stored is exactly what a training pull will read back."""
    out: dict[str, Any] = {}
    for key, value in features.items():
        if hasattr(value, "value"):          # enum
            out[key] = value.value
        elif isinstance(value, date):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


__all__ = [
    "RepaymentService", "ScoreOutcome",
    "_FORBIDDEN_FEATURE_KEYS", "SCORECARD_VERSION", "risk_category_for",
]
