"""collections.payment_reversal_requests + the three reversal capabilities (#2).

The two-stage reversal's request row (ADR 0015). AGENCY_OWNED: both the bank and the
agency touch it at different stages, so it takes v2_0012's two-party template, copied
verbatim as a frozen literal (every revision copies its template rather than importing
it). RLS is COARSE — tenant isolation only; the stage lock is service-enforced (ADR
0015), so the same expression serves USING and WITH CHECK.

reversal_status_enum is a new native enum in public. The three capabilities
(payment.reversal.request/.approve.agency/.approve.bank) seed into tenancy.permissions
+ role_permissions (v2_0008's pattern). The audit-action enum values were added
separately in v2_0028 (a value added by ALTER TYPE cannot be used in the same tx).

Assigned v2_0029 by fc (chain v2_0025->0026 l8->0027 l7-n1->0028->0029). Renumber if
the wave shifts — number is only in this file.
Revision ID: v2_0029
Revises: v2_0028
Create Date: 2026-10-01
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "v2_0029"
down_revision = "v2_0028"
branch_labels = None
depends_on = None

APP_ROLE, JOBS_ROLE = "tiq_app", "tiq_jobs"

# v2_0012's AGENCY_OWNED template, verbatim. BOTH arms require bank_id = current_bank_id():
# an agency session matches only within its own bank, never on agency_id alone.
_AGENCY_OWNED = ("(bank_id = tenancy.current_bank_id() AND (tenancy.current_scope() = 'BANK' "
                 "OR agency_id = tenancy.current_agency_id()))")
RLS_AGENCY_OWNED = ("collections.payment_reversal_requests",)
RLS_POLICIES = {t: _AGENCY_OWNED for t in RLS_AGENCY_OWNED}

_STATUSES = ("PENDING_AGENCY", "PENDING_BANK", "APPROVED", "REJECTED")

PERMISSIONS = (
    ("payment.reversal.request", "payment", "request to reverse a mistaken collection", False, False),
    ("payment.reversal.approve.agency", "payment",
     "the agency's approval of a reversal (routes it to the bank)", False, False),
    ("payment.reversal.approve.bank", "payment",
     "the bank's fiduciary final sign-off; the ledger unwinds here", False, True),
)
ROLE_GRANTS = (
    ("AGENCY_MANAGER", "payment.reversal.request"),
    ("AGENCY_MANAGER", "payment.reversal.approve.agency"),
    ("AGENCY_ADMIN", "payment.reversal.approve.agency"),
    ("BANK_ADMIN", "payment.reversal.approve.bank"),
)


def _if_role(role: str, sql: str) -> str:
    body = sql.replace("'", "''")
    return (f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
            f"THEN EXECUTE '{body}'; END IF; END $$;")


def _grant_specs() -> list[tuple[str, str, str]]:
    out = []
    for t in RLS_AGENCY_OWNED:
        out.append((APP_ROLE, "SELECT, INSERT, UPDATE, DELETE", t))
        out.append((JOBS_ROLE, "SELECT, INSERT, UPDATE, DELETE", t))
    return out


def upgrade() -> None:
    reversal_status = postgresql.ENUM(*_STATUSES, name="reversal_status_enum", schema="public")
    reversal_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "payment_reversal_requests",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("bank_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("agency_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("payment_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("case_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", postgresql.ENUM(name="reversal_status_enum", schema="public", create_type=False),
                  server_default=sa.text("'PENDING_AGENCY'"), nullable=False),
        sa.Column("agency_requested_by_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("agency_approved_by_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("agency_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bank_approved_by_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("bank_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["payment_id", "bank_id"],
                                ["collections.payments.id", "collections.payments.bank_id"],
                                name=op.f("fk_payment_reversal_requests_payment_id_bank_id_payments"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agency_requested_by_id"], ["tenancy.users.id"],
                                name=op.f("fk_payment_reversal_requests_agency_requested_by_id_users")),
        sa.ForeignKeyConstraint(["agency_approved_by_id"], ["tenancy.users.id"],
                                name=op.f("fk_payment_reversal_requests_agency_approved_by_id_users")),
        sa.ForeignKeyConstraint(["bank_approved_by_id"], ["tenancy.users.id"],
                                name=op.f("fk_payment_reversal_requests_bank_approved_by_id_users")),
        sa.CheckConstraint(
            "(status IN ('PENDING_AGENCY', 'REJECTED')) OR "
            "(agency_approved_by_id IS NOT NULL AND agency_approved_at IS NOT NULL)",
            name=op.f("ck_payment_reversal_requests_agency_stage_named_once_passed")),
        sa.CheckConstraint(
            "(status <> 'APPROVED') OR (bank_approved_by_id IS NOT NULL AND bank_approved_at IS NOT NULL)",
            name=op.f("ck_payment_reversal_requests_bank_stage_named_when_approved")),
        sa.CheckConstraint(
            "bank_approved_by_id IS NULL OR "
            "(bank_approved_by_id <> agency_requested_by_id AND bank_approved_by_id <> agency_approved_by_id)",
            name=op.f("ck_payment_reversal_requests_bank_approver_is_not_the_agency")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_reversal_requests")),
        schema="collections",
    )
    op.create_index("uq_one_open_reversal_per_payment", "payment_reversal_requests", ["payment_id"],
                    unique=True, schema="collections", postgresql_where=sa.text("status <> 'REJECTED'"))
    op.create_index(op.f("ix_payment_reversal_requests_agency_id_status"), "payment_reversal_requests",
                    ["agency_id", "status"], schema="collections")

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
    op.drop_index(op.f("ix_payment_reversal_requests_agency_id_status"),
                  table_name="payment_reversal_requests", schema="collections")
    op.drop_index("uq_one_open_reversal_per_payment", table_name="payment_reversal_requests",
                  schema="collections", postgresql_where=sa.text("status <> 'REJECTED'"))
    op.drop_table("payment_reversal_requests", schema="collections")
    postgresql.ENUM(name="reversal_status_enum", schema="public").drop(op.get_bind(), checkfirst=True)
