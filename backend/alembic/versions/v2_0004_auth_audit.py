"""P1 auth + audit additions (tasks A06-A08, A16 for d4; P2 lifecycle declared).

1. audit_action_enum gains the invite / password / session / MFA actions d4
   writes now, and the agency-lifecycle / placement actions P2 will write
   (D01, placement). Values are appended, never reordered; `ENUM_ADDITIONS`
   is what tests/test_alembic_v2_baseline replays against the models.
2. tenancy.users.totp_secret VARCHAR(64) -> TEXT: the secret is stored
   Fernet-encrypted under TOTP_ENC_KEY (coordinator's A08 ruling), and a
   Fernet token does not fit in 64 characters.
3. tenancy.users.totp_last_step BIGINT NULL: the last accepted 30-second TOTP
   step, for replay refusal by compare-and-swap
   (`UPDATE … WHERE totp_last_step IS NULL OR totp_last_step < :step`).
   Never defaulted: NULL means "no code accepted yet".

Downgrade: Postgres cannot drop a value from an enum type. The values stay
(harmless; nothing below v2_0004 writes them) and a re-upgrade is a no-op for
them (`IF NOT EXISTS`). The two column changes are reversed; narrowing
totp_secret back to 64 refuses if any stored ciphertext is longer, which is
the right failure.

Revision ID: v2_0004
Revises: v2_0003
Create Date: 2026-09-28
"""
import sqlalchemy as sa
from alembic import op

revision = "v2_0004"
down_revision = "v2_0003"
branch_labels = None
depends_on = None

# (type name, values appended in this order) — replayed by the enum drift test.
ENUM_ADDITIONS = (
    ("audit_action_enum", (
        # identity (d4: A06 invites, A07 passwords, A08 MFA, A16 sessions) — written now
        "USER_INVITED", "INVITE_ACCEPTED", "INVITE_REVOKED", "USER_CREATED",
        "PASSWORD_CHANGED", "PASSWORD_RESET_ISSUED", "PASSWORD_RESET",
        "SESSION_REVOKED", "MFA_ENABLED", "MFA_DISABLED", "MFA_FAILED",
        # agency lifecycle + placement (P2 D01 / placement) — declared, wired with their services
        "AGENCY_ONBOARDED", "AGENCY_ACTIVATED", "AGENCY_SUSPENDED", "AGENCY_OFFBOARDED",
        "CONTRACT_CHANGED", "PLACEMENT_CREATED", "PLACEMENT_RECALLED", "USER_DEACTIVATED",
    )),
)


def upgrade() -> None:
    for name, values in ENUM_ADDITIONS:
        for v in values:
            op.execute(f"ALTER TYPE public.{name} ADD VALUE IF NOT EXISTS '{v}'")
    op.alter_column("users", "totp_secret", schema="tenancy",
                    existing_type=sa.String(length=64), type_=sa.Text(), existing_nullable=True)
    op.add_column("users", sa.Column("totp_last_step", sa.BigInteger(), nullable=True), schema="tenancy")


def downgrade() -> None:
    op.drop_column("users", "totp_last_step", schema="tenancy")
    op.alter_column("users", "totp_secret", schema="tenancy",
                    existing_type=sa.Text(), type_=sa.String(length=64), existing_nullable=True)
    # audit_action_enum values: kept (see the module docstring).
