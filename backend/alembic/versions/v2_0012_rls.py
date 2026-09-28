"""Row-level security, step 1 (A13): policies ENABLED, roles and grants, not enforced for the API.

DATA-MODEL-V2 §8. RLS enforces TENANCY only (bank, agency); team and self
rules stay in services/scope.py. Every policy reads the three helpers below,
which return NULL for an unset or reset setting, and every policy is false on
a NULL tenant: RLS fails closed.

What this revision does NOT do (step 2, owner-gated, §8.6):
- FORCE. The tables' owner (the API's login today) still bypasses, so no
  running stack changes behaviour. A superuser bypasses regardless.
- Move the API onto a `tiq_app` login and the workers onto `tiq_jobs`.
- SECURITY DEFINER functions for pre-authentication lookups.
tests/pg proves every policy AS tiq_app (SET ROLE) so step 2 flips a proven
switch.

Roles are cluster-wide. They are created here only when absent AND the
migrating user may create roles; otherwise a NOTICE says so and the grants
are skipped (a deployment creates them out of band, then re-runs the grants
with `alembic downgrade v2_0011 && alembic upgrade v2_0012`). Downgrade never
drops a role: another database in the cluster may use it.

Grants go to the tables named here, never `ALL TABLES IN SCHEMA`: that would
include partitions, and a partition read directly is not filtered by its
parent's policy. Partition maintenance creates new partitions with no grant.

Frozen literals, not imports, like every revision here; tests/test_rls_policy_map.py
fails if a table in the models is in neither list.

Revision ID: v2_0012
Revises: v2_0011
Create Date: 2026-09-28
"""
from alembic import op

revision = "v2_0012"
down_revision = "v2_0011"
branch_labels = None
depends_on = None

APP_ROLE, JOBS_ROLE = "tiq_app", "tiq_jobs"

# (a) owned by an agency: bank_id AND agency_id columns.
AGENCY_OWNED = (
    "collections.call_logs", "collections.cases", "collections.disputes", "collections.fraud_reviews",
    "collections.payments", "collections.placements", "collections.ptps", "collections.settlement_offers",
    "collections.visits", "lending.loan_dpd_history", "ml.model_predictions",
    "planning.allocation_decisions", "planning.allocation_runs", "planning.allocation_settings",
    "planning.beats", "tenancy.agency_contract_terms", "tenancy.agency_contracts",
    "tenancy.agency_documents", "tenancy.agency_regions", "tenancy.user_invites",
    "tenancy.user_sessions", "tenancy.users", "workforce.agent_devices", "workforce.agent_locations",
    "workforce.agent_performance", "workforce.agents", "workforce.leave_requests",
)
# (b) the bank's book, visible to an agency only through a placement: table -> its loan column.
VIA_PLACEMENT = {
    "lending.loans": "id",
    "lending.loan_instalments": "loan_id",
    "lending.bank_actions": "loan_id",
    "ml.repayment_score_snapshots": "loan_id",
}
# (b') customers: through any of their loans that is placed with the agency.
VIA_CUSTOMER_LOANS = ("lending.customers",)
# (c) the bank's alone.
BANK_ONLY = (
    "tenancy.regions", "tenancy.branches", "lending.bank_feed_batches", "lending.bank_feed_rows",
    "planning.placement_runs", "planning.placement_decisions",
)
# Tables with a policy of their own shape.
SPECIAL = ("tenancy.banks", "tenancy.agencies", "audit.audit_logs")
# Global or pre-authentication: no RLS.
NO_RLS = (
    "analytics.mv_refresh_log", "collections.collection_stages", "lending.bank_action_types",
    "lending.legal_statuses", "lending.settlement_statuses", "ml.model_candidates",
    "planning.allocation_objectives", "planning.allocation_outcomes", "planning.placement_outcomes",
    "tenancy.password_reset_tokens", "tenancy.permissions", "tenancy.role_permissions",
    "tenancy.used_quick_login_tokens",
)

HELPERS = """
CREATE OR REPLACE FUNCTION tenancy.current_bank_id() RETURNS uuid LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.bank_id', true), '')::uuid $$;
CREATE OR REPLACE FUNCTION tenancy.current_agency_id() RETURNS uuid LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.agency_id', true), '')::uuid $$;
CREATE OR REPLACE FUNCTION tenancy.current_scope() RETURNS text LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.scope', true), '') $$;
"""

_BANK = "bank_id = tenancy.current_bank_id()"
_BANK_SCOPE = "tenancy.current_scope() = 'BANK'"
_AGENCY_OWNED = f"({_BANK} AND ({_BANK_SCOPE} OR agency_id = tenancy.current_agency_id()))"


def _placed(loan_expr: str) -> str:
    return ("EXISTS (SELECT 1 FROM collections.placements p "
            f"WHERE p.loan_id = {loan_expr} AND p.agency_id = tenancy.current_agency_id())")


def _policies() -> dict[str, str]:
    """table -> the one expression used for both USING and WITH CHECK."""
    out = {t: _AGENCY_OWNED for t in AGENCY_OWNED}
    for t, col in VIA_PLACEMENT.items():
        out[t] = f"({_BANK} AND ({_BANK_SCOPE} OR {_placed(t.split('.')[1] + '.' + col)}))"
    for t in VIA_CUSTOMER_LOANS:
        out[t] = (f"({_BANK} AND ({_BANK_SCOPE} OR EXISTS (SELECT 1 FROM lending.loans l "
                  f"WHERE l.customer_id = customers.id AND {_placed('l.id')})))")
    for t in BANK_ONLY:
        out[t] = f"({_BANK} AND {_BANK_SCOPE})"
    out["tenancy.banks"] = "(id = tenancy.current_bank_id())"
    out["tenancy.agencies"] = f"({_BANK} AND ({_BANK_SCOPE} OR id = tenancy.current_agency_id()))"
    out["audit.audit_logs"] = (f"({_AGENCY_OWNED} OR (bank_id IS NULL AND tenancy.current_scope() = 'PLATFORM'))")
    return out


def _if_role(role: str, sql: str) -> str:
    body = sql.replace("'", "''")
    return (f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
            f"THEN EXECUTE '{body}'; END IF; END $$;")


def _grants() -> list[str]:
    domain = sorted(_policies())
    schemas = sorted({t.split(".")[0] for t in (*domain, *NO_RLS)} | {"analytics"})
    out = []
    for role in (APP_ROLE, JOBS_ROLE):
        for s in schemas:
            out.append(_if_role(role, f"GRANT USAGE ON SCHEMA {s} TO {role}"))
        for t in domain:
            if t == "audit.audit_logs":           # append-only for both: no UPDATE / DELETE / TRUNCATE
                out.append(_if_role(role, f"GRANT SELECT, INSERT ON {t} TO {role}"))
            else:
                out.append(_if_role(role, f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {role}"))
        for t in NO_RLS:
            out.append(_if_role(role, f"GRANT SELECT ON {t} TO {role}"))
    # Pre-auth token tables are written by the API; the jobs need nothing more than to read them.
    for t in ("tenancy.password_reset_tokens", "tenancy.used_quick_login_tokens"):
        out.append(_if_role(APP_ROLE, f"GRANT INSERT, UPDATE, DELETE ON {t} TO {APP_ROLE}"))
    # Analytics: the API reads the security_invoker views; the materialized views carry no policy.
    for v in ("dim_date", "dim_region", "dim_agency", "dim_agent", "dim_product", "dim_bucket",
              "v_case_360", "v_today_field_activity"):
        out.append(_if_role(APP_ROLE, f"GRANT SELECT ON analytics.{v} TO {APP_ROLE}"))
        out.append(_if_role(JOBS_ROLE, f"GRANT SELECT ON analytics.{v} TO {JOBS_ROLE}"))
    for mv in ("mv_collections_daily", "mv_field_activity_daily"):
        out.append(_if_role(JOBS_ROLE, f"GRANT SELECT ON analytics.{mv} TO {JOBS_ROLE}"))
    out.append(_if_role(JOBS_ROLE, f"GRANT INSERT, UPDATE ON analytics.mv_refresh_log TO {JOBS_ROLE}"))
    for fn in ("current_bank_id", "current_agency_id", "current_scope"):
        for role in (APP_ROLE, JOBS_ROLE):
            out.append(_if_role(role, f"GRANT EXECUTE ON FUNCTION tenancy.{fn}() TO {role}"))
    return out


ROLES = f"""
DO $$
DECLARE may_create boolean;
BEGIN
  SELECT rolsuper OR rolcreaterole INTO may_create FROM pg_roles WHERE rolname = current_user;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
    IF may_create THEN CREATE ROLE {APP_ROLE} NOLOGIN NOBYPASSRLS;
    ELSE RAISE NOTICE 'A13: role {APP_ROLE} absent and % may not create roles; grants skipped', current_user;
    END IF;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{JOBS_ROLE}') THEN
    IF may_create THEN CREATE ROLE {JOBS_ROLE} NOLOGIN BYPASSRLS;
    ELSE RAISE NOTICE 'A13: role {JOBS_ROLE} absent and % may not create roles; grants skipped', current_user;
    END IF;
  END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(HELPERS)
    for table, expr in _policies().items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY p_tenant ON {table} USING {expr} WITH CHECK {expr}")
    op.execute(ROLES)
    for stmt in _grants():
        op.execute(stmt)


def downgrade() -> None:
    for table in _policies():
        op.execute(f"DROP POLICY IF EXISTS p_tenant ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for role in (APP_ROLE, JOBS_ROLE):
        for s in sorted({t.split(".")[0] for t in (*_policies(), *NO_RLS)} | {"analytics"}):
            op.execute(_if_role(role, f"REVOKE ALL ON ALL TABLES IN SCHEMA {s} FROM {role}"))
            op.execute(_if_role(role, f"REVOKE USAGE ON SCHEMA {s} FROM {role}"))
        for fn in ("current_bank_id", "current_agency_id", "current_scope"):
            op.execute(_if_role(role, f"REVOKE ALL ON FUNCTION tenancy.{fn}() FROM {role}"))
    op.execute("DROP FUNCTION IF EXISTS tenancy.current_scope()")
    op.execute("DROP FUNCTION IF EXISTS tenancy.current_agency_id()")
    op.execute("DROP FUNCTION IF EXISTS tenancy.current_bank_id()")
