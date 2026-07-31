# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-14 (later) — New file. normalize_phone()/send_twilio() moved out of
#   endpoints/agent.py (_normalize_phone/_send_twilio) — a direct extraction,
#   logic unchanged. Shared infrastructure, not visit-only: used by
#   VisitService plus 3 endpoints that stay in agent.py for now
#   (collect_payment, notify_visit, notify_case) — those callers only changed
#   their import source, not their behavior. Full detail + why: /changelog.md
# ───────────────────────────────────────────────────────────────────────────
"""
SMS/WhatsApp notification via Twilio.

Stateless by design (no DB dependency) — kept as a class rather than bare
module functions to match this codebase's service pattern (VisitService,
CaseService, AuthService).
"""
from __future__ import annotations

from app.core.config import settings


class NotificationService:
    @staticmethod
    def normalize_phone(raw: str) -> str:
        p = raw.strip().lstrip("+")
        if not p.startswith("91"):
            p = "91" + p.lstrip("0")
        return p

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
