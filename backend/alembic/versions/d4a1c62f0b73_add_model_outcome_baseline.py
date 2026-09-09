"""add outcome baseline, definition version and status to model_predictions

Revision ID: d4a1c62f0b73
Revises: c8f2a41b6d09
Create Date: 2026-09-08

The label for `recovery_risk` is `paid >= 0.8 * min(overdue_amount,
emi_amount)`, and both of those are overwritten in place on `Loan`. Reading them
at labelling time would compare a payment window against a balance those very
payments already reduced, so they are frozen at prediction time here.

`outcome_baseline` is deliberately NOT folded into `features`: features are the
model's INPUTS and are what PSI is computed on; the baseline is what the OUTCOME
is measured against. `emi_amount` is not a model feature at all, so it has
nowhere else to live.

Rows written before this migration carry no baseline and resolve to
NO_BASELINE — they are left unlabelled rather than scored against a guess.
"""
from alembic import op
import sqlalchemy as sa

revision = "d4a1c62f0b73"
down_revision = "c8f2a41b6d09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_predictions", sa.Column("outcome_baseline", sa.JSON(), nullable=True))
    op.add_column("model_predictions",
                  sa.Column("outcome_definition_version", sa.String(length=40), nullable=True))
    op.add_column("model_predictions",
                  sa.Column("outcome_status", sa.String(length=30), nullable=True))
    op.create_index("ix_model_predictions_outcome_status", "model_predictions",
                    ["outcome_status"])


def downgrade() -> None:
    op.drop_index("ix_model_predictions_outcome_status", table_name="model_predictions")
    op.drop_column("model_predictions", "outcome_status")
    op.drop_column("model_predictions", "outcome_definition_version")
    op.drop_column("model_predictions", "outcome_baseline")
