"""Schema drift fix: ai.llm_calls.{input_tokens,output_tokens,cache_tokens}
carried a server_default (v2_0031) the model never declared.

`alembic check` compares the DB against app/models/llm_call.py, which uses
Python-side `default=0` (applied by SQLAlchemy on INSERT), never
`server_default`. The two read the same in a normal insert, but `alembic
check` only looks at the server side, so it saw v2_0031's DB default as an
op the model doesn't ask for -- test_the_head_matches_the_models and
test_the_chain_downgrades_to_base_and_upgrades_again both failed on a
pristine checkout (coordinator audit, 2026-10-07, reproduced by 12).

Fixed to match intent, not by adding the default to the model: core/llm.py's
`_record_usage` is the only writer and it always sets all three explicitly
(every `LLMCall(...)` construction names input_tokens/output_tokens/
cache_tokens), so the DB-side default was never exercised and is dead
weight, not a documented contract. v2_0031 is already applied on main, so
this drops the default in a new revision rather than editing that one in
place (CLAUDE.md: a landed migration's SQL is frozen).

Revision ID: v2_0033
Revises: v2_0032
Create Date: 2026-10-07
"""
from alembic import op

revision = "v2_0033"
down_revision = "v2_0032"
branch_labels = None
depends_on = None

_COLUMNS = ("input_tokens", "output_tokens", "cache_tokens")


def upgrade() -> None:
    for col in _COLUMNS:
        op.alter_column("llm_calls", col, server_default=None, schema="ai")


def downgrade() -> None:
    for col in _COLUMNS:
        op.alter_column("llm_calls", col, server_default="0", schema="ai")
