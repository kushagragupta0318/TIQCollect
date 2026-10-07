"""Manager↔agent chat: AGENT_DIRECT subject, AGENT sender side, messaging.agent_chat.

A 1:1 thread between an agency (its managers/admins) and ONE of its agents
(subject_type = 'AGENT_DIRECT', subject_id = agent_id). Reuses the existing
message_threads / messages / thread_reads tables — no new tables, no enum. Only the
two String+CHECKs widen, and one capability seeds.

Per-agent isolation is SERVICE-enforced: the _AGENCY_OWNED RLS policy is agency-coarse
(no current_agent_id()). A CUTOVER-BLOCKING item for this axis is recorded in
DATA-MODEL-V2 §8.6 — before the tiq_app FORCE, a current_agent_id() GUC + a tightened
policy must land, or an AGENT scope would read agency-mates' threads via the DB.

Chains off v2_0037. Number lives only in this file; reparse at merge.
Revision ID: v2_0038
Revises: v2_0037
Create Date: 2026-10-07
"""
import sqlalchemy as sa
from alembic import op

revision = "v2_0038"
down_revision = "v2_0037"
branch_labels = None
depends_on = None

_SUBJECTS_OLD = ("REVERSAL", "PLACEMENT", "ISSUE")
_SUBJECTS_NEW = ("REVERSAL", "PLACEMENT", "ISSUE", "AGENT_DIRECT")
_SIDES_OLD = ("BANK", "AGENCY")
_SIDES_NEW = ("BANK", "AGENCY", "AGENT")

PERMISSIONS = (
    ("messaging.agent_chat", "messaging", "manager↔agent direct chat within an agency", False, False),
)
ROLE_GRANTS = (
    ("FIELD_AGENT", "messaging.agent_chat"),
    ("AGENCY_ADMIN", "messaging.agent_chat"),
    ("AGENCY_MANAGER", "messaging.agent_chat"),
)


def _in(col: str, values: tuple[str, ...]) -> str:
    return col + " IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _widen(table: str, constraint: str, col: str, values: tuple[str, ...]) -> None:
    # DROP IF EXISTS tolerates a schema where a test fixture stripped the CHECK;
    # the recreate (validated — a fresh upgrade runs on clean data) restores it.
    op.execute(f"ALTER TABLE collections.{table} DROP CONSTRAINT IF EXISTS {constraint}")
    op.create_check_constraint(op.f(constraint), table, _in(col, values), schema="collections")


def _narrow(table: str, constraint: str, col: str, values: tuple[str, ...]) -> None:
    # Downgrade: re-add NOT VALID so it does not hard-fail on rows already present
    # (existing AGENT_DIRECT/AGENT rows, or the RLS test's generic seeded values).
    op.execute(f"ALTER TABLE collections.{table} DROP CONSTRAINT IF EXISTS {constraint}")
    op.execute(f"ALTER TABLE collections.{table} ADD CONSTRAINT {constraint} "
               f"CHECK ({_in(col, values)}) NOT VALID")


def upgrade() -> None:
    _widen("message_threads", "ck_message_threads_subject_type", "subject_type", _SUBJECTS_NEW)
    _widen("messages", "ck_messages_sender_side", "sender_side", _SIDES_NEW)

    perms = sa.table("permissions", sa.column("code"), sa.column("category"), sa.column("description"),
                     sa.column("requires_second_person"), sa.column("is_sensitive"), schema="tenancy")
    grants = sa.table("role_permissions", sa.column("role"), sa.column("permission_code"), schema="tenancy")
    from sqlalchemy.dialects.postgresql import insert
    op.get_bind().execute(insert(perms).values([
        dict(code=c, category=cat, description=d, requires_second_person=sp, is_sensitive=s)
        for c, cat, d, sp, s in PERMISSIONS]).on_conflict_do_nothing())
    op.get_bind().execute(insert(grants).values([
        dict(role=r, permission_code=c) for r, c in ROLE_GRANTS]).on_conflict_do_nothing())


def downgrade() -> None:
    bind = op.get_bind()
    codes = [c for c, *_ in PERMISSIONS]
    bind.execute(sa.text("DELETE FROM tenancy.role_permissions WHERE permission_code = ANY(:c)"), {"c": codes})
    bind.execute(sa.text("DELETE FROM tenancy.permissions WHERE code = ANY(:c)"), {"c": codes})
    _narrow("messages", "ck_messages_sender_side", "sender_side", _SIDES_OLD)
    _narrow("message_threads", "ck_message_threads_subject_type", "subject_type", _SUBJECTS_OLD)
