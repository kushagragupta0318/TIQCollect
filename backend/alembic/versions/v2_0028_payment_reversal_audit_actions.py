"""audit_action_enum gains PAYMENT_REVERSAL_REQUESTED + PAYMENT_REVERSED (#2 reversal).

The four-eyes void of a mistaken collection writes two rows: the request
(PAYMENT_REVERSAL_REQUESTED) and the applied reversal (PAYMENT_REVERSED), mirroring
placement plan/apply. Appended, never reordered — ENUM_ADDITIONS is replayed by the
enum drift test, and the Python AuditAction members are at the END in this order.

Its own revision because Postgres will not let a value added by ALTER TYPE ADD VALUE
be USED in the same transaction; the table and the capability seed that follow are
in v2_0029. Downgrade: Postgres cannot drop an enum value; it stays harmlessly and a
re-upgrade is a no-op (IF NOT EXISTS).

Chains off v2_0027 (visit_evidence), the single head after merge-forward to f697b27.
Number lives only in this file; re-point if the wave shifts again at merge.
Revision ID: v2_0028
Revises: v2_0027
Create Date: 2026-10-01
"""
from alembic import op

revision = "v2_0028"
down_revision = "v2_0027"
branch_labels = None
depends_on = None

ENUM_ADDITIONS = (
    ("audit_action_enum", ("PAYMENT_REVERSAL_REQUESTED", "PAYMENT_REVERSED")),
)


def upgrade() -> None:
    for name, values in ENUM_ADDITIONS:
        for v in values:
            op.execute(f"ALTER TYPE public.{name} ADD VALUE IF NOT EXISTS '{v}'")


def downgrade() -> None:
    pass    # enum values cannot be dropped; see the module docstring
