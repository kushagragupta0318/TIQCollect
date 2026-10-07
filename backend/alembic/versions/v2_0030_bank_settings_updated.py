"""audit_action_enum gains BANK_SETTINGS_UPDATED (K01 Admin → Settings).

A bank admin editing the bank's ops settings (contact hours, geo-fence radius,
SLA target) through PATCH /bank/settings. The values themselves live in the
existing tenancy.banks.brand JSON bag, so there is no table or column change
here — only the one new audit-action value its write path records.

Appended, never reordered; ENUM_ADDITIONS is replayed by the enum drift test
(tests/test_alembic_v2_baseline.py).

Downgrade: Postgres cannot drop an enum value; it stays, harmlessly, and a
re-upgrade is a no-op (IF NOT EXISTS).

NOTE (coordinator): the number v2_0028 is this lane's claim on the next free
slot (head is v2_0027). If another lane also takes 0028, renumber at merge and
repoint down_revision — the content is one idempotent ADD VALUE.

Revision ID: v2_0028
Revises: v2_0027
Create Date: 2026-10-07
"""
from alembic import op

revision = "v2_0030"
down_revision = "v2_0029"
branch_labels = None
depends_on = None

ENUM_ADDITIONS = (
    ("audit_action_enum", ("BANK_SETTINGS_UPDATED",)),
)


def upgrade() -> None:
    for name, values in ENUM_ADDITIONS:
        for v in values:
            op.execute(f"ALTER TYPE public.{name} ADD VALUE IF NOT EXISTS '{v}'")


def downgrade() -> None:
    pass    # enum values cannot be dropped; see the module docstring
