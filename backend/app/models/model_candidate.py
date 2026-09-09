# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-09 — NEW. The retraining lifecycle's only durable state.
#
#   BEFORE THIS TABLE, `retrain_recommended` was a boolean in a dict that a
#   Celery task returned into a log line and a result backend nothing reads.
#   The 2026-09-09 audit traced it: monitoring fires, and the chain stops.
#
#   A candidate is the unit of that chain. It exists from the moment monitoring
#   asks for a retrain to the moment a person accepts or rejects the result, and
#   it carries every decision made about it in between so the question "why is
#   this model live" has a row rather than an anecdote.
#
#   THE STATE MACHINE IS THE SAFETY PROPERTY. Promotion reads `state`, and only
#   APPROVED is promotable. Nothing else in the system may write `champion.txt`
#   — see registry.promote, which refuses without an approved candidate id.
# ───────────────────────────────────────────────────────────────────────────
"""One retraining attempt, from trigger to verdict.

Every transition is recorded in `state_history` with a timestamp and a reason,
because the interesting question about a rejected model is never "was it
rejected" but "on which gate, against which incumbent, on what data".
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, Enum as SAEnum, Float, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class CandidateState(str, enum.Enum):
    """Where a retraining attempt has got to.

    ONLY `APPROVED` IS PROMOTABLE, and only a human puts a candidate there.
    Every other state is either in flight or terminal-and-inert.
    """

    # In flight.
    TRAINING = "TRAINING"
    VALIDATING = "VALIDATING"
    COMPARING = "COMPARING"

    # Waiting on a person. The champion is unchanged while a candidate sits here.
    PENDING_APPROVAL = "PENDING_APPROVAL"

    # Terminal, accepted.
    APPROVED = "APPROVED"
    PROMOTED = "PROMOTED"

    # Terminal, inert. Each is a different question and they are not merged:
    # "the data was not there", "the model was not good enough", "it was not
    # better than what we already run" and "the machinery broke" call for four
    # different responses.
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    REJECTED_VALIDATION = "REJECTED_VALIDATION"
    REJECTED_COMPARISON = "REJECTED_COMPARISON"
    REJECTED_BY_HUMAN = "REJECTED_BY_HUMAN"
    FAILED = "FAILED"


#: States from which no further automated work happens and the candidate can
#: never reach production. A promoted candidate is terminal too, but it is the
#: one terminal state that IS production.
INERT_STATES = {
    CandidateState.INSUFFICIENT_DATA,
    CandidateState.REJECTED_VALIDATION,
    CandidateState.REJECTED_COMPARISON,
    CandidateState.REJECTED_BY_HUMAN,
    CandidateState.FAILED,
}

#: A retrain trigger must not start a second candidate while one of these is
#: outstanding — that is the idempotency rule, and it is enforced in code
#: (lifecycle.start_retraining) as well as by the unique constraint below.
ACTIVE_STATES = {
    CandidateState.TRAINING,
    CandidateState.VALIDATING,
    CandidateState.COMPARING,
    CandidateState.PENDING_APPROVAL,
    CandidateState.APPROVED,
}


class ModelCandidate(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "model_candidates"

    model_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    #: The version string the artifact is written under. Never "champion", and
    #: never equal to a version already on disk — lifecycle mints it from the
    #: incumbent version plus the trigger timestamp so two candidates cannot
    #: collide on an artifact directory.
    candidate_version: Mapped[str | None] = mapped_column(String(50), nullable=True,
                                                          index=True)

    #: The version that was live WHEN THIS CANDIDATE WAS EVALUATED. Promotion
    #: re-checks it: if the champion moved in between, the comparison this
    #: candidate passed was against a model that is no longer running, and the
    #: approval is stale.
    incumbent_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    state: Mapped[CandidateState] = mapped_column(
        SAEnum(CandidateState, name="model_candidate_state_enum"),
        nullable=False, index=True, default=CandidateState.TRAINING)

    # ── what caused it ──────────────────────────────────────────────────────
    #: Deterministic id of the MONITORING EVENT that asked for this retrain, not
    #: of the run that happened to observe it. Two nightly runs over the same
    #: matured cohort produce the same id, so the UNIQUE constraint below turns
    #: a duplicate trigger into a no-op instead of a second training job.
    monitoring_run_id: Mapped[str] = mapped_column(String(80), nullable=False,
                                                   index=True)
    trigger_reasons: Mapped[list | None] = mapped_column(JSON, nullable=True)
    monitoring_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ── the data it was trained on ──────────────────────────────────────────
    #: Row count, date bounds, class balance, outcome definition version, the
    #: horizon, and the prediction ids' hash — everything needed to rebuild the
    #: exact cohort or to prove two runs used different data.
    training_cohort: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cohort_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome_definition_version: Mapped[str | None] = mapped_column(String(50),
                                                                   nullable=True)

    # ── what was produced and what was decided ──────────────────────────────
    feature_set: Mapped[list | None] = mapped_column(JSON, nullable=True)
    code_versions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    training_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    validation_run_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    #: Every gate row as the training pipeline produced it: gate, observed,
    #: threshold, result. Stored whole — a summary would lose the one line
    #: somebody will want.
    gate_results: Mapped[list | None] = mapped_column(JSON, nullable=True)
    gates_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    comparison_run_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    comparison_results: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    comparison_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: Signed: challenger minus incumbent, on the shared out-of-time frame.
    gini_uplift: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── the human decision ──────────────────────────────────────────────────
    decided_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"),
                                                      nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                        nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)
    #: What the pointer said immediately before promotion. This is the rollback
    #: target, recorded at the moment it stops being true.
    promoted_from_version: Mapped[str | None] = mapped_column(String(50),
                                                              nullable=True)

    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: [{at, from, to, reason}] — appended on every transition, never rewritten.
    state_history: Mapped[list | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        # ONE CANDIDATE PER MONITORING EVENT PER MODEL. This is the idempotency
        # guarantee at the storage layer, so a race between two workers ends in
        # an IntegrityError rather than two training runs.
        UniqueConstraint("model_name", "monitoring_run_id",
                         name="uq_candidate_monitoring_event"),
        Index("ix_candidate_model_state", "model_name", "state"),
    )

    # ── transitions ─────────────────────────────────────────────────────────
    def transition(self, to: CandidateState, reason: str = "") -> None:
        """Move state and record it. The history is append-only on purpose."""
        history = list(self.state_history or [])
        history.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "from": self.state.value if isinstance(self.state, CandidateState)
                    else str(self.state),
            "to": to.value,
            "reason": reason,
        })
        self.state_history = history
        self.state = to

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def is_promotable(self) -> bool:
        """The single predicate promotion consults. Deliberately narrow: an
        APPROVED state is not enough on its own — the gates and the incumbent
        comparison must both have been recorded as passing, so a row hand-edited
        into APPROVED still cannot reach production."""
        return (self.state == CandidateState.APPROVED
                and self.gates_passed is True
                and self.comparison_passed is True
                and bool(self.candidate_version))

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.id,
            "model_name": self.model_name,
            "candidate_version": self.candidate_version,
            "incumbent_version": self.incumbent_version,
            "state": self.state.value if isinstance(self.state, CandidateState)
                     else str(self.state),
            "monitoring_run_id": self.monitoring_run_id,
            "trigger_reasons": self.trigger_reasons or [],
            "training_cohort": self.training_cohort,
            "cohort_rows": self.cohort_rows,
            "outcome_definition_version": self.outcome_definition_version,
            "feature_set": self.feature_set,
            "code_versions": self.code_versions,
            "training_metrics": self.training_metrics,
            "validation_run_id": self.validation_run_id,
            "gate_results": self.gate_results,
            "gates_passed": self.gates_passed,
            "comparison_run_id": self.comparison_run_id,
            "comparison_results": self.comparison_results,
            "comparison_passed": self.comparison_passed,
            "gini_uplift": self.gini_uplift,
            "decided_by_id": self.decided_by_id,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "decision_note": self.decision_note,
            "promoted_at": self.promoted_at.isoformat() if self.promoted_at else None,
            "promoted_from_version": self.promoted_from_version,
            "rejection_reason": self.rejection_reason,
            "error": self.error,
            "state_history": self.state_history or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
