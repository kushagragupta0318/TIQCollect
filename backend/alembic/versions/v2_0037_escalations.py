"""General escalations: collections.escalation_issues + collections.thread_reads,
the ISSUE subject type, and the messaging.escalate capability.

An agency opens a general escalation (any issue, not tied to a reversal/placement);
it is the subject of one ISSUE message thread. thread_reads is the per-user read
marker behind the inbox's unread flag. Both tables are AGENCY_OWNED — v2_0012's
two-party template, verbatim — and live in collections (USAGE already granted by
v2_0012; this adds the table privileges).

subject_type on message_threads widens to include ISSUE (a String + CHECK, not a
native enum — the CHECK is dropped and recreated). The ESCALATION_STATUS_CHANGED
audit value was added separately in v2_0036.

Chains off v2_0036. Number lives only in this file; reparse at merge.
Revision ID: v2_0037
Revises: v2_0036
Create Date: 2026-10-07
"""
import sqlalchemy as sa
from alembic import op

revision = "v2_0037"
down_revision = "v2_0036"
branch_labels = None
depends_on = None

APP_ROLE, JOBS_ROLE = "tiq_app", "tiq_jobs"

_AGENCY_OWNED = ("(bank_id = tenancy.current_bank_id() AND (tenancy.current_scope() = 'BANK' "
                 "OR agency_id = tenancy.current_agency_id()))")
RLS_AGENCY_OWNED = ("collections.escalation_issues", "collections.thread_reads")
RLS_POLICIES = {t: _AGENCY_OWNED for t in RLS_AGENCY_OWNED}

_SUBJECTS_OLD = ("REVERSAL", "PLACEMENT")
_SUBJECTS_NEW = ("REVERSAL", "PLACEMENT", "ISSUE")
_ISSUE_STATUSES = ("OPEN", "RESOLVED", "CLOSED")

PERMISSIONS = (
    ("messaging.escalate", "messaging", "open a general bank↔agency escalation", False, False),
)
ROLE_GRANTS = (
    ("AGENCY_ADMIN", "messaging.escalate"),
    ("AGENCY_MANAGER", "messaging.escalate"),
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
        for t in RLS_AGENCY_OWNED:          # USAGE on collections already granted by v2_0012
            out.append((role, "SELECT, INSERT, UPDATE, DELETE", t))
    return out


def upgrade() -> None:
    # Widen message_threads.subject_type to allow ISSUE (String + CHECK, not an enum).
    # IF EXISTS: tolerant of a schema where the CHECK was already removed (e.g. a test
    # fixture that strips CHECKs before seeding) — the recreate restores the right one.
    op.execute("ALTER TABLE collections.message_threads "
               "DROP CONSTRAINT IF EXISTS ck_message_threads_subject_type")
    op.create_check_constraint(op.f("ck_message_threads_subject_type"), "message_threads",
                               _in("subject_type", _SUBJECTS_NEW), schema="collections")

    op.create_table(
        "escalation_issues",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("bank_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("agency_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=10), server_default=sa.text("'OPEN'"), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["tenancy.users.id"],
                                name=op.f("fk_escalation_issues_created_by_user_id_users")),
        sa.CheckConstraint(_in("status", _ISSUE_STATUSES), name=op.f("ck_escalation_issues_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_escalation_issues")),
        schema="collections",
    )
    op.create_index(op.f("ix_escalation_issues_agency_id_status"), "escalation_issues",
                    ["agency_id", "status"], schema="collections")
    op.create_index(op.f("ix_escalation_issues_bank_id_status"), "escalation_issues",
                    ["bank_id", "status"], schema="collections")

    op.create_table(
        "thread_reads",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("bank_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("agency_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("thread_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["collections.message_threads.id"],
                                name=op.f("fk_thread_reads_thread_id_message_threads"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["tenancy.users.id"],
                                name=op.f("fk_thread_reads_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_thread_reads")),
        schema="collections",
    )
    op.create_index("uq_thread_read_per_user", "thread_reads", ["thread_id", "user_id"],
                    unique=True, schema="collections")

    for table, expr in RLS_POLICIES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY p_tenant ON {table} USING {expr} WITH CHECK {expr}")
    for role, priv, obj in _grant_specs():
        op.execute(_if_role(role, f"GRANT {priv} ON {obj} TO {role}"))

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
    for role, priv, obj in reversed(_grant_specs()):
        op.execute(_if_role(role, f"REVOKE {priv} ON {obj} FROM {role}"))
    for table in RLS_POLICIES:
        op.execute(f"DROP POLICY IF EXISTS p_tenant ON {table}")
    op.drop_index("uq_thread_read_per_user", table_name="thread_reads", schema="collections")
    op.drop_table("thread_reads", schema="collections")
    op.drop_index(op.f("ix_escalation_issues_bank_id_status"), table_name="escalation_issues", schema="collections")
    op.drop_index(op.f("ix_escalation_issues_agency_id_status"), table_name="escalation_issues", schema="collections")
    op.drop_table("escalation_issues", schema="collections")
    # Restore the narrower subject_type CHECK (no ISSUE). IF EXISTS tolerates a
    # schema where the CHECK was already removed; NOT VALID adds it for future
    # writes without re-validating existing rows — a downgrade should not hard-fail
    # on rows already present (and the RLS test seeds generic subject_type values
    # with the CHECK stripped). A fresh upgrade recreates it VALID.
    op.execute("ALTER TABLE collections.message_threads "
               "DROP CONSTRAINT IF EXISTS ck_message_threads_subject_type")
    op.execute("ALTER TABLE collections.message_threads ADD CONSTRAINT ck_message_threads_subject_type "
               f"CHECK ({_in('subject_type', _SUBJECTS_OLD)}) NOT VALID")
