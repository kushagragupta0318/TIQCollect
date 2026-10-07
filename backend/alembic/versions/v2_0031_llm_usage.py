"""ai.llm_calls (F11): one row per LLM call, written by core/llm.py's own
best-effort recorder — token counts, the feature that made the call, and a
cost computed once at write time from the provider's published price.

bank_id is NULLABLE, unlike every other BANK_ONLY table: none of today's six
call sites pass it yet (core/llm.py's `complete()`/`chat()` default it to
None), so most rows land unattributed at first, the same known, counted gap
audit.audit_logs already has for its own actor-less rows. RLS still applies
the plain bank-only template -- an unattributed row simply matches no bank's
scope, same as it matches no bank's `bank_id =` filter in the service layer
(services/bank/usage_read.py) -- `test_each_policy_fits_its_columns` only
requires the column, not NOT NULL.

Revision number given by the coordinator (tiqcollect-73, 2026-10-07): v2_0028
collided with the payment-reversal lane's own v2_0028, already merged; the
chain's single head is v2_0030 (bank_settings_updated). v2_0031 provisional —
confirm with the coordinator before this merges.

Revision ID: v2_0031
Revises: v2_0030
Create Date: 2026-10-07
"""
import sqlalchemy as sa
from alembic import op

revision = "v2_0031"
down_revision = "v2_0030"
branch_labels = None
depends_on = None

APP_ROLE, JOBS_ROLE = "tiq_app", "tiq_jobs"

# Picked up by tests/test_rls_policy_map.py's `_later()`, exactly as v2_0013
# and v2_0018 extend v2_0012's own BANK_ONLY list.
RLS_BANK_ONLY = ("ai.llm_calls",)
_BANK_ONLY_EXPR = "(bank_id = tenancy.current_bank_id() AND tenancy.current_scope() = 'BANK')"
RLS_POLICIES = {t: _BANK_ONLY_EXPR for t in RLS_BANK_ONLY}


def _if_role(role: str, sql: str) -> str:
    body = sql.replace("'", "''")
    return (f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
            f"THEN EXECUTE '{body}'; END IF; END $$;")


def _grant_specs() -> list[tuple[str, str, str]]:
    out = []
    for t in RLS_BANK_ONLY:
        # Append-only, like audit.audit_logs: a metering row is a historical
        # fact about what a call cost, never edited or removed after insert.
        out.append((APP_ROLE, "SELECT, INSERT", t))
        out.append((JOBS_ROLE, "SELECT, INSERT", t))   # Celery tasks call llm.complete() too
    return out


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("bank_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=60), nullable=False),
        sa.Column("feature", sa.String(length=40), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost", sa.Numeric(12, 6, asdecimal=False), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["bank_id"], ["tenancy.banks.id"], name=op.f("fk_llm_calls_bank_id_banks")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
        schema="ai",
    )
    op.create_index(op.f("ix_llm_calls_bank_id_created_at"), "llm_calls", ["bank_id", "created_at"],
                    schema="ai")
    op.create_index(op.f("ix_llm_calls_feature_created_at"), "llm_calls", ["feature", "created_at"],
                    schema="ai")

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
    op.drop_index(op.f("ix_llm_calls_feature_created_at"), table_name="llm_calls", schema="ai")
    op.drop_index(op.f("ix_llm_calls_bank_id_created_at"), table_name="llm_calls", schema="ai")
    op.drop_table("llm_calls", schema="ai")
