# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-09-28 — NEW (P1 A06, d4). Invitations: how a person gets an account
#   without anybody ever choosing, seeing or sending them a password.
#
#   - An admin invites an email address to a role in their own bank or agency.
#     The token is minted with `secrets`, shown ONCE (LINK) or sent by SMS,
#     and stored only as sha256 (tenancy.user_invites.token_sha256). It lives
#     72 hours and is single-use: acceptance is a compare-and-swap on
#     accepted_at, so two tabs racing the same link create one account.
#   - The invitee sets their own password (credentials.password_problem) and
#     is signed in through auth_service.open_session — the frozen way every
#     login of any kind starts a session.
#   - Who may invite whom is ONE function, can_invite(). It is role-based
#     today and becomes a require_perm() check when the capability registry
#     (A01, ce) lands; the rule's shape is unchanged either way.
#   - FIELD_AGENT is not invitable here: an agent is a workforce.agents row
#     with an employee code, a base and a manager, which Manage Agents (G)
#     creates. PLATFORM_ADMIN and SERVICE are never invitable.
#   - EMAIL delivery is refused as not configured (CHANNEL_UNAVAILABLE), not
#     faked: no provider exists. SMS needs PUBLIC_BASE_URL for the link and
#     goes through NotificationService, so demo tenants are never texted.
#   - An AGENCY_MASTER_LOGIN invite accepted does NOT activate the agency:
#     that is D03 (P2), which hooks accept_invite.
# ────────────────────────────────────────────────────────────────────────────
"""Invite a person to a role; they accept by setting their own password."""
from __future__ import annotations

import re
from datetime import timedelta

from fastapi import Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import stage_audit, write_audit
from app.core.config import settings
from app.core.errors import AppException, ErrorCode
from app.core.security import hash_password, token_sha256
from app.models.audit_log import AuditAction
from app.models.identity import UserInvite
from app.models.tenancy import Agency, Bank
from app.models.user import BANK_ROLES, User, UserRole
from app.services.credentials import new_token, now, require_good_password, utc

INVITE_TTL = timedelta(hours=72)
LIST_CAP = 200

_INVALID = "This invitation link is not valid. It may have expired, been used, or been withdrawn."


def _client_ip(request: Request | None) -> str | None:
    return request.client.host if request is not None and request.client else None


# ── who may invite whom ─────────────────────────────────────────────────────
def can_invite(inviter: User, role: UserRole, *, bank_id: str | None, agency_id: str | None) -> bool:
    """The one rule. A bank admin brings in bank users and an agency's master
    login; an agency admin brings in its managers; the platform admin brings
    in a bank's first admin. Nobody invites into another tenant."""
    if role in (UserRole.PLATFORM_ADMIN, UserRole.SERVICE, UserRole.FIELD_AGENT):
        return False
    if inviter.role == UserRole.PLATFORM_ADMIN:
        return role == UserRole.BANK_ADMIN and bank_id is not None and agency_id is None
    if inviter.role == UserRole.BANK_ADMIN:
        if bank_id != inviter.bank_id:
            return False
        if role in BANK_ROLES:
            return agency_id is None
        return role == UserRole.AGENCY_ADMIN and agency_id is not None
    if inviter.role == UserRole.AGENCY_ADMIN:
        return (role == UserRole.AGENCY_MANAGER and bank_id == inviter.bank_id
                and agency_id is not None and agency_id == inviter.agency_id)
    return False


def _normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def _normalise_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]              # the book stores Indian numbers as 10 digits
    if not 10 <= len(digits) <= 15:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Enter a phone number of 10 to 15 digits.")
    return digits


def status_of(inv: UserInvite) -> str:
    if inv.accepted_at is not None:
        return "ACCEPTED"
    if inv.revoked_at is not None:
        return "REVOKED"
    if utc(inv.expires_at) <= now():
        return "EXPIRED"
    return "OPEN"


def to_dict(inv: UserInvite) -> dict:
    """The admin's view. Never carries the token or its hash."""
    return {
        "id": inv.id, "email": inv.email, "full_name": inv.full_name, "phone": inv.phone,
        "role": inv.role.value, "purpose": inv.purpose, "bank_id": inv.bank_id, "agency_id": inv.agency_id,
        "delivery_channel": inv.delivery_channel, "invited_by": inv.invited_by,
        "created_at": utc(inv.created_at).isoformat() if inv.created_at else None,
        "expires_at": utc(inv.expires_at).isoformat(),
        "status": status_of(inv),
    }


# ── create ──────────────────────────────────────────────────────────────────
def create_invite(db: Session, inviter: User, *, email: str, role: UserRole, full_name: str, phone: str,
                  agency_id: str | None = None, bank_id: str | None = None, channel: str = "LINK",
                  request: Request | None = None) -> dict:
    """Returns the invite, plus the token ONCE when the channel is LINK."""
    email = _normalise_email(email)
    phone = _normalise_phone(phone)
    full_name = (full_name or "").strip()
    if not email or "@" not in email or not full_name:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "An invitation needs a name, an email and a phone.")
    channel = (channel or "LINK").upper()
    if channel not in ("LINK", "SMS", "EMAIL"):
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Channel must be LINK, SMS or EMAIL.")
    if channel == "EMAIL":
        raise AppException(503, ErrorCode.CHANNEL_UNAVAILABLE,
                           "Email delivery is not configured. Send the link yourself, or by SMS.")

    # The tenant comes from the inviter, never from the request, except the
    # platform admin (no bank of their own) naming the bank.
    if inviter.role != UserRole.PLATFORM_ADMIN:
        bank_id = inviter.bank_id
    if role in BANK_ROLES:
        agency_id = None
    elif inviter.role == UserRole.AGENCY_ADMIN:
        agency_id = inviter.agency_id

    if not can_invite(inviter, role, bank_id=bank_id, agency_id=agency_id):
        write_audit(db, action=AuditAction.ROLE_VIOLATION_ATTEMPT, user_id=inviter.id, entity_type="UserInvite",
                    success=False, failure_reason=f"invite of {role.value} refused",
                    details={"role": role.value, "bank_id": bank_id, "agency_id": agency_id},
                    ip_address=_client_ip(request))
        raise AppException(403, ErrorCode.INVITE_NOT_ALLOWED, "You cannot invite someone to that role.")
    if inviter.role == UserRole.PLATFORM_ADMIN:
        # Allowed above; only now does this request act inside the one named bank (RLS, A13b S1b).
        from app.services.scope import platform_acts_in_bank
        bank_id = platform_acts_in_bank(db, inviter, bank_id)

    bank = db.get(Bank, bank_id) if bank_id else None
    if bank is None or bank.status != "ACTIVE":
        raise AppException(404, ErrorCode.NOT_FOUND, "Bank not found")
    if agency_id is not None:
        agency = db.get(Agency, agency_id)
        if agency is None or agency.bank_id != bank_id:
            raise AppException(404, ErrorCode.NOT_FOUND, "Agency not found")
        allowed = ("PENDING", "ACTIVE") if role == UserRole.AGENCY_ADMIN else ("ACTIVE",)
        if agency.status not in allowed:
            raise AppException(409, ErrorCode.CONFLICT, f"The agency is {agency.status.lower()}.")

    if db.query(User.id).filter((User.email == email) | (User.phone == phone)).first():
        raise AppException(409, ErrorCode.CONFLICT, "An account with this email or phone already exists.")
    open_invite = (db.query(UserInvite)
                   .filter(UserInvite.email == email, UserInvite.accepted_at.is_(None),
                           UserInvite.revoked_at.is_(None)).first())
    if open_invite is not None:
        if utc(open_invite.expires_at) > now():
            raise AppException(409, ErrorCode.CONFLICT,
                               "This person already has an open invitation. Withdraw it to send a new one.")
        # An expired open invite would block the unique index: withdraw it.
        open_invite.revoked_at = now()
        open_invite.revoked_by = inviter.id

    sms_link = None
    if channel == "SMS":
        base = (settings.PUBLIC_BASE_URL or "").strip().rstrip("/")
        if not base or base.startswith("${"):
            raise AppException(503, ErrorCode.CHANNEL_UNAVAILABLE,
                               "SMS invitations need PUBLIC_BASE_URL. Send the link yourself instead.")
        sms_link = base

    token, sha = new_token()
    inv = UserInvite(
        bank_id=bank_id, agency_id=agency_id,
        purpose="AGENCY_MASTER_LOGIN" if role == UserRole.AGENCY_ADMIN and inviter.is_bank_user else "USER_ONBOARD",
        email=email, phone=phone, full_name=full_name, role=role, token_sha256=sha,
        delivery_channel=channel, invited_by=inviter.id, expires_at=now() + INVITE_TTL,
    )
    db.add(inv)
    db.flush()
    stage_audit(db, action=AuditAction.USER_INVITED, user_id=inviter.id, entity_type="UserInvite",
                entity_id=inv.id, bank_id=inv.bank_id, agency_id=inv.agency_id, ip_address=_client_ip(request),
                details={"role": role.value, "purpose": inv.purpose, "bank_id": bank_id, "agency_id": agency_id,
                         "channel": channel})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppException(409, ErrorCode.CONFLICT,
                           "This person already has an open invitation. Withdraw it to send a new one.")

    delivered = None
    if channel == "SMS":
        from app.services.notification_service import NotificationService
        link = f"{sms_link}{set_password_path(token)}"
        body = f"You have been invited to TIQCollect. Set your password within 72 hours: {link}"
        delivered = NotificationService.send_sms("+" + NotificationService.normalize_phone(phone), body,
                                                 db=db, bank_id=bank_id, agency_id=agency_id)

    out = {"invite": to_dict(inv), "delivered": delivered}
    if channel == "LINK":
        out["token"] = token            # shown once; the caller builds the link
        out["path"] = set_password_path(token)
    return out


def set_password_path(token: str) -> str:
    """The frontend route that accepts an invitation (pages/auth/SetPasswordPage)."""
    return f"/set-password?token={token}"


# ── list / revoke (admin) ───────────────────────────────────────────────────
def _in_scope(query, principal: User):
    if principal.role == UserRole.PLATFORM_ADMIN:
        return query
    if principal.role == UserRole.BANK_ADMIN:
        return query.filter(UserInvite.bank_id == principal.bank_id)
    if principal.role == UserRole.AGENCY_ADMIN:
        return query.filter(UserInvite.agency_id == principal.agency_id)
    return query.filter(UserInvite.id.is_(None))            # no one else sees invites


def list_invites(db: Session, principal: User) -> list[dict]:
    rows = (_in_scope(db.query(UserInvite), principal)
            .order_by(UserInvite.created_at.desc()).limit(LIST_CAP).all())
    return [to_dict(r) for r in rows]


def revoke_invite(db: Session, principal: User, invite_id: str, *, request: Request | None = None) -> dict:
    inv = _in_scope(db.query(UserInvite).filter(UserInvite.id == invite_id), principal).first()
    if inv is None:
        raise AppException(404, ErrorCode.NOT_FOUND, "Invitation not found")
    if status_of(inv) != "OPEN":
        raise AppException(409, ErrorCode.CONFLICT, f"The invitation is already {status_of(inv).lower()}.")
    inv.revoked_at = now()
    inv.revoked_by = principal.id
    stage_audit(db, action=AuditAction.INVITE_REVOKED, user_id=principal.id, entity_type="UserInvite",
                entity_id=inv.id, bank_id=inv.bank_id, agency_id=inv.agency_id, ip_address=_client_ip(request), details={"role": inv.role.value})
    db.commit()
    return to_dict(inv)


# ── accept (public) ─────────────────────────────────────────────────────────
def _open_by_token(db: Session, token: str) -> UserInvite:
    # A13b S1b: the invitation's tenant first, then the row under it. The token
    # is the capability: whoever holds it acts inside the invitee's tenant.
    from app.core import preauth
    sha = token_sha256(token or "")
    invitee = preauth.invitee_by_token_sha(db, sha)
    if invitee is None:
        raise AppException(400, ErrorCode.INVITE_INVALID, _INVALID)
    preauth.bind(db, invitee)
    inv = db.query(UserInvite).filter(UserInvite.token_sha256 == sha).first()
    if inv is None or status_of(inv) != "OPEN":
        raise AppException(400, ErrorCode.INVITE_INVALID, _INVALID)
    return inv


def preview_invite(db: Session, token: str) -> dict:
    """What the set-password page shows before the invitee commits."""
    inv = _open_by_token(db, token)
    org = None
    if inv.agency_id:
        a = db.get(Agency, inv.agency_id)
        org = a.legal_name if a else None
    elif inv.bank_id:
        b = db.get(Bank, inv.bank_id)
        org = b.legal_name if b else None
    return {"email": inv.email, "full_name": inv.full_name, "role": inv.role.value, "organisation": org,
            "expires_at": utc(inv.expires_at).isoformat()}


def accept_invite(db: Session, token: str, password: str, device_id: str, request: Request) -> dict:
    """Create the account and sign it in. Single-use by compare-and-swap."""
    from app.services import auth_service

    inv = _open_by_token(db, token)
    require_good_password(password, email=inv.email)
    stamp = now()
    won = (db.query(UserInvite)
           .filter(UserInvite.id == inv.id, UserInvite.accepted_at.is_(None), UserInvite.revoked_at.is_(None),
                   UserInvite.expires_at > stamp)
           .update({UserInvite.accepted_at: stamp}, synchronize_session=False))
    if won != 1:
        db.rollback()
        raise AppException(400, ErrorCode.INVITE_INVALID, _INVALID)
    user = User(email=inv.email, phone=inv.phone, full_name=inv.full_name or inv.email, role=inv.role,
                bank_id=inv.bank_id, agency_id=inv.agency_id, hashed_password=hash_password(password),
                is_active=True, is_verified=True, must_change_password=False, password_changed_at=stamp)
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise AppException(409, ErrorCode.CONFLICT, "An account with this email or phone already exists.")
    (db.query(UserInvite).filter(UserInvite.id == inv.id)
     .update({UserInvite.accepted_user_id: user.id}, synchronize_session=False))
    # D03 (P2) hooks here for purpose AGENCY_MASTER_LOGIN: activate the agency.
    # A SESSION IS MINTED HERE (43's review, 2026-09-28: every minting site
    # states its MFA decision). The account was created a moment ago, so it
    # cannot be enrolled or owe a forced change: the only gate that can apply
    # is BANK_MFA_REQUIRED, and a bank user under it gets the enrollment
    # ticket, not a session, exactly as at login.
    from app.services import mfa_service
    _stage_accept(db, inv, user, request)
    if inv.role == UserRole.AGENCY_ADMIN and inv.agency_id:
        from app.services.bank.agency_service import _maybe_activate
        _maybe_activate(db, inv.agency_id)
    gate = mfa_service.enrollment_gate(db, user)
    if gate is not None:
        db.commit()
        return gate
    # Through the same post-gate path as /auth/login: device binding, the
    # LOGIN row, one commit that also carries the two rows staged above
    # (coordinator's audit MED).
    # No device secret on this route: a field agent with a bound device gets the uniform 403.
    return auth_service.complete_login(db, user, device_id, request, method="invite", device_secret=None)


def _stage_accept(db: Session, inv: UserInvite, user: User, request: Request | None) -> None:
    stage_audit(db, action=AuditAction.INVITE_ACCEPTED, user_id=user.id, entity_type="UserInvite",
                entity_id=inv.id, ip_address=_client_ip(request), details={"role": inv.role.value})
    stage_audit(db, action=AuditAction.USER_CREATED, user_id=user.id, entity_type="User", entity_id=user.id,
                ip_address=_client_ip(request),
                details={"role": inv.role.value, "via": "invite", "invited_by": inv.invited_by,
                         "bank_id": inv.bank_id, "agency_id": inv.agency_id})
