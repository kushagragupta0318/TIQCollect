# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B05) — New file: the lending tables v1 never had
#   (docs/DATA-MODEL-V2.md §4.2).
#
#   loan_dpd_history is the one that matters most. v1 overwrote Loan.dpd and
#   Loan.status in place, so there was no delinquency panel at all — and the
#   transition matrix, roll/cure rates, the NPA bridge, the Monte Carlo and
#   every "NO_HISTORY_FEATURES" the ML config excludes all need one. One
#   writer: the ingest upserts on (loan_id, as_of_date); the generator writes
#   LEDGER rows. Nothing else writes.
#
#   loan_instalments makes DPD derivable instead of asserted (the ledger
#   simulator's billing arithmetic). What was paid is never stored here: it is
#   derived from VERIFIED payments, the one definition. An `amount_paid`
#   column would be a second writer of arrears.
#
#   bank_feed_batches / bank_feed_rows keep what the bank sent (v1's ingest
#   read the CSV and kept nothing), and bank_actions records what the bank DID
#   — replacing the free-text resolution_notes prefix that was the only trace
#   of a RECALL.
# ────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer,
    SmallInteger, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base, CreatedAtMixin, JsonDoc, Money, UUIDPrimaryKey, UUIDType, uuid_fk,
)
from app.models.loan import DPD_BUCKET_SQL, LOAN_STATUS_SQL, LOAN_TYPE_SQL, DPDBucket, LoanStatus, LoanType

SCHEMA = "lending"


class LoanInstalment(Base, UUIDPrimaryKey, CreatedAtMixin):
    __tablename__ = "loan_instalments"

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    loan_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    schedule_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    instalment_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_due: Mapped[float] = mapped_column(Money, nullable=False)
    principal_component: Mapped[float | None] = mapped_column(Money)
    interest_component: Mapped[float | None] = mapped_column(Money)
    is_current_schedule: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(12), nullable=False)

    __table_args__ = (
        UniqueConstraint("loan_id", "schedule_version", "instalment_no"),
        ForeignKeyConstraint(["loan_id", "bank_id"], ["lending.loans.id", "lending.loans.bank_id"],
                             ondelete="CASCADE"),
        CheckConstraint("source IN ('BANK_FEED', 'LEDGER', 'GENERATED')", name="source"),
        Index("ix_loan_instalments_due", "bank_id", "due_date",
              postgresql_where="is_current_schedule", sqlite_where="is_current_schedule = 1"),
        {"schema": SCHEMA},
    )


DPD_HISTORY_SOURCES = ("FEED", "LEDGER", "SNAPSHOT", "PREDICTION_LOG", "TRANSFORM_CURRENT")


class LoanDpdHistory(Base):
    """One row per loan per month-end, plus a daily row for the current month.

    Partitioned monthly by as_of_date on Postgres (migration-only DDL, design
    §7); the natural key (loan_id, as_of_date) is the PK and already carries
    the partition key.
    """
    __tablename__ = "loan_dpd_history"

    loan_id: Mapped[str] = mapped_column(UUIDType, primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    dpd: Mapped[int] = mapped_column(Integer, nullable=False)
    dpd_bucket: Mapped[DPDBucket] = mapped_column(DPD_BUCKET_SQL, nullable=False)
    loan_status: Mapped[LoanStatus | None] = mapped_column(LOAN_STATUS_SQL)
    overdue_amount: Mapped[float | None] = mapped_column(Money)
    total_outstanding: Mapped[float | None] = mapped_column(Money)
    outstanding_principal: Mapped[float | None] = mapped_column(Money)
    penal_charges: Mapped[float | None] = mapped_column(Money)
    npa_flag: Mapped[bool | None] = mapped_column(Boolean)
    loan_type: Mapped[LoanType] = mapped_column(LOAN_TYPE_SQL, nullable=False)   # segment key
    region_id: Mapped[str | None] = uuid_fk("tenancy.regions.id", nullable=True)
    agency_id: Mapped[str | None] = uuid_fk("tenancy.agencies.id", nullable=True)
    placement_id: Mapped[str | None] = mapped_column(UUIDType)
    is_month_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    is_backfill: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    observed_pit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    feed_batch_id: Mapped[str | None] = mapped_column(UUIDType)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.now(), default=func.now())

    __table_args__ = (
        # No FK from loan_dpd_history to loans on the partitioned table would
        # cost a trigger per partition; the composite FK is declared and kept.
        ForeignKeyConstraint(["loan_id", "bank_id"], ["lending.loans.id", "lending.loans.bank_id"],
                             ondelete="RESTRICT"),
        CheckConstraint("source IN (" + ", ".join(repr(s) for s in DPD_HISTORY_SOURCES) + ")", name="source"),
        CheckConstraint("dpd >= 0", name="dpd_non_negative"),
        Index(None, "bank_id", "as_of_date"),
        Index("ix_loan_dpd_history_month_end", "bank_id", "as_of_date",
              postgresql_where="is_month_end", sqlite_where="is_month_end = 1"),
        Index(None, "agency_id", "as_of_date"),
        {"schema": SCHEMA},
    )


FEED_TYPES = ("DAILY_BOOK", "PAYMENTS", "ACTIONS")
FEED_STATUSES = ("RECEIVED", "VALIDATING", "LOADED", "PARTIAL", "REJECTED")


class BankFeedBatch(Base, UUIDPrimaryKey, CreatedAtMixin):
    __tablename__ = "bank_feed_batches"

    bank_id: Mapped[str] = uuid_fk("tenancy.banks.id")
    feed_type: Mapped[str] = mapped_column(String(12), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(500))
    received_via: Mapped[str] = mapped_column(String(8), nullable=False, default="UPLOAD")
    uploaded_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="RECEIVED")
    rows_total: Mapped[int | None] = mapped_column(Integer)
    rows_accepted: Mapped[int | None] = mapped_column(Integer)
    rows_quarantined: Mapped[int | None] = mapped_column(Integer)
    rows_skipped: Mapped[int | None] = mapped_column(Integer)
    dq_report: Mapped[dict | None] = mapped_column(JsonDoc)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.now(), default=func.now())
    loaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("bank_id", "feed_type", "business_date", "file_sha256"),
        CheckConstraint("feed_type IN ('DAILY_BOOK', 'PAYMENTS', 'ACTIONS')", name="feed_type"),
        CheckConstraint("received_via IN ('SFTP', 'API', 'UPLOAD', 'DEMO')", name="received_via"),
        CheckConstraint("status IN (" + ", ".join(repr(s) for s in FEED_STATUSES) + ")", name="status"),
        Index(None, "bank_id", "business_date"),
        {"schema": SCHEMA},
    )


FEED_ROW_STATUSES = ("PENDING", "ACCEPTED", "QUARANTINED", "SKIPPED", "RELEASED")


class BankFeedRow(Base, UUIDPrimaryKey, CreatedAtMixin):
    """Staging and quarantine: every column as received, and why a row was held."""
    __tablename__ = "bank_feed_rows"

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    batch_id: Mapped[str] = uuid_fk("lending.bank_feed_batches.id", ondelete="CASCADE")
    row_no: Mapped[int] = mapped_column(Integer, nullable=False)
    loan_account_number: Mapped[str | None] = mapped_column(String(30))
    customer_ref: Mapped[str | None] = mapped_column(String(30))
    case_number: Mapped[str | None] = mapped_column(String(20))
    raw: Mapped[dict] = mapped_column(JsonDoc, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="PENDING")
    dq_errors: Mapped[list | None] = mapped_column(JsonDoc)
    loan_id: Mapped[str | None] = uuid_fk("lending.loans.id", nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)

    __table_args__ = (
        UniqueConstraint("batch_id", "row_no"),
        CheckConstraint("status IN (" + ", ".join(repr(s) for s in FEED_ROW_STATUSES) + ")", name="status"),
        Index("ix_bank_feed_rows_quarantined", "bank_id", "status",
              postgresql_where="status = 'QUARANTINED'", sqlite_where="status = 'QUARANTINED'"),
        Index(None, "loan_account_number"),
        {"schema": SCHEMA},
    )


class BankAction(Base, UUIDPrimaryKey, CreatedAtMixin):
    """What the bank did to a loan (PAID_DIRECT, RECALL, SETTLED, WRITTEN_OFF,
    DECEASED). Idempotent on (loan, action, date) so a re-sent feed is a no-op."""
    __tablename__ = "bank_actions"

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    loan_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    case_id: Mapped[str | None] = uuid_fk("collections.cases.id", nullable=True)
    placement_id: Mapped[str | None] = uuid_fk("collections.placements.id", nullable=True)
    action_type: Mapped[str] = mapped_column(
        String(30), ForeignKey("lending.bank_action_types.code", ondelete="RESTRICT"), nullable=False)
    action_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(100))
    amount: Mapped[float | None] = mapped_column(Money)
    remark: Mapped[str | None] = mapped_column(Text)
    batch_id: Mapped[str | None] = uuid_fk("lending.bank_feed_batches.id", nullable=True)
    feed_row_id: Mapped[str | None] = uuid_fk("lending.bank_feed_rows.id", ondelete="SET NULL", nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effects: Mapped[dict | None] = mapped_column(JsonDoc)

    __table_args__ = (
        UniqueConstraint("loan_id", "action_type", "action_date"),
        ForeignKeyConstraint(["loan_id", "bank_id"], ["lending.loans.id", "lending.loans.bank_id"],
                             ondelete="RESTRICT"),
        Index(None, "bank_id", "action_date"),
        Index(None, "case_id"),
        {"schema": SCHEMA},
    )
