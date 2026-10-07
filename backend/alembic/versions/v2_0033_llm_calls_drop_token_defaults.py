"""Two schema gaps on ai.llm_calls (v2_0031), both found on a pristine main
checkout (coordinator audit, 2026-10-07) and both folded into this one
revision since v2_0031 is already applied and frozen (CLAUDE.md):

1. input_tokens/output_tokens/cache_tokens carried a server_default the
   model never declared. `alembic check` compares the DB against
   app/models/llm_call.py, which uses Python-side `default=0` (applied by
   SQLAlchemy on INSERT), never `server_default` -- the two read the same on
   a normal insert, but `alembic check` only looks at the server side, so it
   saw v2_0031's DB default as an op the model doesn't ask for.
   test_the_head_matches_the_models and
   test_the_chain_downgrades_to_base_and_upgrades_again both failed.

   Fixed to match intent, not by adding the default to the model:
   core/llm.py's `_record_usage` is the only writer and it always sets all
   three explicitly, so the DB-side default was dead weight, not a
   documented contract.

2. ai.llm_calls got table grants (SELECT, INSERT) but v2_0031 never granted
   `tiq_app`/`tiq_jobs` USAGE on the `ai` schema itself -- `ai` had no
   policied table before this one, so unlike `strategy` (granted once, by
   v2_0013, when ITS first table landed; v2_0018's later strategy table
   did not need to repeat it) nothing had granted it yet. The RLS matrix
   test runs `SET ROLE tiq_app` and hit `permission denied for schema ai`
   on every row (found by 72, folded in here rather than a separate
   v2_0034 -- same frozen-v2_0031 reason as the default fix above).

   RLS is dormant at runtime (the API runs as `fieldops`, which bypasses
   it), so neither gap breaks anything live; both are exactly what would
   bite at the `tiq_app` cutover, which is the whole point of proving them
   on the matrix now.

Revision ID: v2_0033
Revises: v2_0032
Create Date: 2026-10-07
"""
from alembic import op

revision = "v2_0033"
down_revision = "v2_0032"
branch_labels = None
depends_on = None

APP_ROLE, JOBS_ROLE = "tiq_app", "tiq_jobs"
_COLUMNS = ("input_tokens", "output_tokens", "cache_tokens")


def _if_role(role: str, sql: str) -> str:
    body = sql.replace("'", "''")
    return (f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
            f"THEN EXECUTE '{body}'; END IF; END $$;")


def upgrade() -> None:
    for col in _COLUMNS:
        op.alter_column("llm_calls", col, server_default=None, schema="ai")
    for role in (APP_ROLE, JOBS_ROLE):
        op.execute(_if_role(role, f"GRANT USAGE ON SCHEMA ai TO {role}"))


def downgrade() -> None:
    for role in (APP_ROLE, JOBS_ROLE):
        op.execute(_if_role(role, f"REVOKE USAGE ON SCHEMA ai FROM {role}"))
    for col in _COLUMNS:
        op.alter_column("llm_calls", col, server_default="0", schema="ai")
