"""Who is this, before row-level security lets the API read anything.

A pre-authentication route must find a user before any tenant is bound:
login, refresh, a token link, the public ID-card check, a Twilio webhook.
As tiq_app, RLS then reads nothing (DATA-MODEL-V2 §8.4). Each lookup here
calls one narrow SECURITY DEFINER function (alembic v2_0026) that returns the
principal's TENANT and nothing else. The caller `bind`s it and then reads the
row itself, under RLS, exactly as before. SQLite (the test suite) has neither
RLS nor functions, so it reads the same columns directly.

    principal = preauth.by_email(db, email)
    if principal is None: ...refuse...
    preauth.bind(db, principal)
    user = db.query(User).filter(User.email == email).first()   # now visible
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import apply_tenant_context
from app.core.ids import parse_uuid
from app.models.user import User, UserRole, tenant_scope


@dataclass(frozen=True)
class Principal:
    user_id: str | None          # None for an invitee: the account does not exist yet
    bank_id: str | None
    agency_id: str | None
    role: UserRole

    @property
    def scope(self) -> str:
        return tenant_scope(self.role, self.agency_id)


def _pg(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def _call(db: Session, fn: str, **params) -> Principal | None:
    """`fn` is one of the literal calls below, never caller input."""
    row = db.execute(text(f"SELECT user_id::text, bank_id::text, agency_id::text, role FROM {fn}"),
                     params).first()
    return Principal(row[0], row[1], row[2], UserRole(row[3])) if row else None


def _of(user: User | None) -> Principal | None:
    return Principal(user.id, user.bank_id, user.agency_id, user.role) if user is not None else None


def by_user_id(db: Session, user_id) -> Principal | None:
    uid = parse_uuid(user_id)
    if uid is None:
        return None
    if _pg(db):
        return _call(db, "tenancy.auth_principal_by_id(CAST(:v AS uuid))", v=uid)
    return _of(db.get(User, uid))


def by_email(db: Session, email: str | None) -> Principal | None:
    """Exact match: the caller normalises first, as its own query does."""
    if not email:
        return None
    if _pg(db):
        return _call(db, "tenancy.auth_principal_by_email(:v)", v=email)
    return _of(db.query(User).filter(User.email == email).first())


def by_phone(db: Session, phones: list[str]) -> Principal | None:
    """Any of the given spellings; the lowest id wins if two accounts match."""
    phones = sorted({p for p in phones if p})
    if not phones:
        return None
    if _pg(db):
        return _call(db, "tenancy.auth_principal_by_phone(CAST(:v AS text[]))", v=phones)
    return _of(db.query(User).filter(User.phone.in_(phones)).order_by(User.id).first())


def by_agent_id(db: Session, agent_id) -> Principal | None:
    """The agent's own user (the public ID-card check)."""
    aid = parse_uuid(agent_id)
    if aid is None:
        return None
    if _pg(db):
        return _call(db, "tenancy.auth_principal_by_agent(CAST(:v AS uuid))", v=aid)
    from app.models.agent import Agent
    agent = db.get(Agent, aid)
    return _of(db.get(User, agent.user_id)) if agent is not None else None


def invitee_by_token_sha(db: Session, token_sha: str) -> Principal | None:
    """The tenant and role an invitation is for; user_id is None."""
    if not token_sha:
        return None
    if _pg(db):
        return _call(db, "tenancy.auth_invitee_by_token(:v)", v=token_sha)
    from app.models.identity import UserInvite
    inv = db.query(UserInvite).filter(UserInvite.token_sha256 == token_sha).first()
    return Principal(None, inv.bank_id, inv.agency_id, inv.role) if inv is not None else None


def bind(db: Session, principal: Principal) -> None:
    """This session's transactions now carry the principal's tenant, as an
    authenticated request's do (dependencies.get_current_user)."""
    apply_tenant_context(db, bank_id=principal.bank_id, agency_id=principal.agency_id,
                         scope=principal.scope, user_id=principal.user_id)
