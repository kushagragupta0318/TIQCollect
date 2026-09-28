# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-09-28 — NEW (P2 G02, ce). "Manage Agents": create a field agent.
#
#   invite_service.can_invite() explicitly refuses FIELD_AGENT (own comment:
#   "an agent is a workforce.agents row with an employee code, a base and a
#   manager, which Manage Agents (G) creates") — an agent isn't a person
#   accepting an invite into a role that already fits the invite shape, it's
#   a User row AND an Agent row created together, with fields (base
#   location, employee code, ID card number, manager) an admin invite never
#   collects. This is that other path.
#
#   No password is ever chosen or seen by the creating manager: the account
#   is created with an unusable, discarded-immediately placeholder hash and
#   must_change_password=True, and activation (a one-time set-password link
#   sent by SMS) is a SEPARATE call — see create_agent's own docstring for
#   why it isn't in this transaction.
#
#   Tenant binding is never taken from the caller: bank_id/agency_id come
#   from `manager` (the authenticated principal, already capability-checked
#   by require_perm("agents.manage") at the route), never from the request
#   body, so a manager cannot create an agent in — or read one back from —
#   any agency but their own.
# ────────────────────────────────────────────────────────────────────────────
"""Create, and later edit/suspend/reactivate, a field agent (workforce.agents
+ its User row) on behalf of the manager who owns them."""
from __future__ import annotations

import re
import secrets

from fastapi import Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import stage_audit
from app.core.errors import AppException, ErrorCode
from app.core.geo import point_in_geojson_polygon
from app.core.security import hash_password
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.audit_log import AuditAction
from app.models.tenancy import Region
from app.models.user import User, UserRole
from app.services.invite_service import _normalise_email, _normalise_phone

_LAT_RANGE = (-90.0, 90.0)
_LON_RANGE = (-180.0, 180.0)


def _client_ip(request: Request | None) -> str | None:
    return request.client.host if request is not None and request.client else None


def _require_manager(principal: User) -> None:
    """Defence in depth behind require_perm("agents.manage"): the route
    dependency already restricts this capability to AGENCY_MANAGER /
    AGENCY_ADMIN (core/permissions.py), but a service that trusts its own
    caller's role without re-checking is how a future route wiring mistake
    turns into a tenant leak instead of a 403 at import time."""
    if principal.role not in (UserRole.AGENCY_MANAGER, UserRole.AGENCY_ADMIN):
        raise AppException(403, ErrorCode.FORBIDDEN, "Only a manager can manage agents.")
    if not principal.bank_id or not principal.agency_id:
        # Should not occur — ck_users_role_scope requires both for these
        # roles — but this is exactly the shape of gap the standalone plan's
        # own audits keep finding (a None where a constraint should have
        # made one impossible). Fail closed rather than create a row with a
        # NULL tenant.
        raise AppException(403, ErrorCode.FORBIDDEN, "Your account has no agency to create agents in.")


def _validate_employee_code(code: str) -> str:
    code = (code or "").strip().upper()
    if not code or len(code) > 20:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Enter an employee code of up to 20 characters.")
    return code


def _validate_id_card_number(number: str) -> str:
    number = (number or "").strip()
    if not number or len(number) > 50:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Enter an ID card number.")
    return number


def _validate_base_location(lat: float, lon: float, territory_region_id: str | None,
                            manager: User, db: Session) -> None:
    if not (_LAT_RANGE[0] <= lat <= _LAT_RANGE[1]) or not (_LON_RANGE[0] <= lon <= _LON_RANGE[1]):
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "The base location is not a valid latitude/longitude.")
    if territory_region_id is None:
        return
    region = db.get(Region, territory_region_id)
    if region is None or region.bank_id != manager.bank_id:
        # Uniform 404 shape (scope.py convention): a region that exists but
        # belongs to another bank must read identically to one that does
        # not exist at all.
        raise AppException(404, ErrorCode.NOT_FOUND, "Not found")
    # coverage_geojson is sparse today (most regions have none) — that is
    # "nothing to check against", not "reject everything". Only a region
    # that HAS drawn a boundary can fail this check.
    if region.coverage_geojson and not point_in_geojson_polygon(lat, lon, region.coverage_geojson):
        raise AppException(422, ErrorCode.VALIDATION_ERROR,
                           f"The base location is outside {region.name}'s coverage area.")


def create_agent(
    db: Session, manager: User, *,
    full_name: str, email: str, phone: str, employee_code: str, id_card_number: str,
    base_latitude: float, base_longitude: float, territory: str,
    territory_region_id: str | None = None, gender: str | None = None,
    specialization: AgentSpecialization | None = None, vehicle_type: str | None = None,
    max_cases_per_day: int | None = None, languages_spoken: list[str] | None = None,
    request: Request | None = None,
) -> dict:
    """Creates the User (role=FIELD_AGENT) and Agent rows in one transaction
    and stages the audit row alongside them, so the entity and its audit
    trail commit together or not at all.

    Deliberately does NOT send the activation link itself — issuing that
    link is a second call (password_service, once its issue_first_password
    lands) so a notification-delivery failure can never roll back an agent
    that was, in every way that matters to the database, successfully
    created. The caller (the route) makes both calls and reports whichever
    of them actually failed, rather than one masking the other.
    """
    _require_manager(manager)

    full_name = (full_name or "").strip()
    if not full_name:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Enter the agent's full name.")
    email = _normalise_email(email)
    if not email or "@" not in email:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Enter a valid email address.")
    phone = _normalise_phone(phone)
    employee_code = _validate_employee_code(employee_code)
    id_card_number = _validate_id_card_number(id_card_number)
    territory = (territory or "").strip()
    if not territory:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Enter the agent's territory.")
    _validate_base_location(base_latitude, base_longitude, territory_region_id, manager, db)

    if db.query(User.id).filter((User.email == email) | (User.phone == phone)).first():
        raise AppException(409, ErrorCode.CONFLICT, "An account with this email or phone already exists.")

    user = User(
        email=email, phone=phone, full_name=full_name, role=UserRole.FIELD_AGENT,
        bank_id=manager.bank_id, agency_id=manager.agency_id,
        # Unusable and discarded immediately — nobody, including this
        # process a moment from now, knows this value. The agent's real
        # first password is chosen by them, through the activation link.
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        is_active=True, is_verified=True, must_change_password=True,
    )
    db.add(user)
    db.flush()

    agent = Agent(
        user_id=user.id, employee_code=employee_code, id_card_number=id_card_number,
        manager_user_id=manager.id, bank_id=manager.bank_id, agency_id=manager.agency_id,
        base_latitude=base_latitude, base_longitude=base_longitude, territory=territory,
        territory_region_id=territory_region_id, gender=gender,
        specialization=specialization or AgentSpecialization.BOTH,
        vehicle_type=vehicle_type or "TWO_WHEELER",
        max_cases_per_day=max_cases_per_day or 15,
        languages_spoken=languages_spoken or [], status=AgentStatus.OFF_DUTY, tier=AgentTier.TIER_3,
    )
    db.add(agent)
    try:
        # One try/except from here through the commit: the (agency_id,
        # employee_code) and (bank_id, id_card_number) uniqueness constraints
        # can only be discovered by the database, and the first of the two
        # flushes below (to get agent.id for the audit row) is exactly where
        # SQLite raises them — catching only around commit() missed it.
        db.flush()
        stage_audit(db, action=AuditAction.USER_CREATED, user_id=manager.id, entity_type="Agent",
                   entity_id=agent.id, ip_address=_client_ip(request),
                   details={"role": "FIELD_AGENT", "via": "manager_created", "agent_user_id": user.id,
                            "employee_code": employee_code, "agency_id": manager.agency_id})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppException(409, ErrorCode.CONFLICT,
                           "This employee code or ID card number is already in use.")

    return {
        "agent_id": agent.id, "user_id": user.id, "employee_code": agent.employee_code,
        "full_name": user.full_name, "email": user.email, "phone": user.phone,
        "territory": agent.territory, "status": agent.status.value,
    }
