"""add model_predictions — the trained-model feedback loop

Revision ID: b3e7d1f90c25
Revises: a1c9f42b83d7
Create Date: 2026-09-08

One append-only row per served model score, plus the outcome the labeller
attaches later. See models/model_prediction.py for why the feature vector is
stored alongside the score rather than only the score.

NOTE ON THE TWO SCHEMA AUTHORITIES (CLAUDE.md known issue 5). seed_data.py does
drop_all + create_all and never stamps alembic_version, so a seeded box builds
this table from the model and never runs this migration, while a production box
must run this migration and never runs the seed. Both paths therefore have to be
kept correct, and they can silently diverge. This file and
app/models/model_prediction.py were written together and must be changed
together until that issue is resolved.
"""
from alembic import op
import sqlalchemy as sa

revision = "b3e7d1f90c25"
down_revision = "a1c9f42b83d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_predictions",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),

        sa.Column("model_name", sa.String(length=60), nullable=False),
        sa.Column("model_version", sa.String(length=30), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=True),

        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("entity_id", sa.String(length=40), nullable=False),
        sa.Column("loan_id", sa.String(length=36), nullable=True),
        sa.Column("case_id", sa.String(length=36), nullable=True),
        sa.Column("agent_id", sa.String(length=36), nullable=True),

        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),

        sa.Column("probability", sa.Float(), nullable=True),
        sa.Column("points", sa.Integer(), nullable=True),
        sa.Column("band", sa.String(length=4), nullable=True),
        sa.Column("is_modelled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("fallback_reason", sa.String(length=500), nullable=True),

        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("feature_coverage", sa.Float(), nullable=True),
        sa.Column("reason_codes", sa.JSON(), nullable=False),

        sa.Column("actual_outcome", sa.Integer(), nullable=True),
        sa.Column("outcome_attached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_horizon_days", sa.Integer(), nullable=True),

        sa.ForeignKeyConstraint(["loan_id"], ["loans.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_model_predictions_model_name", "model_predictions", ["model_name"])
    op.create_index("ix_model_predictions_model_version", "model_predictions", ["model_version"])
    op.create_index("ix_model_predictions_entity_id", "model_predictions", ["entity_id"])
    op.create_index("ix_model_predictions_loan_id", "model_predictions", ["loan_id"])
    op.create_index("ix_model_predictions_case_id", "model_predictions", ["case_id"])
    op.create_index("ix_model_predictions_agent_id", "model_predictions", ["agent_id"])
    op.create_index("ix_model_predictions_as_of_date", "model_predictions", ["as_of_date"])
    op.create_index("ix_model_predictions_band", "model_predictions", ["band"])
    op.create_index("ix_model_predictions_actual_outcome", "model_predictions",
                    ["actual_outcome"])
    op.create_index("ix_model_pred_model_version_asof", "model_predictions",
                    ["model_name", "model_version", "as_of_date"])
    op.create_index("ix_model_pred_pending_outcome", "model_predictions",
                    ["model_name", "actual_outcome", "as_of_date"])


def downgrade() -> None:
    op.drop_table("model_predictions")
