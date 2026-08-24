# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-21 — New file. This table is the reason the repayment score can ever
#   become a trained model rather than staying a scorecard forever.
#
#   The blocker was never the features, it was the LABEL. PTP.status is drawn
#   from fixed weights with no borrower conditioning (seed_data.py:1644-1650),
#   and every seeded Payment has payment_date == visit_date, so "repaid after a
#   visit" does not exist as an event anywhere in the data. No amount of feature
#   engineering fixes that.
#
#   What DOES arrive, every evening, is the truth: ingest_daily.py reads a
#   bank_action column carrying PAID_DIRECT, SETTLED, WRITTEN_OFF, RECALL and
#   DECEASED — and today it applies the consequence and discards the fact. Each
#   row here is a prediction made BEFORE that truth landed, so the two can be
#   joined. Operating the scorecard produces the training set as a by-product,
#   the same way FraudReview turns a manager's confirm/dismiss into labelled
#   examples (models/fraud_review.py:6-10).
#
#   Follows the storage rule this codebase already settled on: computed things
#   are recomputed, not stored. The exception is deliberate and narrow — a
#   snapshot is a POINT-IN-TIME record, and the inputs it was scored on cannot
#   be recomputed later because Loan.dpd and PTP.status are both overwritten in
#   place with no history. Recomputing them tomorrow would silently answer a
#   different question. So the features are frozen here, or they are lost.
# ───────────────────────────────────────────────────────────────────────────
"""One score, the inputs behind it, and — later — what actually happened."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Enum as SAEnum, Float, ForeignKey, Index,
    Integer, String, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey
from app.models.customer import RiskCategory

# ── What caused this row to be written ───────────────────────────────────────
TRIGGER_NIGHTLY = "NIGHTLY"
TRIGGER_INGEST = "INGEST"
TRIGGER_SEED = "SEED"
TRIGGER_MANUAL = "MANUAL"

# ── Which tier produced the number ───────────────────────────────────────────
SOURCE_SCORECARD = "SCORECARD"
SOURCE_COMMAND_CENTER = "COMMAND_CENTER"
SOURCE_MODEL = "MODEL"

# ── Outcomes ─────────────────────────────────────────────────────────────────
# Deliberately plain strings rather than a Postgres enum, matching
# FraudReview.finding_type. The bank's action vocabulary belongs to the bank and
# will grow; adding a value to a Postgres enum needs a migration and can never
# be removed, which is a poor trade for a column whose domain is not ours.
OUTCOME_REPAID = "REPAID"            # paid in full within the horizon
OUTCOME_PARTIAL = "PARTIAL"          # paid something, short of the target
OUTCOME_NO_PAYMENT = "NO_PAYMENT"    # horizon elapsed, nothing received

# CENSORED outcomes. These are NOT negatives, and the training pull excludes
# them. A bank recall or an approved settlement is the bank's own administrative
# decision; labelling it "the borrower failed to pay" would teach a model that
# the bank's choice is the borrower's fault, and it would then underrate exactly
# the accounts that were pulled back for good reasons.
OUTCOME_SETTLED = "SETTLED"
OUTCOME_WRITTEN_OFF = "WRITTEN_OFF"
OUTCOME_RECALLED = "RECALLED"
OUTCOME_DECEASED = "DECEASED"
OUTCOME_CENSORED = "CENSORED"        # case closed for a non-payment reason

POSITIVE_OUTCOMES = frozenset({OUTCOME_REPAID, OUTCOME_PARTIAL})
CENSORED_OUTCOMES = frozenset({
    OUTCOME_SETTLED, OUTCOME_WRITTEN_OFF, OUTCOME_RECALLED,
    OUTCOME_DECEASED, OUTCOME_CENSORED,
})

# JSONB on Postgres: this is the training-set table and its features will be
# filtered on, so indexing and not reparsing on every read both matter.
#
# The .with_variant fallback is NOT cosmetic. tests/test_otp_service.py builds
# the whole schema on SQLite via create_all, so a Postgres-only type anywhere
# under app/models breaks fourteen unrelated tests with "SQLiteTypeCompiler
# object has no attribute visit_JSONB". Any JSON column added here needs the
# same treatment.
_JSON_DOC = JSON().with_variant(JSONB, "postgresql")

# Where the outcome came from, in precedence order — the bank's own word beats
# anything we infer from our payment ledger.
OUTCOME_SOURCE_BANK_ACTION = "BANK_ACTION"
OUTCOME_SOURCE_PAYMENT = "PAYMENT"
OUTCOME_SOURCE_INFERRED = "INFERRED"


class RepaymentSnapshot(Base, UUIDPrimaryKey):
    """A repayment likelihood as it stood on one day, for one loan."""
    __tablename__ = "repayment_score_snapshots"

    # ── Grain ────────────────────────────────────────────────────────────────
    # NOT NULL is load-bearing, not defensive. Postgres treats NULLs as distinct
    # inside a UNIQUE constraint, so a nullable loan_id would let duplicate
    # customer-level rows through the uq_ below without a word.
    loan_id: Mapped[str] = mapped_column(
        ForeignKey("loans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalised so the worst-loan customer rollup does not join back through
    # loans on every read — same reasoning as FraudReview.agent_id.
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("cases.id", ondelete="SET NULL"), nullable=True
    )

    # The point-in-time key. Every feature in `features` was computed with a
    # <= as_of_date filter; nothing after this date may have influenced it.
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)

    # Loan.dpd and PTP.status are overwritten daily with no history, so a
    # backfilled row leaks the future through both and cannot be made honest.
    # Backfills are permitted but marked, and the documented training pull
    # excludes them by default.
    is_backfill: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # ── The score ────────────────────────────────────────────────────────────
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)

    likelihood: Mapped[float] = mapped_column(Float, nullable=False)
    # Stored rather than derived so a query never has to know the direction
    # convention. risk_score == 100 - likelihood, always.
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    band: Mapped[str] = mapped_column(String(20), nullable=False)
    risk_category: Mapped[RiskCategory] = mapped_column(
        SAEnum(RiskCategory, name="risk_category_enum"), nullable=False
    )
    # Share of the scorecard's total weight that had any evidence behind it.
    # Carried so a modeller can drop thin rows rather than assume they are solid.
    evidence_coverage: Mapped[float] = mapped_column(Float, nullable=False)

    features: Mapped[dict] = mapped_column(_JSON_DOC, nullable=False)
    contributions: Mapped[dict] = mapped_column(_JSON_DOC, nullable=False)

    # ── What actually happened. All nullable — written by the labeller, one
    # outcome horizon later. NULL means "not yet known", never "nothing happened".
    outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    outcome_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    outcome_observed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    outcome_horizon_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # as_of_date -> label anchor gap, so freshness can be filtered on rather
    # than assumed. A 7-day anchor row and a same-day row are not equivalent.
    feature_age_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Recovery potential (2026-08-24) ──────────────────────────────────────
    # A SECOND score on the same row rather than a second table. The grain is
    # identical — one loan, one day — as is the point-in-time discipline, the
    # nightly scan and the retention rule. A parallel table would have duplicated
    # the JSON variant fallback, the censoring vocabulary and every index below,
    # and then drifted from them, which is the failure mode this codebase's
    # changelogs are mostly about.
    #
    # All nullable. Rows written before this shipped have no recovery prediction,
    # and that must stay distinguishable from "predicted, and the answer was
    # zero" — see the IS NOT NULL guard in attach_recovery_outcomes.
    #
    # recovery_potential is a plain String, not the RecoveryPotential enum, for
    # the same reason `outcome` above is: adding a value to a Postgres enum needs
    # a migration and can never be removed. The scorecard's band vocabulary is
    # ours and may grow.
    recovery_potential: Mapped[str | None] = mapped_column(String(8), nullable=True)
    recovery_rate_30: Mapped[float | None] = mapped_column(Float, nullable=True)
    recovery_rate_60: Mapped[float | None] = mapped_column(Float, nullable=True)
    recovery_rate_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    # How fast, as distinct from how much. Kept because the 30/60 rates are
    # derived from it and a modeller reading this row should not have to
    # re-derive the ramp to know why they differ from the 90.
    recovery_speed_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    recovery_evidence_coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    recovery_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    recovery_model_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    recovery_contributions: Mapped[dict | None] = mapped_column(_JSON_DOC, nullable=True)

    # ── What actually came back. Written by the labeller, one horizon at a time.
    #
    # NULL NEVER MEANS "nothing arrived" — 0.0 means that. NULL is one of two
    # things, told apart by recovery_labelled_through_days:
    #
    #   amount NULL, marker <  horizon  -> the horizon has not matured yet
    #   amount NULL, marker >= horizon  -> matured, but UNOBSERVABLE
    #   amount 0.0                      -> observed, and nothing came back
    #
    # UNOBSERVABLE (added 2026-08-24) means the loan had no case for the whole
    # window. Payment.case_id is NOT NULL, so money reaches the ledger only
    # through a case: a caseless loan cannot show a recovery however much the
    # borrower paid. Recording 0.0 there would put a fact in the training set
    # that is not one, and on the 2026-08-24 cohort it would have biased LOW
    # hardest — 50 of the 115 caseless loans are LOW against 26 HIGH — flattering
    # the scorecard's apparent separation. Validation excludes these and counts
    # them separately.
    recovered_amount_30: Mapped[float | None] = mapped_column(Float, nullable=True)
    recovered_amount_60: Mapped[float | None] = mapped_column(Float, nullable=True)
    recovered_amount_90: Mapped[float | None] = mapped_column(Float, nullable=True)

    # How far the labeller has got with this row: 0, 30, 60 or 90. NOT NULL with
    # a server default, so the partial index below has a total predicate and a
    # pre-existing row reads as "nothing done yet" rather than as unknown.
    #
    # This is what makes the recovery label multi-horizon at all. The repayment
    # labeller visits a row once and is finished; this one has to come back twice
    # more, and "have I already done 60?" has to be a fact on the row rather than
    # something inferred from which amount columns happen to be populated —
    # a genuinely-zero recovery at 60 days is indistinguishable from an unvisited
    # one otherwise.
    recovery_labelled_through_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    __table_args__ = (
        # One score per loan per day. This is what makes "ingest scored it at
        # 19:30, the beat scored it again at 19:45" a no-op instead of a
        # duplicate, and it is why both paths are safe to leave enabled.
        UniqueConstraint("loan_id", "as_of_date", name="uq_repayment_snapshot_grain"),
        Index("ix_repayment_snapshot_customer_asof", "customer_id", "as_of_date"),
        # The training pull: everything labelled, in a date window.
        Index("ix_repayment_snapshot_asof_outcome", "as_of_date", "outcome"),
        # The labeller's nightly scan. Partial, because the rows it wants are a
        # small and shrinking minority — every row it finds, it removes from
        # this index by filling in the outcome.
        Index(
            "ix_repayment_snapshot_unlabelled", "as_of_date",
            postgresql_where=text("outcome IS NULL"),
        ),
        # The recovery labeller's scan. A SEPARATE partial index, not a widening
        # of the one above: a row can have its repayment `outcome` filled in at
        # day 30 and still owe its 60- and 90-day recovery figures, so
        # "outcome IS NULL" stops describing the work left to do the moment
        # recovery labelling exists.
        #
        # Same shrinking-minority reasoning as above — every row this finds, it
        # removes from the index by advancing recovery_labelled_through_days.
        Index(
            "ix_repayment_snapshot_recovery_unlabelled", "as_of_date",
            postgresql_where=text("recovery_labelled_through_days < 90"),
        ),
    )

    def __repr__(self) -> str:      # pragma: no cover - debugging aid
        return (
            f"<RepaymentSnapshot loan={self.loan_id} as_of={self.as_of_date} "
            f"likelihood={self.likelihood} outcome={self.outcome or '-'}>"
        )
