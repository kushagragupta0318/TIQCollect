"""audit_action_enum gains USER_REACTIVATED and USER_ROLE_CHANGED (K01 Bank Users).

Bank Users records a reactivation and a role change under USER_DEACTIVATED
with an explicit `details.event`, because the enum had no member for either
and a lane cannot add one to a native Postgres type from its own branch. That
was the right call for the lane and the wrong state to leave the product in:
the bank Audit page renders counts_by_action and highlights USER_DEACTIVATED
as a sensitive action, so from the day Bank Users shipped a lender's
compliance screen counts every analyst/techops role flip and every
reactivation AS A DEACTIVATION, and shows rows reading "User deactivated" for
users who were not. details.event is not visible in a count.

Unlike AGENT_STATUS_CHANGED -- which is honestly named for a change in either
direction and legitimately covers suspend and reactivate -- USER_DEACTIVATED
asserts something that did not happen.

NOTHING ELSE IS IN THIS MIGRATION. Postgres will not let a value added by
ALTER TYPE ... ADD VALUE be USED in the same transaction, and alembic runs
each migration in one, so the bank_user_service change that starts writing
these two lands in a SEPARATE commit, after this. v2_0016 is the same shape
for the same reason.

NO BACKFILL, deliberately (coordinator's ruling, 2026-10-07, agreeing with the
recommendation). Rows already written as USER_DEACTIVATED with
details.event = "USER_ROLE_CHANGED" or "USER_REACTIVATED" KEEP that action.
Repainting them would mean UPDATE-ing audit rows, and the immutability of this
table is on CLAUDE.md's load-bearing list -- it is the one claim the trail
makes. A small historical inaccuracy on a days-old demo book is strictly
better than mutating audit history to tidy a count. The split is from this
revision forward.

Downgrade is a no-op: Postgres cannot drop an enum value. Same as v2_0016.

Revision ID: v2_0032
Revises: v2_0031
Create Date: 2026-10-07
"""
from alembic import op

revision = "v2_0032"
# Re-pointed from v2_0030 when Usage landed at v2_0031: both were children of
# v2_0030 for a moment, which is a FORK -- two heads, and `alembic upgrade
# head` refuses. Verified by reparsing after the merge-forward rather than by
# assuming. The number is monotonic; the PARENT is what prevents the fork.
down_revision = "v2_0031"
branch_labels = None
depends_on = None

ENUM_ADDITIONS = (
    ("audit_action_enum", ("USER_REACTIVATED", "USER_ROLE_CHANGED")),
)


def upgrade() -> None:
    for name, values in ENUM_ADDITIONS:
        for v in values:
            op.execute(f"ALTER TYPE public.{name} ADD VALUE IF NOT EXISTS '{v}'")


def downgrade() -> None:
    pass    # enum values cannot be dropped; see the module docstring
