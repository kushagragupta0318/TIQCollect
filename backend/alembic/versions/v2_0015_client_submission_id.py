"""client_submission_id on visits, call_logs and ptps; the outbox cursor on agent_devices (P7, tiqcollect-37).

An agent's app queues a visit or a call offline and replays it when signal
returns, possibly hours later. The existing duplicate guard is a 15-second
window (visit_service._DUPLICATE_SUBMIT_WINDOW_SECONDS), useless for that.
The client now sends its own id for each submission; a second INSERT with
the same (agent_id, client_submission_id) fails on the partial unique index,
and the service returns the row the first one made. Nullable: rows written
before the outbox, or by a client that does not send one, carry none.

PTPs are a separate POST after the visit (set_ptp, its own 15-second
window), so they carry their own key. Evidence (photos, recordings,
signature) is columns on the visit row, so the visit's key covers it; there
is no separate media table in v2.

workforce.agent_devices gains last_outbox_seq / last_outbox_captured_at:
the last outbox item the server accepted from that device, for a
per-device monotonic check on replay. Design approved by coordinator 0c.

Revision ID: v2_0015
Revises: v2_0014
Create Date: 2026-09-29
"""
import sqlalchemy as sa
from alembic import op

revision = "v2_0015"
down_revision = "v2_0014"
branch_labels = None
depends_on = None

TABLES = ("visits", "call_logs", "ptps")


def upgrade() -> None:
    op.add_column("agent_devices", sa.Column("last_outbox_seq", sa.BigInteger(), nullable=True), schema="workforce")
    op.add_column("agent_devices", sa.Column("last_outbox_captured_at", sa.DateTime(timezone=True), nullable=True),
                  schema="workforce")
    for t in TABLES:
        op.add_column(t, sa.Column("client_submission_id", sa.Uuid(as_uuid=False), nullable=True),
                      schema="collections")
        op.create_index(f"uq_{t}_agent_id_client_submission_id", t, ["agent_id", "client_submission_id"],
                        unique=True, schema="collections",
                        postgresql_where=sa.text("client_submission_id IS NOT NULL"))


def downgrade() -> None:
    for t in reversed(TABLES):
        op.drop_index(f"uq_{t}_agent_id_client_submission_id", table_name=t, schema="collections",
                      postgresql_where=sa.text("client_submission_id IS NOT NULL"))
        op.drop_column(t, "client_submission_id", schema="collections")
    op.drop_column("agent_devices", "last_outbox_captured_at", schema="workforce")
    op.drop_column("agent_devices", "last_outbox_seq", schema="workforce")
