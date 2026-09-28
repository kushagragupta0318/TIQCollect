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

Downgrade re-creates the v2_0002 list. It refuses, with a message naming the
row count, while any row still carries MFA_CHANGED: the old constraint cannot
describe those rows. It deliberately does not remap them (to ADMIN_REVOKED or
anything else), because that would rewrite the record of why a session ended.
(Audit LOW, 2026-09-28: this said "refuses" when the only refusal was the raw
CHECK violation from ADD CONSTRAINT; the count is now checked first.)

Revision ID: v2_0005
Revises: v2_0004
Create Date: 2026-09-28
"""
import sqlalchemy as sa
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
    n = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM tenancy.user_sessions WHERE revoked_reason = 'MFA_CHANGED'")).scalar()
    if n:
        raise RuntimeError(
            f"v2_0005 downgrade refused: {n} user_sessions row(s) carry revoked_reason 'MFA_CHANGED', "
            "which the v2_0004 constraint cannot hold. They are not remapped (that would rewrite why a "
            "session ended); resolve them deliberately, then downgrade.")
    op.drop_constraint(_NAME, "user_sessions", schema="tenancy", type_="check")
    op.create_check_constraint(op.f(_NAME), "user_sessions", _check(_PREVIOUS), schema="tenancy")
