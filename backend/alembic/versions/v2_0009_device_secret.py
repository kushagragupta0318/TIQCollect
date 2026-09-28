"""workforce.agent_devices.device_secret_sha256 (A09b).

The SHA-256 of a secret the SERVER issues when an agent's device is bound. A
login from the bound device must present the secret; the client-chosen
device_id alone could be replayed by anyone who learnt it. Nullable: devices
bound before this revision have none and are issued one on their next login.
Never the secret itself, and never anything derived from the client's IP
(owner's decision).

Revision ID: v2_0009
Revises: v2_0008
Create Date: 2026-09-28
"""
import sqlalchemy as sa
from alembic import op

revision = "v2_0009"
down_revision = "v2_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_devices", sa.Column("device_secret_sha256", sa.String(length=64), nullable=True),
                  schema="workforce")


def downgrade() -> None:
    op.drop_column("agent_devices", "device_secret_sha256", schema="workforce")
