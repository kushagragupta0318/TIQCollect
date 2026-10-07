"""audit_action_enum gains MESSAGE_SENT (bank↔agency messaging).

Each message posted to a bank↔agency thread writes one append-only audit row
(MESSAGE_SENT), carrying the thread's bank_id/agency_id. Appended, never
reordered — ENUM_ADDITIONS is replayed by the enum drift test, and the Python
AuditAction member is at the END in this order.

Its own revision because Postgres will not let a value added by ALTER TYPE ADD
VALUE be used in the same transaction; the schema + tables follow in v2_0031.
Downgrade: Postgres cannot drop an enum value; it stays harmlessly and a
re-upgrade is a no-op (IF NOT EXISTS).

Chains off v2_0033 (Usage's llm_calls token-default fix), the head after merge-forward
to the cash-forecast integration (4df7096). MESSAGE_SENT is appended LAST — its
migration runs after every other audit_action_enum value, matching the Python member
order. Number lives only in this file.
Revision ID: v2_0034
Revises: v2_0033
Create Date: 2026-10-07
"""
from alembic import op

revision = "v2_0034"
down_revision = "v2_0033"
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
