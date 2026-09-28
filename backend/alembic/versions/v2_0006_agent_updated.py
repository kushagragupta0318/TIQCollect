"""audit_action_enum gains AGENT_UPDATED (P2 G02 Manage Agents, for ce).

An agency editing an agent's territory, phone, specialisation and similar
fields without a status change. AGENT_STATUS_CHANGED covers status moves,
USER_CREATED the creation; nothing covered a plain edit. Appended, never
reordered; ENUM_ADDITIONS is replayed by the enum drift test.

Downgrade: Postgres cannot drop an enum value; it stays, harmlessly, and a
re-upgrade is a no-op (IF NOT EXISTS).

Revision ID: v2_0006
Revises: v2_0005
Create Date: 2026-09-28
"""
from alembic import op

revision = "v2_0006"
down_revision = "v2_0005"
branch_labels = None
depends_on = None

ENUM_ADDITIONS = (
    ("audit_action_enum", ("AGENT_UPDATED",)),
)


def upgrade() -> None:
    for name, values in ENUM_ADDITIONS:
        for v in values:
            op.execute(f"ALTER TYPE public.{name} ADD VALUE IF NOT EXISTS '{v}'")


def downgrade() -> None:
    pass    # enum values cannot be dropped; see the module docstring
