"""RLS step 2a (A13b S1b): pre-authentication lookups, the tenantless refusal
writer, PLATFORM's own rows, and the audit tenant backfill.

DATA-MODEL-V2 §8.4 and §8.6. Nothing here changes how anything connects: the
API still logs in as the table owner, which bypasses RLS until step 2 (S3).

1. Role `tiq_auth` (NOLOGIN BYPASSRLS): owns the SECURITY DEFINER functions
   and holds SELECT on exactly the columns they read. A definer owned by the
   table owner would be held to FORCE'd policies with no tenant set, and find
   nobody (the S1 audit, H3). Created only when the migrating user is a
   superuser (BYPASSRLS needs one); otherwise a NOTICE, and the functions stay
   owned by the migrating user, which works until FORCE (S4).
2. Five lookups, each returning a principal's TENANT only
   (user_id, bank_id, agency_id, role), for app/core/preauth.py:
   by user id, by email, by phone, by agent id, and an invitation by its token
   hash. EXECUTE is revoked from PUBLIC and granted to tiq_app.
3. audit.auth_write_refusal: the one way to write an audit row with no user
   and no tenant (a pre-authentication refusal). It takes only success=false
   rows of the actions it lists, and never a tenant, so it cannot write into a
   bank's trail.
4. tenancy.users and tenancy.user_sessions: PLATFORM sees ITS OWN row
   (bank_id NULL, id = app.user_id). v2_0012's template hid a platform
   admin's own user and session rows, so it could not sign in (the S1 audit,
   H4). PLATFORM still reads no bank's rows (Q20).
5. Backfill of audit_logs rows written with no tenant before A13b S1a: the
   actor's tenant, then the entity's, then the entity's agency for a bank
   actor's row about an agency's entity (the rule S1b applies at write).
   Rows copied from v1 already carry the demo tenant (the transform's copy()).
   Only NULL tenant columns are written; nothing else in a row changes.

Frozen literals, not imports, like every revision here. The policy
expressions restate v2_0012's template text on purpose.

Revision ID: v2_0019
Revises: v2_0018
Create Date: 2026-09-30
"""
from alembic import op

revision = "v2_0019"
down_revision = "v2_0018"
branch_labels = None
depends_on = None

AUTH_ROLE, APP_ROLE = "tiq_auth", "tiq_app"

CURRENT_USER_ID = """
CREATE OR REPLACE FUNCTION tenancy.current_user_id() RETURNS uuid LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;
"""

_AGENCY_OWNED = ("(bank_id = tenancy.current_bank_id() AND (tenancy.current_scope() = 'BANK' "
                 "OR agency_id = tenancy.current_agency_id()))")
_PLATFORM_SELF = "(bank_id IS NULL AND tenancy.current_scope() = 'PLATFORM' AND {col} = tenancy.current_user_id())"
# table -> (the step-1 expression, this revision's); tests read both.
POLICIES_REPLACED = {
    "tenancy.users": (_AGENCY_OWNED, f"({_AGENCY_OWNED} OR {_PLATFORM_SELF.format(col='id')})"),
    "tenancy.user_sessions": (_AGENCY_OWNED, f"({_AGENCY_OWNED} OR {_PLATFORM_SELF.format(col='user_id')})"),
}

_PRINCIPAL = "RETURNS TABLE (user_id uuid, bank_id uuid, agency_id uuid, role text)"
_DEFINER = "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp"
# signature -> body. Each reads only the columns granted to tiq_auth below.
LOOKUPS = {
    "tenancy.auth_principal_by_id(p_user_id uuid)":
        "SELECT u.id, u.bank_id, u.agency_id, u.role::text FROM tenancy.users u WHERE u.id = p_user_id",
    "tenancy.auth_principal_by_email(p_email text)":
        "SELECT u.id, u.bank_id, u.agency_id, u.role::text FROM tenancy.users u WHERE u.email = p_email",
    "tenancy.auth_principal_by_phone(p_phones text[])":
        ("SELECT u.id, u.bank_id, u.agency_id, u.role::text FROM tenancy.users u "
         "WHERE u.phone = ANY (p_phones) ORDER BY u.id LIMIT 1"),
    "tenancy.auth_principal_by_agent(p_agent_id uuid)":
        ("SELECT u.id, u.bank_id, u.agency_id, u.role::text FROM workforce.agents a "
         "JOIN tenancy.users u ON u.id = a.user_id WHERE a.id = p_agent_id"),
    "tenancy.auth_invitee_by_token(p_token_sha256 text)":
        ("SELECT NULL::uuid, i.bank_id, i.agency_id, i.role::text FROM tenancy.user_invites i "
         "WHERE i.token_sha256 = p_token_sha256"),
}
REFUSAL_ACTIONS = ("LOGIN_FAILED", "VOICE_CALL_REFUSED")
REFUSAL_SIGNATURE = ("audit.auth_write_refusal(p_action text, p_success boolean, p_entity_type text, "
                     "p_entity_id text, p_failure_reason text, p_ip text, p_user_agent text, p_details jsonb)")
REFUSAL_WRITER = f"""
CREATE FUNCTION {REFUSAL_SIGNATURE} RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF p_success IS DISTINCT FROM false THEN
    RAISE EXCEPTION 'auth_write_refusal writes refusals only';
  END IF;
  IF p_action IS NULL OR p_action NOT IN ({", ".join(f"'{a}'" for a in REFUSAL_ACTIONS)}) THEN
    RAISE EXCEPTION 'auth_write_refusal: % is not a pre-authentication refusal', p_action;
  END IF;
  INSERT INTO audit.audit_logs (id, created_at, user_id, bank_id, agency_id, action, entity_type, entity_id,
                                ip_address, user_agent, details, success, failure_reason)
  VALUES (gen_random_uuid(), now(), NULL, NULL, NULL, p_action::public.audit_action_enum,
          left(p_entity_type, 50), left(p_entity_id, 50), left(p_ip, 45), left(p_user_agent, 500),
          p_details, false, p_failure_reason);
END $$;
"""
# What tiq_auth may read and write: the lookups' columns, and the refusal insert.
AUTH_GRANTS = (
    ("USAGE", "SCHEMA tenancy"), ("USAGE", "SCHEMA workforce"), ("USAGE", "SCHEMA audit"),
    ("SELECT (id, email, phone, bank_id, agency_id, role)", "tenancy.users"),
    ("SELECT (id, user_id)", "workforce.agents"),
    ("SELECT (token_sha256, bank_id, agency_id, role)", "tenancy.user_invites"),
    ("INSERT", "audit.audit_logs"),
)

ROLE = f"""
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{AUTH_ROLE}') THEN
    IF (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
      BEGIN
        CREATE ROLE {AUTH_ROLE} NOLOGIN NOINHERIT BYPASSRLS;
      EXCEPTION WHEN duplicate_object THEN
        NULL;   -- another database in the cluster created it a moment ago
      END;
    ELSE
      RAISE NOTICE 'A13b: role {AUTH_ROLE} absent and % is not a superuser; definers stay owned by %',
        current_user, current_user;
    END IF;
  END IF;
END $$;
"""

# The backfill: NULL tenant columns only, in this order.
BACKFILL = (
    # 1. an actor's row: the actor's tenant
    """UPDATE audit.audit_logs a SET bank_id = u.bank_id, agency_id = coalesce(a.agency_id, u.agency_id)
       FROM tenancy.users u WHERE a.user_id = u.id AND a.bank_id IS NULL AND u.bank_id IS NOT NULL""",
    # 2. a row with no tenant yet (no user, or a PLATFORM actor): its entity's
    *(f"""UPDATE audit.audit_logs a SET bank_id = e.bank_id, agency_id = coalesce(a.agency_id, {agency})
         FROM {table} e WHERE a.bank_id IS NULL AND lower(a.entity_type) = '{etype}' AND a.entity_id = e.id::text"""
      for etype, table, agency in (
          ("ptp", "collections.ptps", "e.agency_id"), ("placement", "collections.placements", "e.agency_id"),
          ("case", "collections.cases", "e.agency_id"), ("visit", "collections.visits", "e.agency_id"),
          ("payment", "collections.payments", "e.agency_id"), ("agency", "tenancy.agencies", "e.id"),
          ("agencydocument", "tenancy.agency_documents", "e.agency_id"),
          ("userinvite", "tenancy.user_invites", "e.agency_id"), ("user", "tenancy.users", "e.agency_id"))),
    # 3. a bank actor's row about an agency's entity: that agency too
    *(f"""UPDATE audit.audit_logs a SET agency_id = {agency}
         FROM {table} e WHERE a.agency_id IS NULL AND a.bank_id = e.bank_id AND lower(a.entity_type) = '{etype}'
           AND a.entity_id = e.id::text AND {agency} IS NOT NULL"""
      for etype, table, agency in (
          ("placement", "collections.placements", "e.agency_id"), ("agency", "tenancy.agencies", "e.id"),
          ("agencydocument", "tenancy.agency_documents", "e.agency_id"),
          ("userinvite", "tenancy.user_invites", "e.agency_id"), ("user", "tenancy.users", "e.agency_id"))),
)


def _if_role(role: str, sql: str) -> str:
    body = sql.replace("'", "''")
    return (f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
            f"THEN EXECUTE '{body}'; END IF; END $$;")


def _replace_policy(table: str, expr: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS p_tenant ON {table}")
    op.execute(f"CREATE POLICY p_tenant ON {table} USING {expr} WITH CHECK {expr}")


def _functions() -> list[str]:
    return [*LOOKUPS, REFUSAL_SIGNATURE]


def upgrade() -> None:
    op.execute(ROLE)
    for priv, obj in AUTH_GRANTS:
        op.execute(_if_role(AUTH_ROLE, f"GRANT {priv} ON {obj} TO {AUTH_ROLE}"))
    op.execute(CURRENT_USER_ID)
    op.execute(_if_role(APP_ROLE, f"GRANT EXECUTE ON FUNCTION tenancy.current_user_id() TO {APP_ROLE}"))
    for signature, body in LOOKUPS.items():
        op.execute(f"CREATE FUNCTION {signature} {_PRINCIPAL} {_DEFINER} AS $$ {body} $$")
    op.execute(REFUSAL_WRITER)
    for signature in _functions():
        op.execute(_if_role(AUTH_ROLE, f"ALTER FUNCTION {signature} OWNER TO {AUTH_ROLE}"))
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(_if_role(APP_ROLE, f"GRANT EXECUTE ON FUNCTION {signature} TO {APP_ROLE}"))
    for table, (_, expr) in POLICIES_REPLACED.items():
        _replace_policy(table, expr)
    for stmt in BACKFILL:
        op.execute(stmt)


def downgrade() -> None:
    """The backfilled tenant columns stay: they are correct, and step 1 reads them the same way."""
    for table, (expr, _) in POLICIES_REPLACED.items():
        _replace_policy(table, expr)
    for signature in reversed(_functions()):
        op.execute(f"DROP FUNCTION IF EXISTS {signature.split('(')[0]}({_arg_types(signature)})")
    op.execute("DROP FUNCTION IF EXISTS tenancy.current_user_id()")
    for priv, obj in reversed(AUTH_GRANTS):
        op.execute(_if_role(AUTH_ROLE, f"REVOKE {priv} ON {obj} FROM {AUTH_ROLE}"))


def _arg_types(signature: str) -> str:
    """'f(p_a uuid, p_b text[])' -> 'uuid, text[]'."""
    args = signature.split("(", 1)[1].rsplit(")", 1)[0]
    return ", ".join(a.strip().split(" ", 1)[1] for a in args.split(",") if a.strip())
