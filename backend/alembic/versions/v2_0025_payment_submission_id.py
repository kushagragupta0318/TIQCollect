"""client_submission_id on payments (retried-payment double-count fix, lane l7).

v2_0015 gave visits, call_logs and ptps a client-supplied submission id with a partial
UNIQUE (agent_id, client_submission_id), so a retry after a lost response returns the row it
already made. Payments got nothing: the only guard was a 15-second window, which a retry after
the photo uploads always exceeds, so a second Payment row was written — the collection counted
twice, a second receipt SMS to the borrower, and nothing in the product to reverse it.

Same column, same index pattern as v2_0015. Nullable: a payment before this, or from a client
that sends no id (the 15-second window still covers it), carries none.

The partial predicate also requires agent_id IS NOT NULL: agent_id is nullable (a direct bank
payment has no collecting agent) and Postgres treats NULLs as distinct, so without it two
agentless rows with the same id would both be allowed. The agent endpoint always has an agent,
but the index should state exactly what it enforces.

Written against v2_0021 (the head of TIQCollect-app now). 0022 (l8), 0023 (l7-n1) and 0024 (l5)
are ahead of it in the batch — renumber if the chain lands in another order (fc owns the chain;
the number lives only in this file, so a renumber is this file alone).

Revision ID: v2_0025
Revises: v2_0021
Create Date: 2026-10-01
"""
import sqlalchemy as sa
from alembic import op

revision = "v2_0025"
down_revision = "v2_0021"
branch_labels = None
depends_on = None

_PREDICATE = "client_submission_id IS NOT NULL AND agent_id IS NOT NULL"


def upgrade() -> None:
    op.add_column("payments", sa.Column("client_submission_id", sa.Uuid(as_uuid=False), nullable=True),
                  schema="collections")
    op.create_index("uq_payments_agent_id_client_submission_id", "payments",
                    ["agent_id", "client_submission_id"], unique=True, schema="collections",
                    postgresql_where=sa.text(_PREDICATE))


def downgrade() -> None:
    op.drop_index("uq_payments_agent_id_client_submission_id", table_name="payments",
                  schema="collections", postgresql_where=sa.text(_PREDICATE))
    op.drop_column("payments", "client_submission_id", schema="collections")
