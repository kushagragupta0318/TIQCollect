"""audit_action_enum gains DOCUMENT_VERIFIED and DOCUMENT_REJECTED (D02, for ce).

The agency onboarding wizard's document review: a verifier (never the
uploader) accepts or rejects an agency document, and both outcomes are
audited. DOCUMENT_UPLOADED already existed. Appended, never reordered;
ENUM_ADDITIONS is replayed by the enum drift test.

Downgrade: Postgres cannot drop an enum value; it stays, harmlessly, and a
re-upgrade is a no-op (IF NOT EXISTS).

Revision ID: v2_0011
Revises: v2_0010
Create Date: 2026-09-28
"""
from alembic import op

revision = "v2_0011"
down_revision = "v2_0010"
branch_labels = None
depends_on = None

ENUM_ADDITIONS = (
    ("audit_action_enum", ("DOCUMENT_VERIFIED", "DOCUMENT_REJECTED")),
)


def upgrade() -> None:
    for name, values in ENUM_ADDITIONS:
        for v in values:
            op.execute(f"ALTER TYPE public.{name} ADD VALUE IF NOT EXISTS '{v}'")


def downgrade() -> None:
    pass    # enum values cannot be dropped; see the module docstring
