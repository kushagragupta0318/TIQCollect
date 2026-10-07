"""audit_action_enum gains ESCALATION_STATUS_CHANGED (messaging escalations).

Changing an escalation issue's status (resolve/close/reopen) writes one audit row,
by the bank or the owning agency, carrying the issue's bank_id/agency_id. Appended,
never reordered — ENUM_ADDITIONS is replayed by the enum drift test, and the Python
AuditAction member is at the END (after MESSAGE_SENT) in this order.

Its own revision because Postgres will not let a value added by ALTER TYPE ADD VALUE
be used in the same transaction; the escalation tables follow in v2_0037. Downgrade:
Postgres cannot drop an enum value; it stays harmlessly and a re-upgrade is a no-op.

Chains off v2_0035 (messaging tables), the single head. Number lives only in this
file; re-point if the wave shifts at merge.
Revision ID: v2_0036
Revises: v2_0035
Create Date: 2026-10-07
"""
from alembic import op

revision = "v2_0036"
down_revision = "v2_0035"
branch_labels = None
depends_on = None

ENUM_ADDITIONS = (
    ("audit_action_enum", ("ESCALATION_STATUS_CHANGED",)),
)


def upgrade() -> None:
    for name, values in ENUM_ADDITIONS:
        for v in values:
            op.execute(f"ALTER TYPE public.{name} ADD VALUE IF NOT EXISTS '{v}'")


def downgrade() -> None:
    pass    # enum values cannot be dropped; see the module docstring
