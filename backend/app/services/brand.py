# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (A14 partial + coordinator audit gate 1) — NEW. The one answer to
#   "whose borrower is this, what do we call ourselves to them, and may we
#   contact them at all?"
#
#   Two defects share this root. "ABC Bank" was a literal in seven files of
#   SMS/WhatsApp text, the receipt and the UPI QR payee (A14). And the B22
#   demo-tenant suppression took a `demo_tenant=False` flag that NO caller
#   passed, so with DEMO_MODE off a demo tenant's invented borrowers would
#   have been texted (audit gate 1, HIGH). Both are fixed by resolving the
#   tenant from the ROWS — case, agent, agency, bank — here, once.
#
#   FAIL CLOSED. `tenant_of()` returns None when nothing resolves, and every
#   send treats None as a demo tenant: suppressed unless the number is on the
#   explicit allowlist. A lookup that fails must never read as "real
#   borrower, go ahead".
# ────────────────────────────────────────────────────────────────────────────
"""Tenant and brand resolution for anything that speaks to a borrower.

    brand = brand_for(db, case=case)        # text: brand.bank_name, brand.upi_payee_name
    NotificationService.send_sms(e164, body, db=db, case_id=case.id)   # resolves again, itself
"""
from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy.orm import Session

logger = structlog.get_logger()

# What a message says when no tenant resolves. Deliberately not a bank's name:
# a borrower must never be told a lender that is not theirs.
NEUTRAL_NAME = "your lender"


@dataclass(frozen=True)
class Tenant:
    bank_id: str
    agency_id: str | None
    bank_name: str                  # banks.display_name
    agency_name: str | None         # agencies.trade_name or legal_name
    upi_payee_name: str             # brand.upi_payee_name, else the bank's display name
    upi_vpa: str | None
    sms_sender_id: str | None
    is_demo: bool                   # bank OR agency flagged demo: invented people, never contacted


def tenant_of(db: Session, *, case=None, case_id: str | None = None, agent=None, agent_id: str | None = None,
              agency_id: str | None = None, bank_id: str | None = None,
              user=None, user_id: str | None = None) -> Tenant | None:
    """Resolve from the most specific subject given. None when nothing
    resolves — callers must treat that as 'do not contact' (fail closed)."""
    from app.models.agent import Agent
    from app.models.case import Case
    from app.models.tenancy import Agency, Bank
    from app.models.user import User

    try:
        if case is None and case_id:
            case = db.get(Case, case_id)
        if case is not None:
            bank_id, agency_id = case.bank_id, case.agency_id
        else:
            if agent is None and agent_id:
                agent = db.get(Agent, agent_id)
            if agent is not None:
                bank_id, agency_id = agent.bank_id, agent.agency_id
            else:
                if user is None and user_id:
                    user = db.get(User, user_id)
                if user is not None:
                    bank_id, agency_id = user.bank_id, user.agency_id
        agency = db.get(Agency, agency_id) if agency_id else None
        if agency is not None:
            bank_id = bank_id or agency.bank_id
        bank = db.get(Bank, bank_id) if bank_id else None
    except Exception as exc:  # noqa: BLE001 — a failed lookup is a "no", never a crash in a send path
        logger.error("brand.tenant_lookup_failed", error=str(exc), error_type=type(exc).__name__)
        return None
    if bank is None:
        return None
    brand = bank.brand or {}
    return Tenant(
        bank_id=bank.id,
        agency_id=agency.id if agency is not None else None,
        bank_name=bank.display_name,
        agency_name=(agency.trade_name or agency.legal_name) if agency is not None else None,
        upi_payee_name=brand.get("upi_payee_name") or bank.display_name,
        upi_vpa=brand.get("upi_vpa"),
        sms_sender_id=brand.get("sms_sender_id"),
        is_demo=bool(bank.is_demo or (agency is not None and agency.is_demo)),
    )


@dataclass(frozen=True)
class Brand:
    """What to CALL ourselves in text. Never used to decide whether to send."""
    bank_name: str
    agency_name: str | None
    upi_payee_name: str
    upi_vpa: str | None


def brand_for(db: Session, **subject) -> Brand:
    t = tenant_of(db, **subject)
    if t is None:
        return Brand(bank_name=NEUTRAL_NAME, agency_name=None, upi_payee_name=NEUTRAL_NAME, upi_vpa=None)
    return Brand(bank_name=t.bank_name, agency_name=t.agency_name, upi_payee_name=t.upi_payee_name,
                 upi_vpa=t.upi_vpa)
