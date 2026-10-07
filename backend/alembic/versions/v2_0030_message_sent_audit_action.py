"""audit_action_enum gains MESSAGE_SENT (bank↔agency messaging).

Each message posted to a bank↔agency thread writes one append-only audit row
(MESSAGE_SENT), carrying the thread's bank_id/agency_id. Appended, never
reordered — ENUM_ADDITIONS is replayed by the enum drift test, and the Python
AuditAction member is at the END in this order.

Its own revision because Postgres will not let a value added by ALTER TYPE ADD
VALUE be used in the same transaction; the schema + tables follow in v2_0031.
Downgrade: Postgres cannot drop an enum value; it stays harmlessly and a
re-upgrade is a no-op (IF NOT EXISTS).

Chains off v2_0029 (the reversal table), the single head. Number lives only in
this file; re-point if the wave shifts at merge.
Revision ID: v2_0030
Revises: v2_0029
Create Date: 2026-10-07
"""
from alembic import op

revision = "v2_0030"
down_revision = "v2_0029"
branch_labels = None
depends_on = None

ENUM_ADDITIONS = (
    ("audit_action_enum", ("MESSAGE_SENT",)),
)


def upgrade() -> None:
    for name, values in ENUM_ADDITIONS:
        for v in values:
            op.execute(f"ALTER TYPE public.{name} ADD VALUE IF NOT EXISTS '{v}'")


def downgrade() -> None:
    pass    # enum values cannot be dropped; see the module docstring
