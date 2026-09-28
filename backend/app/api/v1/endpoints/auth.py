from fastapi import APIRouter, Request
from app.core.dependencies import DbSession, CurrentUser, TokenPayload
from app.core.ratelimit import AUTH_LIMIT, limiter
from app.schemas.auth import LoginRequest, LoginResponse, QuickLoginRequest, RefreshRequest, TokenResponse, MessageResponse
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=LoginResponse, summary="Login — returns JWT access + refresh tokens")
@limiter.limit(AUTH_LIMIT)
async def login(body: LoginRequest, request: Request, db: DbSession):
    return auth_service.login(db, body.email, body.password, body.device_id, request,
                              device_secret=body.device_secret)


# collection_dashboard: lets the multi-agency dashboard deep-link into a manager's session
@router.post("/quick-login", response_model=LoginResponse, summary="Exchange a pre-signed link token for a real session")
@limiter.limit(AUTH_LIMIT)
async def quick_login(body: QuickLoginRequest, request: Request, db: DbSession):
    return auth_service.quick_login(db, body.token, request)


@router.post("/refresh", response_model=TokenResponse, summary="Rotate access + refresh token pair")
async def refresh(body: RefreshRequest, request: Request, db: DbSession):
    return auth_service.refresh_tokens(db, body.refresh_token, request)


@router.post("/logout", response_model=MessageResponse, summary="End this device's session")
async def logout(current_user: CurrentUser, payload: TokenPayload, request: Request, db: DbSession):
    # 2026-09-24 (A05): ends THIS session only — the summary used to say "all
    # sessions", which was true only because there was a single slot.
    auth_service.logout(db, current_user, request, sid=payload.get("sid"))
    return {"message": "Logged out successfully"}


@router.get("/me", summary="Get current authenticated user info")
def me(current_user: CurrentUser, db: DbSession):
    from app.services.brand import tenant_of
    tenant = tenant_of(db, user=current_user)
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role.value,
        "is_active": current_user.is_active,
        # A14: the header names the tenant. None when nothing resolves.
        "bank_name": tenant.bank_name if tenant else None,
        "agency_name": tenant.agency_name if tenant else None,
    }
