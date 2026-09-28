# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — NEW (live-site hotfix, board AU-2). Browser calling through
#   Twilio Voice, rebuilt so it cannot be used as a free dialler.
#
#   WHAT WAS WRONG (agent.py, since the voice feature landed):
#     - POST /agent/voice/outbound was unauthenticated, did not check Twilio's
#       X-Twilio-Signature, and dialled whatever `PhoneTo` the CLIENT sent,
#       with caller_id = our Twilio number. Anyone who could reach the URL, or
#       any logged-in agent, could ring any number in the world on the
#       company's account.
#     - GET /agent/voice/token treated a literal "${VAR}" credential as set
#       (`all([...])` of non-empty strings), minted hour-long tokens, and
#       echoed the SDK's exception text to the client on failure.
#
#   NOW:
#     - The webhook is honoured only with a valid X-Twilio-Signature computed
#       over the PUBLIC URL Twilio called (PUBLIC_BASE_URL + path + query).
#       With PUBLIC_BASE_URL or the auth token unset it refuses everything.
#     - The client sends a CASE id (custom param `CaseId`), never a number.
#       The number is that case's borrower's, resolved here, and only for a
#       case ASSIGNED to the calling agent (strict Case.agent_id == agent.id,
#       the rule media_service and otp_service use), inside RBI contact hours,
#       and never for a do-not-contact borrower.
#     - The caller is the token identity `agent:<user id>`, signed into the
#       call by Twilio; tokens live five minutes.
#
#   This is the v1 cut of what the standalone plan builds on standalone-p1
#   (43's voice_service.py): the same names and shape, minus what v1 has no
#   table for — the login-session check (p1's user_sessions, so there the
#   identity is agent:<user>:<session>) and demo-tenant suppression (p1's
#   B22). On v1 the audit actions are EXISTING ones, because audit_action_enum
#   is a native Postgres enum and a new value needs a migration: refusals are
#   ROLE_VIOLATION_ATTEMPT (CONTACT_HOUR_VIOLATION_ATTEMPT when that is the
#   reason). p1 declares VOICE_CALL_REFUSED / VOICE_CALL_PLACED instead.
# ────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.geo import is_within_contact_hours
from app.services.notification_service import NotificationService

logger = structlog.get_logger()

VOICE_TOKEN_TTL_SECONDS = 300
IDENTITY_PREFIX = "agent"

# Refusal reasons (audit `failure_reason`; never shown to the callee).
BAD_SIGNATURE = "BAD_SIGNATURE"
NOT_CONFIGURED = "NOT_CONFIGURED"
BAD_IDENTITY = "BAD_IDENTITY"
NOT_YOUR_CASE = "NOT_YOUR_CASE"
OUTSIDE_CONTACT_HOURS = "OUTSIDE_CONTACT_HOURS"
DO_NOT_CONTACT = "DO_NOT_CONTACT"
NO_NUMBER = "NO_NUMBER"
NOT_OUR_APP = "NOT_OUR_APP"


class VoiceRefused(Exception):
    def __init__(self, reason: str, *, user_id: str | None = None, case_id: str | None = None):
        self.reason, self.user_id, self.case_id = reason, user_id, case_id
        super().__init__(reason)


def _real(value: str | None) -> bool:
    """A credential that is actually set: not empty, and not an uninterpolated
    "${VAR}" left behind by an .env written for another stack."""
    v = (value or "").strip()
    return bool(v) and not v.startswith("${")


def _uuid(value: str | None) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None


def voice_configured() -> bool:
    """Every credential the token AND the webhook check need, each a real value."""
    return all(_real(v) for v in (
        settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN, settings.TWILIO_API_KEY_SID,
        settings.TWILIO_API_KEY_SECRET, settings.TWILIO_TWIML_APP_SID, settings.TWILIO_PHONE_NUMBER,
        settings.PUBLIC_BASE_URL,
    ))


def identity_for(user_id: str) -> str:
    """agent_<32 hex>. Alphanumerics and underscore only: Twilio client
    identities may not allow ':' or '-', and an identity it rejects makes every
    call fail. (This was agent:<uuid> until the audit of 4dcd9dc.)"""
    return f"{IDENTITY_PREFIX}_{uuid.UUID(str(user_id)).hex}"


def parse_identity(from_param: str | None) -> str | None:
    """Twilio sends the browser caller as `From=client:<identity>`."""
    value = (from_param or "").strip()
    if value.startswith("client:"):
        value = value[len("client:"):]
    prefix = f"{IDENTITY_PREFIX}_"
    if not value.startswith(prefix):
        return None
    hex_part = value[len(prefix):]
    if len(hex_part) != 32 or any(c not in "0123456789abcdef" for c in hex_part):
        return None
    return str(uuid.UUID(hex=hex_part))


def from_our_app(params: dict) -> bool:
    """The signed request came from OUR Twilio account and OUR TwiML app. The
    signature proves the account's auth token signed it; any other TwiML app
    in the same account could otherwise point here with identities of its own."""
    return (params.get("AccountSid") == settings.TWILIO_ACCOUNT_SID
            and params.get("ApplicationSid") == settings.TWILIO_TWIML_APP_SID)


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


def mint_token(user_id: str) -> str:
    from twilio.jwt.access_token import AccessToken
    from twilio.jwt.access_token.grants import VoiceGrant
    token = AccessToken(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_API_KEY_SID, settings.TWILIO_API_KEY_SECRET,
                        identity=identity_for(user_id), ttl=VOICE_TOKEN_TTL_SECONDS)
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
    """The borrower's number for a case assigned to the calling agent, or
    VoiceRefused. The client never supplies a number."""
    from app.models.agent import Agent
    from app.models.case import Case
    from app.models.customer import Customer
    from app.models.user import User, UserRole

    user_id = parse_identity(from_param)
    if user_id is None:
        raise VoiceRefused(BAD_IDENTITY)
    now = now or datetime.now(timezone.utc)

    user = db.get(User, user_id)
    if user is None:
        # Not user_id=user_id: an id with no users row violates the audit
        # table's foreign key on Postgres and the refusal row would be lost.
        raise VoiceRefused(BAD_IDENTITY)
    if not user.is_active or user.role != UserRole.FIELD_AGENT:
        raise VoiceRefused(BAD_IDENTITY, user_id=user_id)

    cid = _uuid(case_id)
    agent = db.query(Agent).filter(Agent.user_id == user_id).first()
    case = db.get(Case, cid) if cid else None
    # Strict ownership: the case is ASSIGNED to this agent. A dialler is the
    # wrong place for the looser "on my beat / my team's" readings of
    # _get_accessible_case_or_404.
    if agent is None or case is None or case.agent_id != agent.id:
        raise VoiceRefused(NOT_YOUR_CASE, user_id=user_id, case_id=cid)
    if not is_within_contact_hours(now):
        raise VoiceRefused(OUTSIDE_CONTACT_HOURS, user_id=user_id, case_id=cid)
    customer = db.get(Customer, case.customer_id)
    if customer is None or customer.do_not_contact:
        raise VoiceRefused(DO_NOT_CONTACT, user_id=user_id, case_id=cid)
    if not customer.phone_primary:
        raise VoiceRefused(NO_NUMBER, user_id=user_id, case_id=cid)
    e164 = "+" + NotificationService.normalize_phone(customer.phone_primary)
    return Destination(e164=e164, case_id=cid, user_id=user_id)
