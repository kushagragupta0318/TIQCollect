"""The `strategy` schema: the bank's own levers and what they cost (DATA-MODEL-V2 §4.8).

`cost_rates` is the ONE cost table: the scorecard's field_cost, the scenario
simulator and the activity ledger all read unit costs from here. Agency
commission is not a cost rate; it comes from agency_contract_terms.

`simulation_runs` / `simulation_results` hold reproducible Monte Carlo runs
(§2512, plan §7.1). Nothing calls them yet: v2_0018 is schema only.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKeyConstraint, Index, Integer,
    Numeric, SmallInteger, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, JsonDoc, UUIDPrimaryKey, UUIDType, uuid_fk
from app.models.loan import LOAN_TYPE_SQL, LoanType

SCHEMA = "strategy"


def _check_in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(repr(v) for v in values) + ")"

COST_CHANNELS = ("FIELD_VISIT", "CALL", "SMS", "WHATSAPP", "EMAIL", "IVR", "LEGAL_NOTICE")
COST_UNITS = ("PER_ATTEMPT", "PER_CONTACT", "PER_KM", "PER_MESSAGE", "PER_CASE")


class CostRate(Base, UUIDPrimaryKey, CreatedAtMixin):
    __tablename__ = "cost_rates"

    bank_id: Mapped[str] = uuid_fk("tenancy.banks.id")
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    unit: Mapped[str] = mapped_column(String(12), nullable=False)
    rate_inr: Mapped[float] = mapped_column(Numeric(12, 4, asdecimal=False), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)          # NULL = open-ended
    source: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)

    __table_args__ = (
        UniqueConstraint("bank_id", "channel", "unit", "valid_from"),
        CheckConstraint("channel IN (" + ", ".join(repr(c) for c in COST_CHANNELS) + ")", name="channel"),
        CheckConstraint("unit IN (" + ", ".join(repr(u) for u in COST_UNITS) + ")", name="unit"),
        CheckConstraint("rate_inr >= 0", name="rate_non_negative"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_range"),
        {"schema": SCHEMA},
    )


# §2512 plus ABSTAINED: E02's engine refuses to report a number it cannot stand behind, and
# "it declined" must not read as "it crashed" (FAILED) or "someone stopped it" (CANCELLED).
SIMULATION_KINDS = ("MONTE_CARLO", "BACKTEST", "SCENARIO", "OPTIMISER")
SIMULATION_STATUSES = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "ABSTAINED")
APPROVAL_STATUSES = ("NONE", "PENDING", "APPROVED", "REJECTED")
RESULT_UNITS = ("INR", "PCT", "COUNT")


class SimulationRun(Base, UUIDPrimaryKey, CreatedAtMixin):
    """One reproducible run: its seed and inputs are stored, so it can be re-run."""

    __tablename__ = "simulation_runs"

    bank_id: Mapped[str] = uuid_fk("tenancy.banks.id")
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="QUEUED")
    progress_pct: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    n_paths: Mapped[int] = mapped_column(Integer, nullable=False)
    horizon_months: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)       # the book snapshot
    inputs: Mapped[dict] = mapped_column(JsonDoc, nullable=False, default=dict)   # preset, macro, levers
    engine_version: Mapped[str] = mapped_column(String(30), nullable=False)
    data_version: Mapped[dict | None] = mapped_column(JsonDoc)           # the mv_refresh_log stamps it read
    backtest_start_date: Mapped[date | None] = mapped_column(Date)
    backtest_band_coverage: Mapped[float | None] = mapped_column(Float)  # observed p10-p90, nominal 0.80
    baseline_run_id: Mapped[str | None] = uuid_fk("strategy.simulation_runs.id", nullable=True,
                                                  use_alter=True)        # scenario comparison

    # ── E02 honesty (§0.1). A run says what it rests on, or says it cannot. ──
    calibrated_by_backtest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    synthetic_inputs: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    synthetic_warning: Mapped[str | None] = mapped_column(Text)          # "SYNTHETIC: …" / "UNCALIBRATED: …"
    calibration: Mapped[dict | None] = mapped_column(JsonDoc)            # engine_version, origin, horizon, coverage…
    assumptions: Mapped[list] = mapped_column(JsonDoc, nullable=False, default=list)
    numpy_version: Mapped[str | None] = mapped_column(String(20))
    chunk_paths: Mapped[list | None] = mapped_column(JsonDoc)
    abstain_reason: Mapped[str | None] = mapped_column(Text)             # set iff status = ABSTAINED

    approval_status: Mapped[str] = mapped_column(String(10), nullable=False, default="NONE")
    approved_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = uuid_fk("tenancy.users.id")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(_check_in("kind", SIMULATION_KINDS), name="kind"),
        CheckConstraint(_check_in("status", SIMULATION_STATUSES), name="status"),
        CheckConstraint(_check_in("approval_status", APPROVAL_STATUSES), name="approval_status"),
        CheckConstraint("progress_pct BETWEEN 0 AND 100", name="progress_range"),
        CheckConstraint("n_paths > 0 AND horizon_months > 0", name="positive_run"),
        # The honesty fields are not decoration: an abstention states why, and a run that is
        # neither calibrated nor honest about it is refused by the database.
        CheckConstraint("(status = 'ABSTAINED') = (abstain_reason IS NOT NULL)", name="abstain_reason_iff"),
        CheckConstraint("calibrated_by_backtest OR synthetic_inputs OR synthetic_warning IS NOT NULL",
                        name="uncalibrated_says_so"),
        Index(None, "bank_id", "created_at"),
        {"schema": SCHEMA},
    )


class SimulationResult(Base, UUIDPrimaryKey):
    """One (metric, segment, period) of a run. PERCENTILES ARE COLUMNS, not rows:
    they are taken per path and then across paths (§2512, plan §7.1), so p5..p95
    of one period are one reading and cannot be stored half-written."""

    __tablename__ = "simulation_results"

    bank_id: Mapped[str] = uuid_fk("tenancy.banks.id")
    run_id: Mapped[str] = uuid_fk("strategy.simulation_runs.id", ondelete="CASCADE")
    metric: Mapped[str] = mapped_column(String(40), nullable=False)      # GNPA_PCT, ECL, NET_RECOVERY…
    period_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    # A canonical NULL-free key ("lt=HOME|st=SMA_1|r=<uuid>"), so the unique key below holds:
    # NULLs in the segment columns would make every "all segments" row distinct.
    segment_key: Mapped[str] = mapped_column(String(200), nullable=False, default="ALL")
    segment_loan_type: Mapped[LoanType | None] = mapped_column(LOAN_TYPE_SQL)
    segment_state: Mapped[str | None] = mapped_column(String(16))        # a dim_portfolio_state code
    segment_region_id: Mapped[str | None] = mapped_column(UUIDType)
    segment_agency_id: Mapped[str | None] = mapped_column(UUIDType)
    p5: Mapped[float | None] = mapped_column(Float)
    p10: Mapped[float | None] = mapped_column(Float)
    p50: Mapped[float | None] = mapped_column(Float)
    p90: Mapped[float | None] = mapped_column(Float)
    p95: Mapped[float | None] = mapped_column(Float)
    mean: Mapped[float | None] = mapped_column(Float)
    sem: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(8), nullable=False)

    __table_args__ = (
        # The run carries the tenant, so a result can never be read under another bank's scope.
        ForeignKeyConstraint(["run_id", "bank_id"],
                            ["strategy.simulation_runs.id", "strategy.simulation_runs.bank_id"],
                            ondelete="CASCADE"),
        UniqueConstraint("run_id", "metric", "segment_key", "period_index"),
        CheckConstraint(_check_in("unit", RESULT_UNITS), name="unit"),
        CheckConstraint("period_index >= 0", name="period_index_non_negative"),
        # Reading a run's shape: the unique key above leads on run_id, so (run_id, period_index)
        # needs no index of its own.
        {"schema": SCHEMA},
    )
