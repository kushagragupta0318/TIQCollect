"""add agents.gender for allocation eligibility

Customer.requires_female_agent could never be enforced: no agent gender was
recorded anywhere. See app/ml/eligibility.py.

Revision ID: c8e4a1b06f37
Revises: b3f1c07a92de
Create Date: 2026-08-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c8e4a1b06f37'
down_revision: Union[str, None] = 'b3f1c07a92de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agents', sa.Column('gender', sa.String(length=10), nullable=True))
    # Partial index: the only query that uses this column asks "which agents are
    # female", so indexing the rest of the table would be dead weight.
    op.create_index('ix_agents_gender', 'agents', ['gender'],
                    postgresql_where=sa.text("gender IS NOT NULL"))


def downgrade() -> None:
    op.drop_index('ix_agents_gender', table_name='agents')
    op.drop_column('agents', 'gender')
