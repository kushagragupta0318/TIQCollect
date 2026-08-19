"""add agent_locations table

Backs the on-duty location trail and the SOS live position. See
app/models/agent_location.py for why the table exists.

Revision ID: b3f1c07a92de
Revises: cfb13e480d4c
Create Date: 2026-08-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b3f1c07a92de'
down_revision: Union[str, None] = 'cfb13e480d4c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False: the type is created explicitly in upgrade() below, so
# create_table() must not also emit a CREATE TYPE for it — doing both raises
# DuplicateObject, since checkfirst only guards the explicit call.
_SOURCE_ENUM = postgresql.ENUM(
    'HEARTBEAT', 'CHECK_IN', 'VISIT', 'SOS',
    name='location_source_enum', create_type=False,
)


def upgrade() -> None:
    _SOURCE_ENUM.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'agent_locations',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('agent_id', sa.String(), nullable=False),
        sa.Column('latitude', sa.Float(), nullable=False),
        sa.Column('longitude', sa.Float(), nullable=False),
        sa.Column('accuracy_metres', sa.Float(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('source', _SOURCE_ENUM, nullable=False,
                  server_default='HEARTBEAT'),
        sa.Column('is_sos', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
        sa.Column('battery_pct', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_agent_locations_agent_id', 'agent_locations', ['agent_id'])
    op.create_index('ix_agent_location_agent_time', 'agent_locations',
                    ['agent_id', 'recorded_at'])
    op.create_index('ix_agent_location_recorded', 'agent_locations', ['recorded_at'])
    op.create_index('ix_agent_location_sos', 'agent_locations',
                    ['agent_id', 'is_sos', 'recorded_at'])


def downgrade() -> None:
    op.drop_index('ix_agent_location_sos', table_name='agent_locations')
    op.drop_index('ix_agent_location_recorded', table_name='agent_locations')
    op.drop_index('ix_agent_location_agent_time', table_name='agent_locations')
    op.drop_index('ix_agent_locations_agent_id', table_name='agent_locations')
    op.drop_table('agent_locations')
    _SOURCE_ENUM.drop(op.get_bind(), checkfirst=True)
