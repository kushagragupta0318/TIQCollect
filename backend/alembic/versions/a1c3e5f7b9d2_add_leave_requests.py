"""leave requests — the door onto the leave calendar that only the seed could write

Revision ID: a1c3e5f7b9d2
Revises: d0b4e6f8a213
Create Date: 2026-09-21

One new table, `leave_requests`. Nothing on `beats` changes: approval writes
leave beats in the shape the seed always has (status CANCELLED,
is_leave_day=True, leave_type, leave_remarks), so the Team Duty calendar,
the Leave summary and the attendance endpoint keep reading exactly what they
read today. `beat_ids` on the request records which beats an approval wrote,
so a revoke removes those and only those.
"""
import sqlalchemy as sa
from alembic import op

revision = "a1c3e5f7b9d2"
down_revision = "d0b4e6f8a213"
branch_labels = None
depends_on = None


def upgrade() -> None:
    leave_type = sa.Enum("SICK_LEAVE", "CASUAL_LEAVE", "EARNED_LEAVE", "ABSENT", name="leave_type_enum")
    leave_status = sa.Enum("REQUESTED", "APPROVED", "REJECTED", "CANCELLED", name="leave_status_enum")
    op.create_table(
        "leave_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("manager_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("from_date", sa.Date(), nullable=False),
        sa.Column("to_date", sa.Date(), nullable=False),
        sa.Column("leave_type", leave_type, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", leave_status, nullable=False, server_default="REQUESTED"),
        sa.Column("requested_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(500), nullable=True),
        sa.Column("beat_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_leave_requests_agent_id", "leave_requests", ["agent_id"])
    op.create_index("ix_leave_requests_manager_user_id", "leave_requests", ["manager_user_id"])
    op.create_index("ix_leave_requests_status", "leave_requests", ["status"])
    op.create_index("ix_leave_agent_dates", "leave_requests", ["agent_id", "from_date", "to_date"])
    op.create_index("ix_leave_manager_status", "leave_requests", ["manager_user_id", "status"])


def downgrade() -> None:
    op.drop_table("leave_requests")
    sa.Enum(name="leave_status_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="leave_type_enum").drop(op.get_bind(), checkfirst=True)
