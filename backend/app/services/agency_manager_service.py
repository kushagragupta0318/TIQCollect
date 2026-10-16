"""Agency-admin management of the agency's AGENCY_MANAGERs: list, and add.

An AGENCY_ADMIN (e.g. the agency's master login) owns the managers in their own
agency. `can_invite` (invite_service) is the one authority for who may create a
manager — this service defers to it rather than restating the rule.

Creation mirrors agent_management_service.create_agent:
  * the real/prod path issues an INVITE (the manager sets their own password via
    a link);
  * in DEMO_MODE a local demo can't deliver that link, so the manager is created
    directly with a known password handed back to the admin — strictly gated,
    the same demo convenience the Add Agent flow uses.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fastapi import Request

from app.core.audit import stage_audit
from app.core.config import settings
from app.core.errors import AppException, ErrorCode
from app.core.security import hash_password
from app.models.agent import Agent
from app.models.audit_log import AuditAction
from app.models.user import User, UserRole
from app.services import invite_service


def _require_agency_admin(user: User) -> None:
    if user.role != UserRole.AGENCY_ADMIN or not user.agency_id:
        raise AppException(403, ErrorCode.FORBIDDEN, "Only an agency administrator can manage managers.")


def _client_ip(request: Request | None) -> str | None:
    return request.client.host if request and request.client else None


def list_managers(db: Session, admin: User) -> list[dict]:
    """Every AGENCY_MANAGER in the admin's own agency, with their agent count."""
    _require_agency_admin(admin)
    agent_counts = dict(
        db.query(Agent.manager_user_id, func.count(Agent.id))
        .filter(Agent.agency_id == admin.agency_id)
        .group_by(Agent.manager_user_id)
        .all()
    )
    managers = (
        db.query(User)
        .filter(User.agency_id == admin.agency_id, User.role == UserRole.AGENCY_MANAGER)
        .order_by(User.full_name)
        .all()
    )
    return [
        {
            "id": m.id, "full_name": m.full_name, "email": m.email, "phone": m.phone,
            "is_active": m.is_active, "created_at": m.created_at,
            "agents_count": int(agent_counts.get(m.id, 0)),
        }
        for m in managers
    ]


def create_manager(db: Session, admin: User, *, full_name: str, email: str, phone: str,
                   request: Request | None = None) -> dict:
    """Add an AGENCY_MANAGER to the admin's agency.

    DEMO_MODE: created directly with a known password, returned as demo_login.
    Otherwise: an invitation whose link the admin hands over; the manager sets
    their own password (nothing is returned that reveals a password).
    """
    _require_agency_admin(admin)

    full_name = (full_name or "").strip()
    if not full_name or len(full_name) > 200:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Enter the manager's full name (up to 200 characters).")

    # One authority for who may create a manager (invite_service.can_invite):
    # an agency admin, into their own bank + agency.
    if not invite_service.can_invite(admin, UserRole.AGENCY_MANAGER, bank_id=admin.bank_id, agency_id=admin.agency_id):
        raise AppException(403, ErrorCode.FORBIDDEN, "You cannot create a manager here.")

    if not settings.DEMO_MODE:
        # Real path: an invitation. The manager sets their own password via the
        # link; no password is ever returned.
        out = invite_service.create_invite(
            db, admin, email=email, role=UserRole.AGENCY_MANAGER, full_name=full_name, phone=phone,
            agency_id=admin.agency_id, bank_id=admin.bank_id, channel="LINK", request=request,
        )
        return {"full_name": full_name, "email": email, "invite": out}

    # DEMO_MODE: a local demo can't deliver the invite link, so create the
    # manager directly with a known password and hand it back. Gated — a real
    # deployment takes the invite path above and never returns a password.
    demo_password = settings.DEMO_MASTER_PASSWORD or "demo-manager-pass-1234"
    try:
        user = User(
            email=email, phone=phone, full_name=full_name, role=UserRole.AGENCY_MANAGER,
            bank_id=admin.bank_id, agency_id=admin.agency_id,
            hashed_password=hash_password(demo_password),
            is_active=True, is_verified=True, must_change_password=False,
            totp_enabled=False, failed_login_attempts=0,
        )
        db.add(user)
        db.flush()
        stage_audit(db, action=AuditAction.USER_CREATED, user_id=admin.id, entity_type="User",
                   entity_id=user.id, ip_address=_client_ip(request),
                   details={"role": "AGENCY_MANAGER", "via": "agency_admin_created", "agency_id": admin.agency_id})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppException(409, ErrorCode.CONFLICT, "A user with that email or phone already exists.")

    return {
        "id": user.id, "full_name": user.full_name, "email": user.email, "phone": user.phone,
        "role": user.role.value,
        "demo_login": {"email": user.email, "password": demo_password},
    }
