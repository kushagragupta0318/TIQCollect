"""a served score names every version it was produced with, and reconciles

Revision ID: d0b4e6f8a213
Revises: c9a3d5e7f102
Create Date: 2026-09-16

Two nullable JSON columns on `model_predictions`, for recovery_risk 2.2.0
(the GAM) and any model after it:

    scoring_versions   {model_artifact, artifact_sha256, feature_definition,
                        calibration, risk_bands, reason_codes, background,
                        training_data} — the exact versions the row was
                        scored with. `model_version` and `artifact_sha256`
                        already named the model; nothing named the
                        calibrator, the band table, the reason-code mapping
                        or the background population the explanation is
                        centred on, and each of those can change without the
                        model changing.
    contributions      the score's own arithmetic: intercept, one entry per
                        feature and per declared interaction, and the logit,
                        which sum exactly. A reason code is a selection from
                        this; the row keeps the whole so the selection can be
                        audited.

Never backfilled: rows written before this migration do not know, and NULL
says so. A scorecard row stays NULL on `contributions` — its per-feature
points are a property of the artifact's points table, not of the row.
"""
import sqlalchemy as sa
from alembic import op

revision = "d0b4e6f8a213"
down_revision = "c9a3d5e7f102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_predictions", sa.Column("scoring_versions", sa.JSON(), nullable=True))
    op.add_column("model_predictions", sa.Column("contributions", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_predictions", "contributions")
    op.drop_column("model_predictions", "scoring_versions")
