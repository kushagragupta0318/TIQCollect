"""the retraining lifecycle's durable state

Revision ID: b6c14e83af27
Revises: a5f8c31d7e40
Create Date: 2026-09-09

The 2026-09-09 audit traced the chain and found it stopped here:

    monitoring -> retrain_recommended -> [NOTHING]

`retrain_recommended` was a boolean returned into a structlog line and a Celery
result backend with no reader. `model_candidates` is what reads it, and it is
the only place a retraining attempt exists between the trigger and a person's
decision.

THE UNIQUE CONSTRAINT IS THE IDEMPOTENCY GUARANTEE. `monitoring_run_id` is a
digest of the monitoring EVENT — model, serving version, outcome definition,
matured count, verdict and reasons — not of the run that observed it. Two
nightly runs over the same matured cohort produce the same id, so a duplicate
trigger collides at the database rather than starting a second training job.

`state` is the safety property. Promotion reads it and only APPROVED is
promotable, and `ModelCandidate.is_promotable` additionally requires the
recorded gate and comparison results to be passes — so a row hand-edited into
APPROVED still cannot reach production.

Three audit actions are added alongside, because a model reaching production is
the highest-consequence manual action in the system and belongs in the audit
trail rather than only in the candidate's own history.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b6c14e83af27"
down_revision = "a5f8c31d7e40"
branch_labels = None
depends_on = None

_STATES = (
    "TRAINING", "VALIDATING", "COMPARING", "PENDING_APPROVAL", "APPROVED",
    "PROMOTED", "INSUFFICIENT_DATA", "REJECTED_VALIDATION",
    "REJECTED_COMPARISON", "REJECTED_BY_HUMAN", "FAILED",
)

_AUDIT_ACTIONS = ("MODEL_CANDIDATE_APPROVED", "MODEL_CANDIDATE_REJECTED",
                  "MODEL_PROMOTED")


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # Created explicitly with checkfirst, then referenced with create_type=False.
    # Without the second half, `create_table` tries to CREATE TYPE a second time
    # and the upgrade dies on DuplicateObject — which is exactly what happened
    # on the first live run of this migration.
    if is_pg:
        sa.Enum(*_STATES, name="model_candidate_state_enum").create(
            bind, checkfirst=True)
        state_enum = postgresql.ENUM(*_STATES, name="model_candidate_state_enum",
                                     create_type=False)
    else:
        state_enum = sa.Enum(*_STATES, name="model_candidate_state_enum")

    op.create_table(
        "model_candidates",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("model_name", sa.String(length=50), nullable=False),
        sa.Column("candidate_version", sa.String(length=50), nullable=True),
        sa.Column("incumbent_version", sa.String(length=50), nullable=True),
        sa.Column("state", state_enum, nullable=False),
        sa.Column("monitoring_run_id", sa.String(length=80), nullable=False),
        sa.Column("trigger_reasons", sa.JSON(), nullable=True),
        sa.Column("monitoring_report", sa.JSON(), nullable=True),
        sa.Column("training_cohort", sa.JSON(), nullable=True),
        sa.Column("cohort_rows", sa.Integer(), nullable=True),
        sa.Column("outcome_definition_version", sa.String(length=50), nullable=True),
        sa.Column("feature_set", sa.JSON(), nullable=True),
        sa.Column("code_versions", sa.JSON(), nullable=True),
        sa.Column("training_metrics", sa.JSON(), nullable=True),
        sa.Column("validation_run_id", sa.String(length=80), nullable=True),
        sa.Column("gate_results", sa.JSON(), nullable=True),
        sa.Column("gates_passed", sa.Boolean(), nullable=True),
        sa.Column("comparison_run_id", sa.String(length=80), nullable=True),
        sa.Column("comparison_results", sa.JSON(), nullable=True),
        sa.Column("comparison_passed", sa.Boolean(), nullable=True),
        sa.Column("gini_uplift", sa.Float(), nullable=True),
        sa.Column("decided_by_id", sa.String(length=36), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promoted_from_version", sa.String(length=50), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("state_history", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["decided_by_id"], ["users.id"],
                                ondelete="SET NULL"),
        sa.UniqueConstraint("model_name", "monitoring_run_id",
                            name="uq_candidate_monitoring_event"),
    )
    op.create_index("ix_model_candidates_model_name", "model_candidates",
                    ["model_name"])
    op.create_index("ix_model_candidates_state", "model_candidates", ["state"])
    op.create_index("ix_model_candidates_candidate_version", "model_candidates",
                    ["candidate_version"])
    op.create_index("ix_model_candidates_monitoring_run_id", "model_candidates",
                    ["monitoring_run_id"])
    op.create_index("ix_candidate_model_state", "model_candidates",
                    ["model_name", "state"])

    if is_pg:
        with op.get_context().autocommit_block():
            for action in _AUDIT_ACTIONS:
                op.execute(
                    f"ALTER TYPE audit_action_enum ADD VALUE IF NOT EXISTS '{action}'")


def downgrade() -> None:
    for ix in ("ix_candidate_model_state", "ix_model_candidates_monitoring_run_id",
               "ix_model_candidates_candidate_version", "ix_model_candidates_state",
               "ix_model_candidates_model_name"):
        op.drop_index(ix, table_name="model_candidates")
    op.drop_table("model_candidates")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        postgresql.ENUM(name="model_candidate_state_enum").drop(bind, checkfirst=True)
    # Postgres cannot drop a value from an enum; the three audit actions stay.
    # Harmless: nothing emits them once this migration is reversed.
