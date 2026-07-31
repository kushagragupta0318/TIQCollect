from fastapi import APIRouter, Request
from app.core.dependencies import DbSession, CurrentUser
from app.schemas.auth import LoginRequest, LoginResponse, QuickLoginRequest, RefreshRequest, TokenResponse, MessageResponse
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=LoginResponse, summary="Login — returns JWT access + refresh tokens")
async def login(body: LoginRequest, request: Request, db: DbSession):
    return auth_service.login(db, body.email, body.password, body.device_id, request)


# collection_dashboard: lets the multi-agency dashboard deep-link into a manager's session
@router.post("/quick-login", response_model=LoginResponse, summary="Exchange a pre-signed link token for a real session")
async def quick_login(body: QuickLoginRequest, request: Request, db: DbSession):
    return auth_service.quick_login(db, body.token, request)


@router.post("/refresh", response_model=TokenResponse, summary="Rotate access + refresh token pair")
async def refresh(body: RefreshRequest, request: Request, db: DbSession):
    return auth_service.refresh_tokens(db, body.refresh_token, request)


@router.post("/logout", response_model=MessageResponse, summary="Invalidate all sessions for current user")
async def logout(current_user: CurrentUser, request: Request, db: DbSession):
    auth_service.logout(db, current_user, request)
    return {"message": "Logged out successfully"}


@router.get("/me", summary="Get current authenticated user info")
async def me(current_user: CurrentUser):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role.value,
        "is_active": current_user.is_active,
    }
