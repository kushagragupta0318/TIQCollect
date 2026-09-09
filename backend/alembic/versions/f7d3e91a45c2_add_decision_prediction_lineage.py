"""link an allocation decision to the prediction that informed it

Revision ID: f7d3e91a45c2
Revises: e2c9b74a1f36
Create Date: 2026-09-09

`model_predictions` carried no run linkage, and a re-plan writes a fresh row per
case. Measured on the live demo book: nine allocation runs for plan_date
2026-09-10, 7-9 prediction rows per case, and **201 of 214 allocated decisions
matched MORE THAN ONE prediction** on (case_id, agent_id). Only 13 were
unambiguous.

Two things were broken by that. A decision could not be traced to the score that
produced it, so "why did this case go to this agent" was unanswerable at the
model level. And when outcomes mature, the same case would contribute eight or
nine near-identical rows to the monitor -- inflating n and correlating the
errors, which makes a Gini look better-estimated than it is.

THE LINK GOES ON THE DECISION, NOT THE RUN ON THE PREDICTION. A prediction is
borrower-side and agent-independent: one score can legitimately inform several
runs, and adding `allocation_run_id` to `model_predictions` would have forced a
choice about which run owns a row that several used. A decision, by contrast,
has exactly one score behind it. The foreign key is therefore the smallest thing
that makes the relationship single-valued in the direction it is actually asked.

Nullable and never backfilled: rows written before this migration genuinely do
not know which prediction informed them, and inventing a link would be worse
than admitting the gap. NULL here means "not recorded", never "no model".

ondelete SET NULL rather than CASCADE -- a decision is an audit record of what
the system did and must survive the pruning of a prediction it referenced.
"""
from alembic import op
import sqlalchemy as sa

revision = "f7d3e91a45c2"
down_revision = "e2c9b74a1f36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "allocation_decisions",
        sa.Column("model_prediction_id", sa.String(length=40), nullable=True),
    )
    op.create_foreign_key(
        "fk_allocation_decisions_model_prediction",
        "allocation_decisions", "model_predictions",
        ["model_prediction_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index(
        "ix_allocation_decisions_model_prediction_id",
        "allocation_decisions", ["model_prediction_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_allocation_decisions_model_prediction_id",
                  table_name="allocation_decisions")
    op.drop_constraint("fk_allocation_decisions_model_prediction",
                       "allocation_decisions", type_="foreignkey")
    op.drop_column("allocation_decisions", "model_prediction_id")
