# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B22, coordinator audit gate 2, HIGH) — NEW. Browser calling
#   through Twilio Voice, rebuilt so it cannot be used as a free dialler.
#
#   WHAT WAS WRONG (agent.py, since the voice feature landed):
#     - POST /agent/voice/outbound was unauthenticated, did not check Twilio's
#       X-Twilio-Signature, and dialled whatever `PhoneTo` the CLIENT sent, with
#       caller_id = our Twilio number. Anyone who could reach the URL — or any
#       logged-in agent, or anyone holding the published demo passwords — could
#       ring any number in the world on the company account.
#     - GET /agent/voice/token had no demo gate, treated a literal "${VAR}"
#       credential as set (`all([...])`), and minted hour-long tokens that
#       outlived a logout.
#
#   NOW:
#     - The webhook is honoured only with a valid X-Twilio-Signature computed
#       over the PUBLIC URL Twilio called (PUBLIC_BASE_URL + path + query).
#       Behind Caddy the app sees http and an internal host, so request.url
#       would never match; with PUBLIC_BASE_URL unset the webhook refuses
#       everything (fail closed).
#     - The client sends a CASE id, never a number. The number is the case's
#       borrower's, resolved here, and only for a case the calling agent may
#       act on (services/scope: assigned, or on today's beat), inside RBI contact hours, not do-not-contact, and through the
#       same demo/tenant suppression as SMS (services/brand.tenant_of).
#     - The caller is identified by the token identity `agent:<user>:<sid>`,
#       and the call is refused unless that login session is still live — so
#       logout, an admin revoke or reuse detection ends calling too, and the
#       token itself lives five minutes.
#   Every refusal returns TwiML that says a neutral sentence (Twilio needs a
#   200 with XML to hang up cleanly) and writes a VOICE_CALL_REFUSED audit row;
#   a placed call writes VOICE_CALL_PLACED with the case, never the number.
# ────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppException
from app.core.geo import is_within_contact_hours
from app.core.ids import parse_uuid
from app.services.notification_service import NotificationService, _real, outbound_allowed
from app.services.scope import agent_case_or_404

logger = structlog.get_logger()

VOICE_TOKEN_TTL_SECONDS = 300
IDENTITY_PREFIX = "agent"

# Refusal reasons (audit `failure_reason`; never shown to the callee).
BAD_SIGNATURE = "BAD_SIGNATURE"
NOT_CONFIGURED = "NOT_CONFIGURED"
BAD_IDENTITY = "BAD_IDENTITY"
SESSION_ENDED = "SESSION_ENDED"
NOT_YOUR_CASE = "NOT_YOUR_CASE"
OUTSIDE_CONTACT_HOURS = "OUTSIDE_CONTACT_HOURS"
DO_NOT_CONTACT = "DO_NOT_CONTACT"
NO_NUMBER = "NO_NUMBER"
DEMO_SUPPRESSED = "DEMO_SUPPRESSED"


class VoiceRefused(Exception):
    def __init__(self, reason: str, *, user_id: str | None = None, case_id: str | None = None):
        self.reason, self.user_id, self.case_id = reason, user_id, case_id
        super().__init__(reason)


def voice_configured() -> bool:
    """Every credential the token AND the webhook check need, each a real
    value (an uninterpolated "${VAR}" is not)."""
    return all(_real(v) for v in (
        settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN, settings.TWILIO_API_KEY_SID,
        settings.TWILIO_API_KEY_SECRET, settings.TWILIO_TWIML_APP_SID, settings.TWILIO_PHONE_NUMBER,
        settings.PUBLIC_BASE_URL,
    ))


def identity_for(user_id: str, sid: str) -> str:
    return f"{IDENTITY_PREFIX}:{user_id}:{sid}"


def parse_identity(from_param: str | None) -> tuple[str, str] | None:
    """Twilio sends the browser caller as `From=client:<identity>`."""
    value = (from_param or "").strip()
    if value.startswith("client:"):
        value = value[len("client:"):]
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != IDENTITY_PREFIX:
        return None
    user_id, sid = parse_uuid(parts[1]), parse_uuid(parts[2])
    return (user_id, sid) if user_id and sid else None


def public_url(path: str, query: str = "") -> str | None:
    base = (settings.PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not _real(base):
        return None
    return f"{base}{path}" + (f"?{query}" if query else "")


def signature_ok(url: str | None, params: dict, signature: str | None) -> bool:
    if not url or not signature or not _real(settings.TWILIO_AUTH_TOKEN):
        return False
    try:
        from twilio.request_validator import RequestValidator
    except ImportError:          # no SDK: nothing can be verified, so nothing is honoured
        return False
    return bool(RequestValidator(settings.TWILIO_AUTH_TOKEN).validate(url, params, signature))


def mint_token(user_id: str, sid: str) -> str:
    from twilio.jwt.access_token import AccessToken
    from twilio.jwt.access_token.grants import VoiceGrant
    token = AccessToken(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_API_KEY_SID, settings.TWILIO_API_KEY_SECRET,
                        identity=identity_for(user_id, sid), ttl=VOICE_TOKEN_TTL_SECONDS)
    token.add_grant(VoiceGrant(outgoing_application_sid=settings.TWILIO_TWIML_APP_SID, incoming_allow=False))
    jwt = token.to_jwt()
    return jwt.decode() if isinstance(jwt, bytes) else jwt


@dataclass(frozen=True)
class Destination:
    e164: str
    case_id: str
    user_id: str


def resolve_destination(db: Session, *, from_param: str | None, case_id: str | None,
                        now: datetime | None = None) -> Destination:
    """The borrower's number for a case this live session's agent may call,
    or VoiceRefused. The client never supplies a number."""
    from app.models.agent import Agent
    from app.models.case import Case
    from app.models.customer import Customer
    from app.models.identity import UserSession
    from app.models.user import User, UserRole
    from app.services.brand import tenant_of

    ident = parse_identity(from_param)
    if ident is None:
        raise VoiceRefused(BAD_IDENTITY)
    user_id, sid = ident
    now = now or datetime.now(timezone.utc)

    session = db.get(UserSession, sid)
    expires = session.expires_at if session is not None else None
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if (session is None or session.user_id != user_id or session.revoked_at is not None
            or expires is None or expires <= now):
        raise VoiceRefused(SESSION_ENDED, user_id=user_id)
    user = db.get(User, user_id)
    if user is None or not user.is_active or user.role != UserRole.FIELD_AGENT:
        raise VoiceRefused(SESSION_ENDED, user_id=user_id)

    cid = parse_uuid(case_id)
    agent = db.query(Agent).filter(Agent.user_id == user_id).first()
    # The one access rule (services/scope, A03): assigned to this agent, or on
    # their beat today, inside their agency. Never a teammate's or another
    # agency's case, never an unassigned pool case.
    try:
        case = agent_case_or_404(db, agent, cid)
    except AppException:
        raise VoiceRefused(NOT_YOUR_CASE, user_id=user_id, case_id=cid) from None
    if not is_within_contact_hours(now):
        raise VoiceRefused(OUTSIDE_CONTACT_HOURS, user_id=user_id, case_id=cid)
    customer = db.get(Customer, case.customer_id)
    if customer is None or customer.do_not_contact:
        raise VoiceRefused(DO_NOT_CONTACT, user_id=user_id, case_id=cid)
    if not customer.phone_primary:
        raise VoiceRefused(NO_NUMBER, user_id=user_id, case_id=cid)
    e164 = "+" + NotificationService.normalize_phone(customer.phone_primary)
    if not outbound_allowed(e164, tenant_of(db, case=case)):
        raise VoiceRefused(DEMO_SUPPRESSED, user_id=user_id, case_id=cid)
    return Destination(e164=e164, case_id=cid, user_id=user_id)
