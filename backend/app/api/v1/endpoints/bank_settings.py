# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-10-07 — NEW (K01 Admin → Settings). GET /bank/settings reads the bank's
#   config; PATCH /bank/settings writes it. Both gated by bank.settings.manage
#   (BANK_ADMIN only, §5.2) — the same authority reads and edits a bank's own
#   settings, so there is no separate read capability to invent and seed.
#   Tenant scope is implicit: the service resolves the bank from
#   current_user.bank_id, so a bank admin can only ever read or write their own
#   tenant's row. Typed Pydantic in/out; the PATCH stages one audit row.
#
#   No `from __future__ import annotations` — see bank_agencies_admin.py's note:
#   it breaks FastAPI/Pydantic 2.10's body-vs-query resolution on a request
#   model, live-server only, invisible to TestClient.
# ────────────────────────────────────────────────────────────────────────────
from fastapi import APIRouter, Request

from app.core.dependencies import DbSession
from app.core.permissions import require_perm
from app.models.user import User
from app.schemas.bank_settings import BankSettingsResponse, UpdateBankSettingsRequest
from app.services.bank import bank_settings_service

router = APIRouter(prefix="/bank", tags=["bank-settings"])


@router.get("/settings", response_model=BankSettingsResponse)
def get_bank_settings_route(db: DbSession, current_user: User = require_perm("bank.settings.manage")):
    return bank_settings_service.get_settings(db, current_user)


@router.patch("/settings", response_model=BankSettingsResponse)
def update_bank_settings_route(body: UpdateBankSettingsRequest, request: Request, db: DbSession,
                               current_user: User = require_perm("bank.settings.manage")):
    return bank_settings_service.update_settings(db, current_user, request=request, **body.model_dump())
