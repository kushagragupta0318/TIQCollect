"""widen allocation_decisions.outcome to 32 chars

The column was varchar(20) while AllocationOutcome.DEFERRED_ROUTE_INFEASIBLE is
25 characters. Writing one would have raised StringDataRightTruncation; it never
fired, so the fault sat unexercised rather than fixed (26,841 rows carry only
ALLOCATED, BLOCKED and DEFERRED). Two further outcomes are added in the same
change — DEFERRED_PTP and DEFERRED_VISIT_CAP — which do fit in 20, but the
column should not be one rename away from breaking again.

Widening a varchar is not a rewrite in Postgres and takes no table lock beyond
a brief ACCESS EXCLUSIVE for the catalogue update, so this is safe on a live
table. The downgrade is only safe while no value exceeds 20 characters, which
is true of every row today; it will fail loudly rather than truncate if that
stops being true.

Revision ID: a1c9f42b83d7
Revises: f4b7d9c1e832
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa

revision = "a1c9f42b83d7"
down_revision = "f4b7d9c1e832"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "allocation_decisions",
        "outcome",
        existing_type=sa.String(length=20),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Refuse rather than truncate: a DEFERRED_ROUTE_INFEASIBLE or any future
    # long value would be silently mangled by a plain narrowing.
    conn = op.get_bind()
    too_long = conn.execute(
        sa.text(
            "SELECT count(*) FROM allocation_decisions WHERE length(outcome) > 20"
        )
    ).scalar()
    if too_long:
        raise RuntimeError(
            f"{too_long} allocation_decisions row(s) have an outcome longer than "
            "20 characters; narrowing the column would truncate them. Resolve "
            "those rows first."
        )
    op.alter_column(
        "allocation_decisions",
        "outcome",
        existing_type=sa.String(length=32),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
