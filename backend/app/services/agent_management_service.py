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

from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import stage_audit
from app.core.errors import AppException, ErrorCode
from app.core.geo import point_in_geojson_polygon
from app.core.security import disabled_password_hash
from app.models.agent import AGENT_GENDER_VALUES, VEHICLE_TYPES, Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.audit_log import AuditAction
from app.models.tenancy import Region
from app.models.user import User, UserRole
from app.services.invite_service import _normalise_email, _normalise_phone
from app.services.scope import agents_in_scope

_LAT_RANGE = (-90.0, 90.0)
_LON_RANGE = (-180.0, 180.0)
_MAX_LANGUAGES = 10
_MAX_LANGUAGE_LENGTH = 30
_MAX_CASES_PER_DAY_RANGE = (1, 50)
# coordinator audit MED: a collision is refused the same way regardless of
# WHICH field collided — naming email vs. phone vs. employee_code vs. ID
# card number would let a caller probe, one field at a time, whether a
# specific value already exists somewhere in the system (across tenants for
# email/phone, since that uniqueness is intentionally global — see
# create_agent's own note on the pre-check below).
_CONFLICT_MESSAGE = "This could not be created — a detail collides with an existing record."


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


def _validate_gender(gender: str | None) -> None:
    if gender is not None and gender.strip().upper() not in AGENT_GENDER_VALUES:
        # Matches Agent's own CHECK constraint (ck_agents_gender) — Postgres
        # enforces it, SQLite does not, same shape of gap as the length caps
        # elsewhere in this file.
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "That gender value is not recognised.")


def _validate_vehicle_type(vehicle_type: str | None) -> None:
    if vehicle_type is not None and vehicle_type.strip().upper() not in VEHICLE_TYPES:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "That vehicle type is not recognised.")


def _validate_max_cases_per_day(value: int | None) -> None:
    if value is not None and not (_MAX_CASES_PER_DAY_RANGE[0] <= value <= _MAX_CASES_PER_DAY_RANGE[1]):
        raise AppException(422, ErrorCode.VALIDATION_ERROR,
                           f"Cases per day must be between {_MAX_CASES_PER_DAY_RANGE[0]} "
                           f"and {_MAX_CASES_PER_DAY_RANGE[1]}.")


def _validate_languages_spoken(languages_spoken: list[str] | None) -> None:
    if not languages_spoken:
        return
    if len(languages_spoken) > _MAX_LANGUAGES:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, f"Enter up to {_MAX_LANGUAGES} languages.")
    if any(not isinstance(lang, str) or not lang.strip() or len(lang) > _MAX_LANGUAGE_LENGTH
          for lang in languages_spoken):
        raise AppException(422, ErrorCode.VALIDATION_ERROR,
                           f"Each language must be text of up to {_MAX_LANGUAGE_LENGTH} characters.")


def _validate_territory(territory: str) -> str:
    territory = (territory or "").strip()
    if not territory or len(territory) > 100:   # Agent.territory: String(100)
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Enter the agent's territory (up to 100 characters).")
    return territory


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
    # Matches User.full_name's own column width (String(200)) — Postgres
    # enforces it and raises a raw DB error on INSERT; SQLite does not
    # (VARCHAR(n) is unenforced there), which is exactly the shape of gap
    # that stays invisible in this suite's sqlite tests and only surfaces
    # against real Postgres. Checked here so it is a clean 422 either way.
    if not full_name or len(full_name) > 200:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Enter the agent's full name (up to 200 characters).")
    email = _normalise_email(email)
    if not email or "@" not in email:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Enter a valid email address.")
    phone = _normalise_phone(phone)
    employee_code = _validate_employee_code(employee_code)
    id_card_number = _validate_id_card_number(id_card_number)
    territory = _validate_territory(territory)
    _validate_gender(gender)
    _validate_vehicle_type(vehicle_type)
    _validate_max_cases_per_day(max_cases_per_day)
    _validate_languages_spoken(languages_spoken)
    _validate_base_location(base_latitude, base_longitude, territory_region_id, manager, db)

    # Global, not agency-scoped, ON PURPOSE: one login per identity across
    # the whole deployment (the same rule invite_service.create_agent
    # relies on for its own email/phone check). Because it is global, the
    # message says nothing about which field collided or with whose
    # account — the alternative would let a caller probe, one field at a
    # time, whether a given email or phone exists in ANOTHER tenant, which
    # a manager has no legitimate reason to learn.
    if db.query(User.id).filter((User.email == email) | (User.phone == phone)).first():
        raise AppException(409, ErrorCode.CONFLICT, _CONFLICT_MESSAGE)

    user = User(
        email=email, phone=phone, full_name=full_name, role=UserRole.FIELD_AGENT,
        bank_id=manager.bank_id, agency_id=manager.agency_id,
        # The one recognised "no real password" marker (core/security.
        # disabled_password_hash), not a throwaway random hash — the agent's
        # real first password is chosen by them, through the activation link.
        hashed_password=disabled_password_hash(),
        is_active=True, is_verified=True, must_change_password=True,
    )
    db.add(user)
    try:
        # One try/except from HERE — the User's own flush is a genuine race
        # window (tiq-auditor's HIGH finding: two concurrent calls with the
        # same email/phone can both clear the pre-check above and then race
        # each other at this flush) — through the final commit. Every DB-
        # discoverable uniqueness violation in this function (User.email,
        # User.phone, the composite (agency_id, employee_code) and
        # (bank_id, id_card_number) on Agent) is caught the same way.
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
        db.flush()
        stage_audit(db, action=AuditAction.USER_CREATED, user_id=manager.id, entity_type="Agent",
                   entity_id=agent.id, ip_address=_client_ip(request),
                   details={"role": "FIELD_AGENT", "via": "manager_created", "agent_user_id": user.id,
                            "employee_code": employee_code, "agency_id": manager.agency_id})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppException(409, ErrorCode.CONFLICT, _CONFLICT_MESSAGE)

    return {
        "agent_id": agent.id, "user_id": user.id, "employee_code": agent.employee_code,
        "full_name": user.full_name, "email": user.email, "phone": user.phone,
        "territory": agent.territory, "status": agent.status.value,
    }


def _agent_in_scope_or_404(db: Session, manager: User, agent_id: str) -> Agent:
    """The uniform 404 (scope.py convention): an agent of another agency, or
    another manager's agent under the SAME agency (agents_in_scope for
    AGENCY_MANAGER is manager_user_id-bound, not agency-wide — see its own
    docstring), reads identically to an id that does not exist at all."""
    agent = agents_in_scope(db, manager).filter(Agent.id == agent_id).first()
    if agent is None:
        raise AppException(404, ErrorCode.NOT_FOUND, "Not found")
    return agent


def edit_agent(
    db: Session, manager: User, agent_id: str, *,
    full_name: str | None = None, phone: str | None = None, territory: str | None = None,
    base_latitude: float | None = None, base_longitude: float | None = None,
    territory_region_id: str | None = None, gender: str | None = None,
    specialization: AgentSpecialization | None = None, vehicle_type: str | None = None,
    max_cases_per_day: int | None = None, languages_spoken: list[str] | None = None,
    request: Request | None = None,
) -> dict:
    """A partial update: every field is optional, and one left out (None)
    keeps its current value rather than being cleared — there is no way to
    blank out an agent's gender or territory_region_id through this call,
    which matches a pre-filled edit form always sending the field it wants
    changed, not "unset this".

    Deliberately narrower than create_agent's field set: email,
    employee_code, id_card_number and the agency/bank/manager binding are
    NOT editable here. employee_code/id_card_number are identity documents
    (re-issuing one is a different, rarer action than a day-to-day edit);
    email is the account's login identity, changing it wants its own
    verification step this endpoint does not have; manager reassignment
    ("transfer manager") is G01's, not G02's.

    base_latitude and base_longitude must both be given together or not at
    all — validating one against the region's coverage polygon while
    silently keeping the other's old value would check a point that is not
    actually the agent's new base."""
    agent = _agent_in_scope_or_404(db, manager, agent_id)
    agent_user = db.get(User, agent.user_id)

    changes: dict[str, tuple] = {}   # field -> (old, new), for the audit row

    if full_name is not None:
        full_name = full_name.strip()
        if not full_name or len(full_name) > 200:
            raise AppException(422, ErrorCode.VALIDATION_ERROR,
                               "Enter the agent's full name (up to 200 characters).")
        if full_name != agent_user.full_name:
            changes["full_name"] = (agent_user.full_name, full_name)
            agent_user.full_name = full_name

    if phone is not None:
        phone = _normalise_phone(phone)
        if phone != agent_user.phone:
            if db.query(User.id).filter(User.phone == phone, User.id != agent_user.id).first():
                raise AppException(409, ErrorCode.CONFLICT, _CONFLICT_MESSAGE)
            changes["phone"] = (agent_user.phone, phone)
            agent_user.phone = phone

    if territory is not None:
        territory = _validate_territory(territory)
        if territory != agent.territory:
            changes["territory"] = (agent.territory, territory)
            agent.territory = territory

    if gender is not None:
        _validate_gender(gender)
        gender = gender.strip().upper()
        if gender != agent.gender:
            changes["gender"] = (agent.gender, gender)
            agent.gender = gender

    if vehicle_type is not None:
        _validate_vehicle_type(vehicle_type)
        vehicle_type = vehicle_type.strip().upper()
        if vehicle_type != agent.vehicle_type:
            changes["vehicle_type"] = (agent.vehicle_type, vehicle_type)
            agent.vehicle_type = vehicle_type

    if specialization is not None and specialization != agent.specialization:
        changes["specialization"] = (agent.specialization.value, specialization.value)
        agent.specialization = specialization

    if max_cases_per_day is not None:
        _validate_max_cases_per_day(max_cases_per_day)
        if max_cases_per_day != agent.max_cases_per_day:
            changes["max_cases_per_day"] = (agent.max_cases_per_day, max_cases_per_day)
            agent.max_cases_per_day = max_cases_per_day

    if languages_spoken is not None:
        _validate_languages_spoken(languages_spoken)
        if languages_spoken != agent.languages_spoken:
            changes["languages_spoken"] = (agent.languages_spoken, languages_spoken)
            agent.languages_spoken = languages_spoken

    if base_latitude is not None or base_longitude is not None:
        if base_latitude is None or base_longitude is None:
            raise AppException(422, ErrorCode.VALIDATION_ERROR,
                               "Give both a latitude and a longitude for the new base location.")
        new_region_id = territory_region_id if territory_region_id is not None else agent.territory_region_id
        _validate_base_location(base_latitude, base_longitude, new_region_id, manager, db)
        if base_latitude != agent.base_latitude or base_longitude != agent.base_longitude:
            changes["base_location"] = ((agent.base_latitude, agent.base_longitude), (base_latitude, base_longitude))
            agent.base_latitude = base_latitude
            agent.base_longitude = base_longitude

    if territory_region_id is not None and territory_region_id != agent.territory_region_id:
        # Re-validated even when the location itself did not change in this
        # call — a manager moving the agent to a new region without also
        # moving the pin must still pass that region's coverage check
        # against the EXISTING base location.
        if base_latitude is None and base_longitude is None:
            _validate_base_location(agent.base_latitude, agent.base_longitude, territory_region_id, manager, db)
        changes["territory_region_id"] = (agent.territory_region_id, territory_region_id)
        agent.territory_region_id = territory_region_id

    if not changes:
        return {"agent_id": agent.id, "changed": []}

    try:
        stage_audit(db, action=AuditAction.AGENT_UPDATED, user_id=manager.id, entity_type="Agent",
                   entity_id=agent.id, ip_address=_client_ip(request),
                   details={"changed": {k: {"from": v[0], "to": v[1]} for k, v in changes.items()}})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppException(409, ErrorCode.CONFLICT, _CONFLICT_MESSAGE)

    return {"agent_id": agent.id, "changed": sorted(changes.keys())}


def suspend_agent(db: Session, manager: User, agent_id: str, *, reason: str,
                  request: Request | None = None) -> dict:
    """Suspends the agent and ends every session they currently hold — a
    suspended agent must not keep working from a tab that is already open.
    "ADMIN_REVOKED" is the closest fit in the fixed session-revoke-reason
    vocabulary (identity.SESSION_REVOKE_REASONS, DB CHECK-constrained,
    migrated separately) — adding a dedicated AGENT_SUSPENDED reason is a
    schema change and goes to 43 first if it's ever wanted."""
    agent = _agent_in_scope_or_404(db, manager, agent_id)
    reason = (reason or "").strip()
    if not reason:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Enter a reason for the suspension.")
    if len(reason) > 500:   # suspended_reason is Text (unbounded in Postgres) — capped here, not by the column
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Keep the suspension reason under 500 characters.")
    if agent.status == AgentStatus.SUSPENDED:
        raise AppException(409, ErrorCode.CONFLICT, "This agent is already suspended.")

    from app.services.auth_service import revoke_user_sessions

    previous_status = agent.status
    agent.status = AgentStatus.SUSPENDED
    agent.suspended_at = datetime.now(timezone.utc)
    agent.suspended_reason = reason
    # coordinator audit HIGH: Agent.status alone did not stop a sign-in —
    # auth_service checks only User.is_active, and login/refresh's own
    # belt-and-braces Agent.status check exists for exactly the case where
    # these two columns ever disagree, not as a substitute for keeping them
    # in step here.
    agent_user = db.get(User, agent.user_id)
    agent_user.is_active = False
    revoke_user_sessions(db, agent.user_id, "ADMIN_REVOKED", by=manager.id)
    # stage_audit, not write_audit (coordinator audit MED): the audit row
    # belongs in the SAME commit as the status change, not a second one
    # after it — a crash between the two used to leave a suspension with no
    # audit trail at all.
    stage_audit(db, action=AuditAction.AGENT_STATUS_CHANGED, user_id=manager.id, entity_type="Agent",
               entity_id=agent.id, ip_address=_client_ip(request),
               details={"event": "AGENT_SUSPENDED", "reason": reason,
                        "from_status": previous_status.value, "to_status": AgentStatus.SUSPENDED.value})
    db.commit()
    return {"agent_id": agent.id, "status": agent.status.value, "suspended_reason": agent.suspended_reason}


def reactivate_agent(db: Session, manager: User, agent_id: str, *, request: Request | None = None) -> dict:
    """Reactivation always lands on OFF_DUTY, never back on whatever the
    agent was doing before the suspension — they must check in again, the
    same as any agent who has not started their day yet. Nothing about the
    account's credentials changes here; if the agent also needs a fresh
    login, that is the separate reset-login action."""
    agent = _agent_in_scope_or_404(db, manager, agent_id)
    if agent.status != AgentStatus.SUSPENDED:
        raise AppException(409, ErrorCode.CONFLICT, "This agent is not suspended.")

    agent.status = AgentStatus.OFF_DUTY
    agent.suspended_at = None
    agent.suspended_reason = None
    agent_user = db.get(User, agent.user_id)
    agent_user.is_active = True
    stage_audit(db, action=AuditAction.AGENT_STATUS_CHANGED, user_id=manager.id, entity_type="Agent",
               entity_id=agent.id, ip_address=_client_ip(request),
               details={"event": "AGENT_REACTIVATED", "from_status": AgentStatus.SUSPENDED.value,
                        "to_status": AgentStatus.OFF_DUTY.value})
    db.commit()
    return {"agent_id": agent.id, "status": agent.status.value}


def reset_agent_login(db: Session, manager: User, agent_id: str, *,
                      request: Request | None = None) -> dict:
    """"Reset Login": end the agent's current sessions and text them a fresh
    set-password link — password_service.admin_reset, not
    issue_first_password. The two look alike (same {sent, expires_at}, same
    can_manage gate, same "nobody but the agent ever sees a password") but
    issue_first_password is for a brand-new account and now 409s on
    anything else (coordinator's audit of 12c3232: a "first" password on an
    ESTABLISHED account would spend its open tokens and leave its sessions
    running — that is a reset). An agent who has ever signed in reaches
    this action, so admin_reset is the one that fits."""
    agent = _agent_in_scope_or_404(db, manager, agent_id)
    from app.services import password_service
    return password_service.admin_reset(db, manager, agent.user_id, request=request)
