"""add label_comparison to model_predictions

Revision ID: e2c9b74a1f36
Revises: d4a1c62f0b73
Create Date: 2026-09-08

Two labellers answer the "did this recover?" question in this repo and they do
not agree:

    model      VERIFIED paid on THIS CASE >= 0.8 * min(overdue, emi)
    repayment  payments of ANY STATUS on ANY CASE of the loan > 0

The second reads as "the 90% rule", but `POSITIVE_OUTCOMES` contains both REPAID
and PARTIAL, so once binarised for training the ratio does not gate the positive
class at all — the bar is simply "some money arrived".

This column holds what the OTHER rule would have said about each matured
prediction, over the same as_of_date and horizon, alongside the payment sums the
disagreement attribution was derived from. It is additive and read by nothing:
`actual_outcome` is untouched, so the target `recovery_risk` was validated
against does not move. The decision to standardise and retrain comes after the
disagreement rate is measured on real matured outcomes, not before.

Nullable with no backfill: a NULL means "not yet compared", which is different
from "compared and identical" and must stay distinguishable.
"""
from alembic import op
import sqlalchemy as sa

revision = "e2c9b74a1f36"
down_revision = "d4a1c62f0b73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_predictions",
                  sa.Column("label_comparison", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_predictions", "label_comparison")
