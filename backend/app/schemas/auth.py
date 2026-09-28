from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    device_id: str = Field(min_length=8, max_length=128, description="Client-generated device identifier")
    # 2026-09-28 (A08, d4): required once the user has enrolled in TOTP.
    totp_code: Optional[str] = Field(default=None, pattern=r"^\d{6}$")


class NextStepResponse(BaseModel):
    """2026-09-28 (A07/A08, d4) — a correct password that does NOT open a
    session: the account must set a new password first (CHANGE_PASSWORD, with
    a single-use reset_token) or enroll in TOTP (ENROLL_MFA, with an
    enrollment_token)."""
    next: Literal["CHANGE_PASSWORD", "ENROLL_MFA"]
    message: str
    reset_token: Optional[str] = None
    enrollment_token: Optional[str] = None


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    role: str
    user_id: str
    full_name: str


# collection_dashboard: quick-login link exchange, no password required
class QuickLoginRequest(BaseModel):
    token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class MessageResponse(BaseModel):
    message: str
