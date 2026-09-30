"""strategy.simulation_runs + simulation_results (T3; DATA-MODEL-V2 §2512, plan §7.1).

Schema and models only: no service, no task, no endpoint reads these yet.

Two choices worth stating, because both were open in the brief:
  - PERCENTILES ARE COLUMNS (p5..p95, mean, sem) on one row per (run, metric,
    segment, period), as §2512 has it, not one row per percentile. They are
    taken per path and then across paths, so a period's band is ONE reading;
    rows-per-percentile would let half a band exist. The unique key leads on
    run_id, so it also serves "everything for this run, by period".
  - `status` takes §2512's five values PLUS `ABSTAINED`, with `abstain_reason`
    set exactly when it is used. E02's engine refuses to report a number it
    cannot stand behind, and "it declined" must not read as "it crashed"
    (FAILED) or "someone stopped it" (CANCELLED).

`memo_report_id` (§2512, FK to strategy.reports) is NOT here: that table does
not exist yet, and a UUID with no FK behind it would look wired when it is not.

Bank-only under RLS, template (c) of §8.3, as v2_0013 did for cost_rates.

Revision ID: v2_0018
Revises: v2_0017
Create Date: 2026-09-30
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "v2_0018"
down_revision = "v2_0017"
branch_labels = None
depends_on = None

APP_ROLE, JOBS_ROLE = "tiq_app", "tiq_jobs"
TABLES = ("simulation_runs", "simulation_results")

# Frozen literals, as every revision here keeps them; the models hold the same tuples.
KINDS = ("MONTE_CARLO", "BACKTEST", "SCENARIO", "OPTIMISER")
STATUSES = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "ABSTAINED")
APPROVALS = ("NONE", "PENDING", "APPROVED", "REJECTED")
UNITS = ("INR", "PCT", "COUNT")

# Read by tests/test_rls_policy_map.py: both tables are bank-only (§8.3 template (c)).
RLS_BANK_ONLY = ("strategy.simulation_runs", "strategy.simulation_results")
_BANK_ONLY = "(bank_id = tenancy.current_bank_id() AND tenancy.current_scope() = 'BANK')"
RLS_POLICIES = {t: _BANK_ONLY for t in RLS_BANK_ONLY}


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(repr(v) for v in values) + ")"


def _if_role(role: str, sql: str) -> str:
    body = sql.replace("'", "''")
    return (f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
            f"THEN EXECUTE '{body}'; END IF; END $$;")


def _grant_specs() -> list[tuple[str, str, str]]:
    out = []
    for t in RLS_BANK_ONLY:
        out.append((APP_ROLE, "SELECT, INSERT, UPDATE, DELETE", t))
        out.append((JOBS_ROLE, "SELECT, INSERT, UPDATE, DELETE", t))    # the engine runs as tiq_jobs
    return out


def upgrade() -> None:
    op.create_table(
        "simulation_runs",
        sa.Column("bank_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="QUEUED"),
        sa.Column("progress_pct", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("n_paths", sa.Integer(), nullable=False),
        sa.Column("horizon_months", sa.SmallInteger(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("engine_version", sa.String(length=30), nullable=False),
        sa.Column("data_version", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("backtest_start_date", sa.Date(), nullable=True),
        sa.Column("backtest_band_coverage", sa.Float(), nullable=True),
        sa.Column("baseline_run_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("calibrated_by_backtest", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("synthetic_inputs", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("synthetic_warning", sa.Text(), nullable=True),
        sa.Column("calibration", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("assumptions", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False),
        sa.Column("numpy_version", sa.String(length=20), nullable=True),
        sa.Column("chunk_paths", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("abstain_reason", sa.Text(), nullable=True),
        sa.Column("approval_status", sa.String(length=10), nullable=False, server_default="NONE"),
        sa.Column("approved_by", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(_in("kind", KINDS), name=op.f("ck_simulation_runs_kind")),
        sa.CheckConstraint(_in("status", STATUSES), name=op.f("ck_simulation_runs_status")),
        sa.CheckConstraint(_in("approval_status", APPROVALS), name=op.f("ck_simulation_runs_approval_status")),
        sa.CheckConstraint("progress_pct BETWEEN 0 AND 100", name=op.f("ck_simulation_runs_progress_range")),
        sa.CheckConstraint("n_paths > 0 AND horizon_months > 0", name=op.f("ck_simulation_runs_positive_run")),
        sa.CheckConstraint("(status = 'ABSTAINED') = (abstain_reason IS NOT NULL)",
                           name=op.f("ck_simulation_runs_abstain_reason_iff")),
        sa.CheckConstraint("calibrated_by_backtest OR synthetic_inputs OR synthetic_warning IS NOT NULL",
                           name=op.f("ck_simulation_runs_uncalibrated_says_so")),
        sa.ForeignKeyConstraint(["bank_id"], ["tenancy.banks.id"], name=op.f("fk_simulation_runs_bank_id_banks")),
        sa.ForeignKeyConstraint(["created_by"], ["tenancy.users.id"],
                                name=op.f("fk_simulation_runs_created_by_users")),
        sa.ForeignKeyConstraint(["approved_by"], ["tenancy.users.id"],
                                name=op.f("fk_simulation_runs_approved_by_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_simulation_runs")),
        sa.UniqueConstraint("id", "bank_id", name=op.f("uq_simulation_runs_id_bank_id")),
        schema="strategy",
    )
    # Self-reference, added after the table exists (scenario comparison).
    op.create_foreign_key(op.f("fk_simulation_runs_baseline_run_id_simulation_runs"), "simulation_runs",
                          "simulation_runs", ["baseline_run_id"], ["id"],
                          source_schema="strategy", referent_schema="strategy")
    op.create_index(op.f("ix_simulation_runs_bank_id_created_at"), "simulation_runs", ["bank_id", "created_at"],
                    schema="strategy")

    op.create_table(
        "simulation_results",
        sa.Column("bank_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("run_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("metric", sa.String(length=40), nullable=False),
        sa.Column("period_index", sa.SmallInteger(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("segment_key", sa.String(length=200), nullable=False, server_default="ALL"),
        sa.Column("segment_loan_type",
                  postgresql.ENUM(name="loan_type_enum", schema="public", create_type=False), nullable=True),
        sa.Column("segment_state", sa.String(length=16), nullable=True),
        sa.Column("segment_region_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("segment_agency_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("p5", sa.Float(), nullable=True),
        sa.Column("p10", sa.Float(), nullable=True),
        sa.Column("p50", sa.Float(), nullable=True),
        sa.Column("p90", sa.Float(), nullable=True),
        sa.Column("p95", sa.Float(), nullable=True),
        sa.Column("mean", sa.Float(), nullable=True),
        sa.Column("sem", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=8), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.CheckConstraint(_in("unit", UNITS), name=op.f("ck_simulation_results_unit")),
        sa.CheckConstraint("period_index >= 0", name=op.f("ck_simulation_results_period_index_non_negative")),
        sa.ForeignKeyConstraint(["bank_id"], ["tenancy.banks.id"], name=op.f("fk_simulation_results_bank_id_banks")),
        # The run carries the tenant: a result cannot be read under another bank's scope.
        sa.ForeignKeyConstraint(["run_id", "bank_id"],
                               ["strategy.simulation_runs.id", "strategy.simulation_runs.bank_id"],
                               name=op.f("fk_simulation_results_run_id_bank_id_simulation_runs"),
                               ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_simulation_results")),
        sa.UniqueConstraint("run_id", "metric", "segment_key", "period_index",
                            name=op.f("uq_simulation_results_run_id_metric_segment_key_period_index")),
        schema="strategy",
    )

    for table, expr in RLS_POLICIES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY p_tenant ON {table} USING {expr} WITH CHECK {expr}")
    for role, priv, obj in _grant_specs():
        op.execute(_if_role(role, f"GRANT {priv} ON {obj} TO {role}"))


def downgrade() -> None:
    for role, priv, obj in reversed(_grant_specs()):
        op.execute(_if_role(role, f"REVOKE {priv} ON {obj} FROM {role}"))
    for table in RLS_POLICIES:
        op.execute(f"DROP POLICY IF EXISTS p_tenant ON {table}")
    op.drop_table("simulation_results", schema="strategy")
    op.drop_index(op.f("ix_simulation_runs_bank_id_created_at"), table_name="simulation_runs", schema="strategy")
    op.drop_table("simulation_runs", schema="strategy")
