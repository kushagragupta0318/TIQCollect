"""user_sessions.revoked_reason gains MFA_CHANGED (P1 A08, for d4).

Confirming an MFA enrollment ends every OTHER session of the user, because
those were opened without the second factor, and keeps the enrolling one.
None of the existing reasons says that honestly: it is neither a password
change nor an administrator's action.

The reason list is a CHECK constraint (ck_user_sessions_revoked_reason, from
v2_0002), so it is dropped and re-created with the list frozen here as
literals. `alembic check` does not compare CHECK text, so
tests/test_alembic_v2_baseline pins the latest frozen list to
models/identity.SESSION_REVOKE_REASONS.

Downgrade re-creates the v2_0002 list. It refuses while any row still carries
MFA_CHANGED, which is the right failure: the old constraint cannot describe
those rows.

Revision ID: v2_0005
Revises: v2_0004
Create Date: 2026-09-28
"""
from alembic import op

revision = "v2_0005"
down_revision = "v2_0004"
branch_labels = None
depends_on = None

SESSION_REVOKE_REASONS = (
    "LOGOUT", "ADMIN_REVOKED", "REUSE_DETECTED", "PASSWORD_CHANGED", "USER_DEACTIVATED", "EXPIRED",
    "DEVICE_RESET", "MFA_CHANGED",
)
_PREVIOUS = SESSION_REVOKE_REASONS[:-1]          # exactly v2_0002's list

_NAME = "ck_user_sessions_revoked_reason"


def _check(reasons) -> str:
    return "revoked_reason IS NULL OR revoked_reason IN (" + ", ".join(f"'{r}'" for r in reasons) + ")"


def upgrade() -> None:
    op.drop_constraint(_NAME, "user_sessions", schema="tenancy", type_="check")
    op.create_check_constraint(op.f(_NAME), "user_sessions", _check(SESSION_REVOKE_REASONS), schema="tenancy")


def downgrade() -> None:
    op.drop_constraint(_NAME, "user_sessions", schema="tenancy", type_="check")
    op.create_check_constraint(op.f(_NAME), "user_sessions", _check(_PREVIOUS), schema="tenancy")
