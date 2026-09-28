"""Request bodies for invites, passwords and MFA (P1 A06-A08, d4, 2026-09-28).

Tokens travel in request BODIES, never in URLs a proxy or browser history
would keep. Lengths are bounded so nothing unbounded reaches bcrypt or a
lookup."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.core.emails import AccountEmail
from app.core.ids import UUIDStr
from app.models.user import UserRole

_TOKEN = Field(min_length=20, max_length=100)
_PASSWORD = Field(min_length=1, max_length=128)
_DEVICE = Field(min_length=8, max_length=128)
_CODE6 = Field(pattern=r"^\d{6}$")


class InviteCreate(BaseModel):
    email: AccountEmail              # `.test` demo domains allowed; EmailStr refuses them (43, 7fa1cf5)
    full_name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=10, max_length=20)
    role: UserRole
    agency_id: Optional[UUIDStr] = None
    bank_id: Optional[UUIDStr] = None           # PLATFORM_ADMIN only; ignored for everyone else
    channel: Literal["LINK", "SMS", "EMAIL"] = "LINK"


class TokenBody(BaseModel):
    token: str = _TOKEN


class InviteAccept(BaseModel):
    token: str = _TOKEN
    password: str = _PASSWORD
    device_id: str = _DEVICE


class PasswordChange(BaseModel):
    current_password: str = _PASSWORD
    new_password: str = _PASSWORD


class PasswordReset(BaseModel):
    token: str = _TOKEN
    new_password: str = _PASSWORD


class ForgotPassword(BaseModel):
    # Email OR phone, so not AccountEmail: an address is only looked up here,
    # never stored, and a malformed one simply matches nobody (same 202).
    identifier: str = Field(min_length=3, max_length=255, description="Email or phone")


class ForgotVerify(BaseModel):
    request_id: str = _TOKEN
    code: str = _CODE6


class MfaCode(BaseModel):
    code: str = _CODE6


class MfaTicketStart(BaseModel):
    enrollment_token: str = _TOKEN


class MfaTicketConfirm(BaseModel):
    enrollment_token: str = _TOKEN
    code: str = _CODE6
    device_id: str = _DEVICE
