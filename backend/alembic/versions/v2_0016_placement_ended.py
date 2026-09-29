"""audit_action_enum gains PLACEMENT_ENDED (P3 placements, for tiqcollect-2b).

The bank feed ends a placement: PAID_DIRECT / SETTLED -> RESOLVED,
WRITTEN_OFF -> RETURNED (coordinator 0c ruling, 2026-09-29). Written by
PlacementService.end_from_feed from scripts/ingest_daily. A bank or agency
recall stays PLACEMENT_RECALLED. Appended, never reordered; ENUM_ADDITIONS
is replayed by the enum drift test.

Downgrade: Postgres cannot drop an enum value; it stays, harmlessly, and a
re-upgrade is a no-op (IF NOT EXISTS).

Revision ID: v2_0016
Revises: v2_0015
Create Date: 2026-09-29
"""
from alembic import op

revision = "v2_0016"
down_revision = "v2_0015"
branch_labels = None
depends_on = None

ENUM_ADDITIONS = (
    ("audit_action_enum", ("PLACEMENT_ENDED",)),
)


def upgrade() -> None:
    for name, values in ENUM_ADDITIONS:
        for v in values:
            op.execute(f"ALTER TYPE public.{name} ADD VALUE IF NOT EXISTS '{v}'")


def downgrade() -> None:
    pass    # enum values cannot be dropped; see the module docstring
