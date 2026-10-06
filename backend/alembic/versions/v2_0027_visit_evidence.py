"""escalation notes, witness and collected documents on visits (N1, lane l7).

The visit form has always captured what an agent types in the escalation box,
whether a witness was present, and up to four "Documents Collected" uploads, and
the submit sent none of it (docs/business/PRIORITIES.md N1; JOURNEYS 1a). The
visit row now keeps them. All nullable: a visit before this, or from a client
that does not send them, carries none. `documents` is a JSON list with one entry
per category (category, key, sha256, content_type); B23's visit_media
takes it over with the photo columns.

Revision ID: v2_0027
Revises: v2_0026
Create Date: 2026-09-30
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "v2_0027"
# v2_0017-v2_0021 are other lanes' and not on this branch yet (v2_0021 is the payment idempotency key).
# Re-point this to the merged head when rebasing; test_alembic_v2_baseline::_chain fails on a forgotten one.
down_revision = "v2_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("visits", sa.Column("escalation_notes", sa.Text(), nullable=True), schema="collections")
    op.add_column("visits", sa.Column("witness_present", sa.Boolean(), nullable=True), schema="collections")
    op.add_column("visits", sa.Column("witness_name", sa.String(length=200), nullable=True), schema="collections")
    op.add_column("visits", sa.Column("documents", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
                  schema="collections")


def downgrade() -> None:
    for column in ("documents", "witness_name", "witness_present", "escalation_notes"):
        op.drop_column("visits", column, schema="collections")
