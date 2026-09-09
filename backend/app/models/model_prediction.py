# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. The feedback loop's spine: one append-only row per served
#   model score, with the features it was computed from and, later, the outcome
#   that actually happened.
#
#   THIS IS RepaymentSnapshot'S SHAPE, GENERALISED. That table already does
#   exactly this for one hand-weighted scorecard — frozen point-in-time
#   features, the score, and an outcome the labeller fills in afterwards — and
#   the reasoning in models/repayment_snapshot.py applies here unchanged. Rather
#   than invent a second discipline, this is the same one made model-agnostic so
#   every future model inherits it.
#
#   WITHOUT THIS TABLE THE LOOP IS NOT CLOSED. A model that scores and is never
#   compared against what happened cannot be monitored, cannot be retrained on
#   its own errors, and cannot be shown to have decayed. CLAUDE.md's feature #20
#   records that the machinery existed but the loop was open; this is the
#   missing half.
# ───────────────────────────────────────────────────────────────────────────
"""
Served model predictions, and the outcomes they are eventually judged against.

APPEND-ONLY. A prediction is a historical fact: it was made, at a time, from
particular inputs, by a particular model version. Editing one destroys the only
record of what the system actually did. Only `actual_outcome` and its timestamp
are ever written after insert, and only by the labeller.

WHY THE FEATURE VECTOR IS STORED, not just the score. Two reasons, both learned
the hard way elsewhere in this repo: a score without its inputs cannot be
explained after the fact, and drift monitoring needs the INPUT distribution, not
the output one — a model can hold its score distribution steady while the
population underneath it moves.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey


class ModelPrediction(Base, UUIDPrimaryKey):
    """One score, served. Immutable except for the outcome the labeller adds."""

    __tablename__ = "model_predictions"

    # ── which model said it ─────────────────────────────────────────────────
    model_name: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    # The exact artifact version. A prediction made under 1.0.0 and one made
    # under 1.1.0 answer different questions, and a monitor that pools them is
    # measuring two models at once — the same trap the scorecards' *_VERSION
    # stamps exist to avoid.
    model_version: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ── what it was about ───────────────────────────────────────────────────
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)   # loan | case | visit
    entity_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    loan_id: Mapped[str | None] = mapped_column(
        ForeignKey("loans.id", ondelete="SET NULL"), nullable=True, index=True)
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("cases.id", ondelete="SET NULL"), nullable=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True)

    # ── when, and as of when ────────────────────────────────────────────────
    # scored_at is the wall clock; as_of_date is the date the FEATURES describe.
    # They are usually the same day and must not be assumed to be: a backfill
    # writes rows whose as_of is months before scored_at, and pooling those with
    # live rows is how a monitor reports a drift that is really a backfill.
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    # ── what it said ────────────────────────────────────────────────────────
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    band: Mapped[str | None] = mapped_column(String(4), nullable=True, index=True)
    # False when the engine declined — no artifact, or too few features. Stored
    # rather than inferred from a null probability, because "the model said
    # nothing" and "the model said 0.0" must never be the same row.
    is_modelled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fallback_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── what it saw ─────────────────────────────────────────────────────────
    features: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    feature_coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason_codes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # ── the frozen basis for the LABEL, not for the score ───────────────────
    # `overdue_amount` and `emi_amount` as they stood when the prediction was
    # made. Separate from `features` on purpose: features are the model's
    # INPUTS and are what PSI is computed on, whereas this is what the OUTCOME
    # is measured against. Merging them would hide one inside the other, which
    # is the duplicate-logic trap this whole module exists to avoid.
    #
    # Frozen because both columns are overwritten in place on Loan: reading
    # them at labelling time would compare a payment window against a balance
    # those very payments already reduced.
    outcome_baseline: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Which rule produced `actual_outcome`. Rows labelled under two definitions
    # answer different questions; a model trained across both without filtering
    # learns two targets at once.
    outcome_definition_version: Mapped[str | None] = mapped_column(
        String(40), nullable=True)

    # The terminal state, including the ones that produce NO label: censored,
    # no baseline, no case. NULL here means "not yet evaluated".
    outcome_status: Mapped[str | None] = mapped_column(
        String(30), nullable=True, index=True)

    # ── what happened, filled in later ──────────────────────────────────────
    # 1 = the risk event occurred (no material payment / not contacted), matching
    # the y = 1 convention fixed in ml/pipeline/config.py. NULL means not yet
    # matured, which is NOT the same as 0 and must never be counted as one.
    actual_outcome: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    outcome_attached_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    outcome_horizon_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── the SAME row under the other labeller, for comparison only ──────────
    # 2026-09-08. What `RepaymentService._infer_outcome` would have said about
    # this prediction, over the same as_of_date and horizon, plus the measured
    # payment sums the disagreement attribution was derived from. Written by
    # ml/pipeline/label_comparison.py.
    #
    # DELIBERATELY NOT `actual_outcome`. Two labellers disagreeing is a fact
    # worth measuring before choosing between them; overwriting the label the
    # model was validated against would change the target silently, and the
    # measurement would then be of a model that no longer exists. Nothing reads
    # this column — no allocator path, no monitor metric, no training pull.
    label_comparison: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        # The monitor's own query: this model, this version, matured rows only.
        Index("ix_model_pred_model_version_asof", "model_name", "model_version",
              "as_of_date"),
        # The labeller's query: what is due for an outcome.
        Index("ix_model_pred_pending_outcome", "model_name", "actual_outcome",
              "as_of_date"),
    )
