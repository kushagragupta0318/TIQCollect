"""add repayment_score_snapshots

A point-in-time record of one repayment likelihood, the features behind it, and
— filled in an outcome horizon later — what the borrower actually did. This is
the training set for a future model, produced as a by-product of operating the
scorecard. See app/models/repayment_snapshot.py for why the features are frozen
here rather than recomputed.

Revision ID: e91b4c2d70af
Revises: d5a72c1e9b40
Create Date: 2026-08-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e91b4c2d70af'
down_revision: Union[str, None] = 'd5a72c1e9b40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Reused, not created. risk_category_enum already exists — it is the type of
# customers.risk_category. create_type=False stops create_table emitting a
# CREATE TYPE that would fail with DuplicateObject, and there is deliberately
# no .create() call to match: this migration does not own the type, so it must
# not drop it in downgrade() either.
_RISK_CATEGORY = postgresql.ENUM(
    'LOW', 'MEDIUM', 'HIGH', 'CRITICAL',
    name='risk_category_enum', create_type=False,
)


def upgrade() -> None:
    op.create_table(
        'repayment_score_snapshots',
        sa.Column('id', sa.String(), nullable=False),

        # Grain. loan_id is NOT NULL on purpose: Postgres treats NULLs as
        # distinct inside a UNIQUE constraint, so a nullable column here would
        # let duplicates slip past uq_repayment_snapshot_grain silently.
        sa.Column('loan_id', sa.String(), nullable=False),
        sa.Column('customer_id', sa.String(), nullable=False),
        sa.Column('case_id', sa.String(), nullable=True),

        sa.Column('as_of_date', sa.Date(), nullable=False),
        sa.Column('scored_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('trigger', sa.String(length=16), nullable=False),
        sa.Column('is_backfill', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),

        # The score.
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('model_version', sa.String(length=40), nullable=False),
        sa.Column('likelihood', sa.Float(), nullable=False),
        sa.Column('risk_score', sa.Float(), nullable=False),
        sa.Column('band', sa.String(length=20), nullable=False),
        sa.Column('risk_category', _RISK_CATEGORY, nullable=False),
        sa.Column('evidence_coverage', sa.Float(), nullable=False),

        # JSONB rather than JSON: this table is queried as a training set, and
        # JSONB indexes and avoids reparsing on every read.
        sa.Column('features', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('contributions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),

        # Outcome — all nullable. NULL means "not yet known", never "nothing
        # happened"; the labeller fills these one horizon later.
        sa.Column('outcome', sa.String(length=24), nullable=True),
        sa.Column('outcome_amount', sa.Float(), nullable=True),
        sa.Column('outcome_source', sa.String(length=20), nullable=True),
        sa.Column('outcome_observed_at', sa.Date(), nullable=True),
        sa.Column('outcome_horizon_days', sa.Integer(), nullable=True),
        sa.Column('feature_age_days', sa.Integer(), nullable=True),

        sa.ForeignKeyConstraint(['loan_id'], ['loans.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        # One score per loan per day. This is what makes the ingest call at
        # 19:30 and the beat at 19:45 idempotent rather than duplicating.
        sa.UniqueConstraint('loan_id', 'as_of_date',
                            name='uq_repayment_snapshot_grain'),
    )
    op.create_index('ix_repayment_score_snapshots_loan_id',
                    'repayment_score_snapshots', ['loan_id'])
    op.create_index('ix_repayment_score_snapshots_customer_id',
                    'repayment_score_snapshots', ['customer_id'])
    op.create_index('ix_repayment_score_snapshots_as_of_date',
                    'repayment_score_snapshots', ['as_of_date'])
    op.create_index('ix_repayment_snapshot_customer_asof',
                    'repayment_score_snapshots', ['customer_id', 'as_of_date'])
    op.create_index('ix_repayment_snapshot_asof_outcome',
                    'repayment_score_snapshots', ['as_of_date', 'outcome'])
    # Partial: the labeller only ever scans unlabelled rows, and every row it
    # labels leaves this index. It stays small no matter how large the table.
    op.create_index('ix_repayment_snapshot_unlabelled',
                    'repayment_score_snapshots', ['as_of_date'],
                    postgresql_where=sa.text('outcome IS NULL'))


def downgrade() -> None:
    op.drop_index('ix_repayment_snapshot_unlabelled',
                  table_name='repayment_score_snapshots')
    op.drop_index('ix_repayment_snapshot_asof_outcome',
                  table_name='repayment_score_snapshots')
    op.drop_index('ix_repayment_snapshot_customer_asof',
                  table_name='repayment_score_snapshots')
    op.drop_index('ix_repayment_score_snapshots_as_of_date',
                  table_name='repayment_score_snapshots')
    op.drop_index('ix_repayment_score_snapshots_customer_id',
                  table_name='repayment_score_snapshots')
    op.drop_index('ix_repayment_score_snapshots_loan_id',
                  table_name='repayment_score_snapshots')
    op.drop_table('repayment_score_snapshots')
    # risk_category_enum is deliberately NOT dropped: it belongs to the
    # customers table, which this migration did not create.
