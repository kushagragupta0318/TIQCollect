from pydantic import BaseModel, Field

from app.core.emails import AccountEmail


class LoginRequest(BaseModel):
    email: AccountEmail          # `.test` demo domains allowed (core/emails.py)
    password: str = Field(min_length=8, max_length=128)
    device_id: str = Field(min_length=8, max_length=128, description="Client-generated device identifier")


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
