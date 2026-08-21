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

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Iterable

import structlog

from app.core.config import settings
from app.ml.repayment_scorecard import (
    SCORECARD_VERSION, risk_category_for, score as scorecard_score,
)
from app.models.case import Case, CaseStatus
from app.models.customer import Customer, RiskCategory
from app.models.loan import Loan
from app.models.payment import Payment
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
# bank_risk_score and recovery_potential are here because in seeded data they
# are themselves functions of dpd and cibil (seed_data.py:1398, :470-498), so
# including them triple-counts delinquency; on real data they are a partner's
# model output and belong to a later tier, never to this scorecard.
_FORBIDDEN_FEATURE_KEYS = frozenset({
    "risk_score", "risk_category", "collection_priority_score",
    "bank_risk_score", "recovery_potential", "priority", "allocation_score",
})


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
        return {"examined": len(due), "labelled": labelled, "by_outcome": counts}

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
