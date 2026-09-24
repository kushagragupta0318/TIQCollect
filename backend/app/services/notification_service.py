# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-14 (later) — New file. normalize_phone()/send_twilio() moved out of
#   endpoints/agent.py (_normalize_phone/_send_twilio) — a direct extraction,
#   logic unchanged. Shared infrastructure, not visit-only: used by
#   VisitService plus 3 endpoints that stay in agent.py for now
#   (collect_payment, notify_visit, notify_case) — those callers only changed
#   their import source, not their behavior. Full detail + why: /changelog.md
# 2026-09-10 — Delivery failures are still swallowed, but they are no longer
#   SILENT. Both senders caught `Exception` and did `pass`, so a Twilio outage,
#   a bad credential or a rejected number produced exactly the same trace as a
#   successful send: none. That matters most for send_sms, whose one caller is
#   OtpService — the borrower's payment-verification code. The agent sees "OTP
#   sent", the borrower gets nothing, and no operator can tell the difference
#   after the fact.
#
#   THE SWALLOW IS DELIBERATE AND IS KEPT. A failed receipt must not roll back a
#   verified payment, and the docstrings have promised best-effort since
#   2026-07-14. What changes is that it logs at ERROR with the exception type,
#   exactly as planner_service._ml_recovery_probabilities was corrected on
#   2026-09-08 for the same reason: "a swallowed exception that degrades
#   correctly still has to be loud."
#
#   NOT the ptp_reminders defect. Nothing here marks a row as sent — verified:
#   no caller of send_twilio/send_sms writes a delivery flag. See
#   workers/tasks/ptp_reminders.py, which deliberately sends nothing AND marks
#   nothing.
# 2026-07-30 — Added send_sms() (SMS-only, no WhatsApp) for borrower payment
#   OTP delivery — OtpService uses it. send_twilio() (SMS + WhatsApp) is
#   unchanged and still used by every existing caller; send_sms() is additive
#   because the OTP channel was specified as SMS. See
#   prototype_to_product/30.07.md and /changelog.md.
# 2026-09-24 (standalone plan B22) — INVENTED NUMBERS ARE NEVER CONTACTED.
#   The demo book is invented people with real-format Indian mobiles
#   (seed_data draws `9` + random digits), and the only thing between a
#   recorded visit and an SMS to a stranger was whether Twilio credentials
#   happened to be set (coordinator audit P0-A1). Now, before any send:
#     - an unresolved "${VAR}" credential counts as unset (a docker
#       --env-file does not interpolate, so .env.example's
#       `TWILIO_ACCOUNT_SID=${TWILIO_ACCOUNT_SID}` arrived as a literal,
#       non-empty SID and the client dialled api.twilio.com);
#     - in DEMO_MODE, or for a demo tenant (`demo_tenant=True` from callers
#       that know the bank/agency), a message goes out ONLY to a number on the
#       explicit allowlist: DEMO_CONTACT_PHONE (the presenter's own phone the
#       showcase OTP is meant to reach) plus DEMO_NOTIFY_ALLOWLIST. Everything
#       else is suppressed and logged — never sent.
#   Suppression returns False like any other non-send, so callers' existing
#   "receipt_sent / sms_sent" reporting stays truthful.
# ───────────────────────────────────────────────────────────────────────────
"""
SMS/WhatsApp notification via Twilio.

Stateless by design (no DB dependency) — kept as a class rather than bare
module functions to match this codebase's service pattern (VisitService,
CaseService, AuthService).
"""
from __future__ import annotations

import re

import structlog

from app.core.config import settings

logger = structlog.get_logger()


def _real(value: str | None) -> bool:
    """A setting that is present AND not an uninterpolated "${VAR}" reference."""
    return bool(value) and not str(value).strip().startswith("${")


def twilio_configured() -> bool:
    return _real(settings.TWILIO_ACCOUNT_SID) and _real(settings.TWILIO_AUTH_TOKEN)


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def demo_allowlist() -> set[str]:
    """E.164 digits (no '+') that may be contacted while demo data is live."""
    raw = [settings.DEMO_CONTACT_PHONE, *str(getattr(settings, "DEMO_NOTIFY_ALLOWLIST", "") or "").split(",")]
    return {NotificationService.normalize_phone(p) for p in raw if p and p.strip()}


def outbound_allowed(phone_e164: str, *, demo_tenant: bool = False) -> bool:
    """The one gate every send passes. Suppressed sends are logged, masked."""
    if not (settings.DEMO_MODE or demo_tenant):
        return True
    if _digits(phone_e164) in demo_allowlist():
        return True
    logger.info("notification.suppressed_demo", to_last4=_digits(phone_e164)[-4:],
                demo_mode=settings.DEMO_MODE, demo_tenant=demo_tenant)
    return False


class NotificationService:
    @staticmethod
    def normalize_phone(raw: str) -> str:
        """Return the number as bare E.164 digits WITHOUT the leading '+'
        (every caller prepends '+' itself).

        Respects an explicit international prefix so non-Indian numbers work:
          '+19998887777'  -> '19998887777'   (US, kept as given)
          '00447911123456'-> '447911123456'  (UK, '00' intl prefix)
          '918015935790'   -> '918015935790'  (already has default country code)
          '8015935790'     -> '918015935790'  (bare national -> default country code)
        The default country code for bare national numbers is configurable via
        DEFAULT_COUNTRY_CODE (defaults to '91'), so an Indian 10-digit number
        still works with no prefix while a foreign number works when entered in
        full '+<cc>...' form.
        """
        s = raw.strip()
        cc = settings.DEFAULT_COUNTRY_CODE
        # Explicit international format ('+..' or '00..') — trust the country code given.
        if s.startswith("+"):
            return re.sub(r"\D", "", s)
        digits = re.sub(r"\D", "", s)
        if digits.startswith("00"):
            return digits[2:]
        # No explicit prefix. If it already carries the default country code
        # (longer than a 10-digit national number), keep it; else prepend it.
        if digits.startswith(cc) and len(digits) > 10:
            return digits
        return cc + digits.lstrip("0")

    @staticmethod
    def send_twilio(phone_e164: str, sms_body: str, wa_body: str, *, demo_tenant: bool = False) -> bool:
        """Send SMS + WhatsApp via Twilio. Best-effort — exceptions are swallowed.

        Returns whether at least one message was handed to the transport.
        2026-09-11 — this returned None, so no caller could tell a delivered
        receipt from a swallowed failure. It still never raises; callers that
        ignore the result behave exactly as before. False also covers "Twilio
        not configured", "no sending number set" and (2026-09-24) "suppressed
        because the recipient is invented demo data": nothing was sent, and
        saying so is the point.
        """
        try:
            if not twilio_configured():
                return False
            if not outbound_allowed(phone_e164, demo_tenant=demo_tenant):
                return False
            from twilio.rest import Client
            client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            sent = False
            if settings.TWILIO_PHONE_NUMBER:
                client.messages.create(body=sms_body, from_=settings.TWILIO_PHONE_NUMBER, to=phone_e164)
                sent = True
            if settings.TWILIO_WHATSAPP_FROM:
                client.messages.create(body=wa_body, from_=settings.TWILIO_WHATSAPP_FROM, to=f"whatsapp:{phone_e164}")
                sent = True
            return sent
        except Exception as exc:
            # Swallowed on purpose (see the header), but never silently.
            logger.error("notification.twilio_send_failed", channel="sms+whatsapp",
                         error=str(exc), error_type=type(exc).__name__, exc_info=True)
            return False

    @staticmethod
    def send_sms(phone_e164: str, sms_body: str, *, demo_tenant: bool = False) -> bool:
        """Send an SMS only (no WhatsApp) via Twilio. Best-effort — exceptions
        are swallowed. Used for borrower payment-verification OTPs, whose
        delivery channel is SMS. When Twilio is unconfigured this is a no-op,
        so tests and local runs work without credentials.

        Returns whether the message was handed to the transport (see
        send_twilio). OtpService does not read it yet; the OTP path's own
        "sent" claim remains a known gap."""
        try:
            if not twilio_configured():
                return False
            if not outbound_allowed(phone_e164, demo_tenant=demo_tenant):
                return False
            from twilio.rest import Client
            client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            if settings.TWILIO_PHONE_NUMBER:
                client.messages.create(body=sms_body, from_=settings.TWILIO_PHONE_NUMBER, to=phone_e164)
                return True
            return False
        except Exception as exc:
            # The OTP path. A borrower who never receives this cannot verify a
            # payment, and before this line nothing anywhere recorded that.
            logger.error("notification.twilio_send_failed", channel="sms",
                         error=str(exc), error_type=type(exc).__name__, exc_info=True)
            return False
