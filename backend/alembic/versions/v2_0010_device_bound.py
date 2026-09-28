"""A09b audit (HIGH): pre-A09b device bindings are released; DEVICE_BOUND.

v2_0009 left devices bound before the secret existed with a NULL
device_secret_sha256, and the login code issued the secret to whoever next
presented that device's password + client-chosen device_id. That is exactly
the replay A09b exists to stop: an attacker who learnt the device_id and the
password would have been handed the secret, and the real phone locked out.

This revision releases every such binding instead (is_bound false, a reason
recorded), so the agent's next login is a FIRST bind — issued a secret and
audited as DEVICE_BOUND, which is appended to audit_action_enum here. A
binding with no secret hash is now a mismatch in the code as well, so a row
that somehow reappears is refused, never upgraded.

Downgrade: the enum value stays (Postgres cannot drop one; a re-upgrade is a
no-op), and released bindings stay released — re-binding a device nobody has
proven would be the defect this revision removes.

Revision ID: v2_0010
Revises: v2_0009
Create Date: 2026-09-28
"""
from alembic import op

revision = "v2_0010"
down_revision = "v2_0009"
branch_labels = None
depends_on = None

ENUM_ADDITIONS = (
    ("audit_action_enum", ("DEVICE_BOUND",)),
)

# Frozen literal, not an import: a migration states what it did on the day.
UNBIND_REASON = "A09b: bound before the device secret existed"


def upgrade() -> None:
    for name, values in ENUM_ADDITIONS:
        for v in values:
            op.execute(f"ALTER TYPE public.{name} ADD VALUE IF NOT EXISTS '{v}'")
    op.execute(
        "UPDATE workforce.agent_devices "
        "SET is_bound = false, unbound_at = now(), "
        f"unbind_reason = '{UNBIND_REASON}' "
        "WHERE is_bound AND device_secret_sha256 IS NULL")


def downgrade() -> None:
    pass    # see the module docstring
