"""messaging schema: message_threads + messages, and the two messaging caps.

Bank↔agency threads anchored to a shared subject (a reversal now, a placement
later). Both tables are AGENCY_OWNED — v2_0012's two-party template, copied
verbatim as a frozen literal. RLS is coarse (tenant isolation); the same
expression serves USING and WITH CHECK.

`messaging` is an eleventh domain schema, added here (the baseline created ten).
CREATE SCHEMA (not IF NOT EXISTS, same rule as v2_0001), USAGE granted to the two
roles, and the database search_path is extended to include it so it matches
core.database.SEARCH_PATH.

subject_type / status / sender_side are String + CHECK (not native enums:
post-baseline native enums aren't tracked by the enum-drift test). The
MESSAGE_SENT audit value was added separately in v2_0030.

Assigned v2_0031 (chain v2_0029 reversal -> v2_0030 enum -> v2_0031). Renumber if
the wave shifts — the number is only in this file.
Revision ID: v2_0031
Revises: v2_0030
Create Date: 2026-10-07
"""
import sqlalchemy as sa
from alembic import op

revision = "v2_0031"
down_revision = "v2_0030"
branch_labels = None
depends_on = None

APP_ROLE, JOBS_ROLE = "tiq_app", "tiq_jobs"

# v2_0012's AGENCY_OWNED template, verbatim. BOTH arms require bank_id = current_bank_id().
_AGENCY_OWNED = ("(bank_id = tenancy.current_bank_id() AND (tenancy.current_scope() = 'BANK' "
                 "OR agency_id = tenancy.current_agency_id()))")
RLS_AGENCY_OWNED = ("messaging.message_threads", "messaging.messages")
RLS_POLICIES = {t: _AGENCY_OWNED for t in RLS_AGENCY_OWNED}

# The database search_path, extended with `messaging`. Matches core.database.SEARCH_PATH.
_SEARCH_PATH_WITH_MESSAGING = ("public, tenancy, lending, collections, workforce, planning, "
                               "ml, ai, strategy, audit, analytics, messaging")
_SEARCH_PATH_BASELINE = ("public, tenancy, lending, collections, workforce, planning, "
                         "ml, ai, strategy, audit, analytics")

_SUBJECT_TYPES = ("REVERSAL", "PLACEMENT")
_STATUSES = ("OPEN", "CLOSED")
_SIDES = ("BANK", "AGENCY")

PERMISSIONS = (
    ("messaging.read", "messaging", "read bank↔agency message threads", False, False),
    ("messaging.send", "messaging", "post a message to a bank↔agency thread", False, False),
)
ROLE_GRANTS = (
    ("BANK_ADMIN", "messaging.read"), ("BANK_ANALYST", "messaging.read"),
    ("BANK_TECHOPS", "messaging.read"), ("AGENCY_ADMIN", "messaging.read"),
    ("AGENCY_MANAGER", "messaging.read"),
    ("BANK_ADMIN", "messaging.send"), ("BANK_TECHOPS", "messaging.send"),
    ("AGENCY_ADMIN", "messaging.send"), ("AGENCY_MANAGER", "messaging.send"),
)


def _in(col: str, values: tuple[str, ...]) -> str:
    return col + " IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _if_role(role: str, sql: str) -> str:
    body = sql.replace("'", "''")
    return (f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
            f"THEN EXECUTE '{body}'; END IF; END $$;")


def _grant_specs() -> list[tuple[str, str, str]]:
    out = []
    for role in (APP_ROLE, JOBS_ROLE):
        out.append((role, "USAGE", "SCHEMA messaging"))
        for t in RLS_AGENCY_OWNED:
            out.append((role, "SELECT, INSERT, UPDATE, DELETE", t))
    return out


def upgrade() -> None:
    op.execute("CREATE SCHEMA messaging")      # not IF NOT EXISTS: never adopt a schema this did not create

    op.create_table(
        "message_threads",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("bank_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("agency_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("status", sa.String(length=8), server_default=sa.text("'OPEN'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(_in("subject_type", _SUBJECT_TYPES),
                           name=op.f("ck_message_threads_subject_type")),
        sa.CheckConstraint(_in("status", _STATUSES), name=op.f("ck_message_threads_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_threads")),
        schema="messaging",
    )
    op.create_index("uq_thread_per_subject", "message_threads", ["subject_type", "subject_id"],
                    unique=True, schema="messaging")
    op.create_index(op.f("ix_message_threads_bank_id_status"), "message_threads",
                    ["bank_id", "status"], schema="messaging")
    op.create_index(op.f("ix_message_threads_agency_id_status"), "message_threads",
                    ["agency_id", "status"], schema="messaging")

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("bank_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("agency_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("thread_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("sender_user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("sender_side", sa.String(length=6), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["messaging.message_threads.id"],
                                name=op.f("fk_messages_thread_id_message_threads"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_user_id"], ["tenancy.users.id"],
                                name=op.f("fk_messages_sender_user_id_users")),
        sa.CheckConstraint(_in("sender_side", _SIDES), name=op.f("ck_messages_sender_side")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        schema="messaging",
    )
    op.create_index(op.f("ix_messages_thread_id_created_at"), "messages",
                    ["thread_id", "created_at"], schema="messaging")

    for table, expr in RLS_POLICIES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY p_tenant ON {table} USING {expr} WITH CHECK {expr}")
    for role, priv, obj in _grant_specs():
        op.execute(_if_role(role, f"GRANT {priv} ON {obj} TO {role}"))

    # Extend the database search_path to include messaging (matches SEARCH_PATH).
    op.execute("DO $$ BEGIN "
               f"EXECUTE format('ALTER DATABASE %I SET search_path TO {_SEARCH_PATH_WITH_MESSAGING}', "
               "current_database()); END $$")

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

    op.execute("DO $$ BEGIN "
               f"EXECUTE format('ALTER DATABASE %I SET search_path TO {_SEARCH_PATH_BASELINE}', "
               "current_database()); END $$")
    for role, priv, obj in reversed(_grant_specs()):
        op.execute(_if_role(role, f"REVOKE {priv} ON {obj} FROM {role}"))
    for table in RLS_POLICIES:
        op.execute(f"DROP POLICY IF EXISTS p_tenant ON {table}")
    op.drop_index(op.f("ix_messages_thread_id_created_at"), table_name="messages", schema="messaging")
    op.drop_table("messages", schema="messaging")
    op.drop_index(op.f("ix_message_threads_agency_id_status"), table_name="message_threads", schema="messaging")
    op.drop_index(op.f("ix_message_threads_bank_id_status"), table_name="message_threads", schema="messaging")
    op.drop_index("uq_thread_per_subject", table_name="message_threads", schema="messaging")
    op.drop_table("message_threads", schema="messaging")
    op.execute("DROP SCHEMA IF EXISTS messaging")   # no CASCADE: refuses if anything else lives there
