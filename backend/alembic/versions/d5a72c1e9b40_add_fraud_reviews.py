"""add fraud_reviews and the ANOMALY_REVIEWED audit action

Stores a manager's verdict on a visit anomaly. Findings themselves are
recomputed, never stored — see app/models/fraud_review.py.

Revision ID: d5a72c1e9b40
Revises: c8e4a1b06f37
Create Date: 2026-08-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd5a72c1e9b40'
down_revision: Union[str, None] = 'c8e4a1b06f37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False: created explicitly below, so create_table must not emit a
# second CREATE TYPE for it.
_VERDICT = postgresql.ENUM(
    'CONFIRMED', 'DISMISSED', name='review_verdict_enum', create_type=False,
)


def upgrade() -> None:
    _VERDICT.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'fraud_reviews',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('visit_id', sa.String(), nullable=False),
        sa.Column('finding_type', sa.String(length=40), nullable=False),
        sa.Column('agent_id', sa.String(), nullable=False),
        sa.Column('verdict', _VERDICT, nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('reviewed_by_user_id', sa.String(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['visit_id'], ['visits.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewed_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('visit_id', 'finding_type', name='uq_fraud_review_visit_type'),
    )
    op.create_index('ix_fraud_reviews_visit_id', 'fraud_reviews', ['visit_id'])
    op.create_index('ix_fraud_reviews_agent_id', 'fraud_reviews', ['agent_id'])
    op.create_index('ix_fraud_review_agent_verdict', 'fraud_reviews', ['agent_id', 'verdict'])

    # Postgres cannot add an enum value inside a transaction before PG12; this
    # runs on 16, where it is allowed, and IF NOT EXISTS keeps it re-runnable.
    op.execute("ALTER TYPE audit_action_enum ADD VALUE IF NOT EXISTS 'ANOMALY_REVIEWED'")


def downgrade() -> None:
    op.drop_index('ix_fraud_review_agent_verdict', table_name='fraud_reviews')
    op.drop_index('ix_fraud_reviews_agent_id', table_name='fraud_reviews')
    op.drop_index('ix_fraud_reviews_visit_id', table_name='fraud_reviews')
    op.drop_table('fraud_reviews')
    _VERDICT.drop(op.get_bind(), checkfirst=True)
    # The audit enum value is intentionally left in place: removing a value from
    # a Postgres enum requires rebuilding the type, and any audit rows already
    # written with it would be orphaned.
