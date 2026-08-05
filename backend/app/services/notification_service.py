# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-14 (later) — New file. normalize_phone()/send_twilio() moved out of
#   endpoints/agent.py (_normalize_phone/_send_twilio) — a direct extraction,
#   logic unchanged. Shared infrastructure, not visit-only: used by
#   VisitService plus 3 endpoints that stay in agent.py for now
#   (collect_payment, notify_visit, notify_case) — those callers only changed
#   their import source, not their behavior. Full detail + why: /changelog.md
# 2026-07-30 — Added send_sms() (SMS-only, no WhatsApp) for borrower payment
#   OTP delivery — OtpService uses it. send_twilio() (SMS + WhatsApp) is
#   unchanged and still used by every existing caller; send_sms() is additive
#   because the OTP channel was specified as SMS. See
#   prototype_to_product/30.07.md and /changelog.md.
# ───────────────────────────────────────────────────────────────────────────
"""
SMS/WhatsApp notification via Twilio.

Stateless by design (no DB dependency) — kept as a class rather than bare
module functions to match this codebase's service pattern (VisitService,
CaseService, AuthService).
"""
from __future__ import annotations

import re

from app.core.config import settings


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
    def send_twilio(phone_e164: str, sms_body: str, wa_body: str) -> None:
        """Send SMS + WhatsApp via Twilio. Best-effort — exceptions are swallowed."""
        try:
            if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
                return
            from twilio.rest import Client
            client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            if settings.TWILIO_PHONE_NUMBER:
                client.messages.create(body=sms_body, from_=settings.TWILIO_PHONE_NUMBER, to=phone_e164)
            if settings.TWILIO_WHATSAPP_FROM:
                client.messages.create(body=wa_body, from_=settings.TWILIO_WHATSAPP_FROM, to=f"whatsapp:{phone_e164}")
        except Exception:
            pass

    @staticmethod
    def send_sms(phone_e164: str, sms_body: str) -> None:
        """Send an SMS only (no WhatsApp) via Twilio. Best-effort — exceptions
        are swallowed. Used for borrower payment-verification OTPs, whose
        delivery channel is SMS. When Twilio is unconfigured this is a no-op,
        so tests and local runs work without credentials."""
        try:
            if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
                return
            from twilio.rest import Client
            client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            if settings.TWILIO_PHONE_NUMBER:
                client.messages.create(body=sms_body, from_=settings.TWILIO_PHONE_NUMBER, to=phone_e164)
        except Exception:
            pass
