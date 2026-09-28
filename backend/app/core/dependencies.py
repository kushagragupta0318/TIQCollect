from typing import Annotated
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.models.user import User, UserRole
from app.core.audit import write_audit
from app.models.audit_log import AuditAction

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def _get_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    try:
        payload = decode_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    return payload


TokenPayload = Annotated[dict, Depends(_get_token_payload)]


def get_current_user(
    payload: TokenPayload,
    db: DbSession,
) -> User:
    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    # 2026-09-24 (A05) — a token issued under a session stops working the
    # moment that session is revoked (logout, admin revoke, reuse detected),
    # not 15 minutes later when it expires. A token with no sid (minted
    # directly: service accounts, tests) has no session to check.
    sid = payload.get("sid")
    if sid:
        from app.models.identity import UserSession
        session = db.get(UserSession, sid)
        if session is None or session.user_id != user.id or session.revoked_at is not None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session ended")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole):
    def _checker(current_user: CurrentUser, request: Request, db: DbSession) -> User:
        if current_user.role not in roles:
            # 2026-09-11 — ROLE_VIOLATION_ATTEMPT had been declared since the
            # first schema and written by nothing, so an agent's token being
            # pointed at a manager route left no trace anywhere. It is a
            # security event and it is recorded as one, before the refusal.
            # Attributed to the caller, so it appears in their own manager's
            # audit view. The 403 itself is unchanged, and write_audit cannot
            # raise: a failed row is logged at ERROR and the refusal proceeds.
            #
            # `db` is the request's session, not a second one — FastAPI caches
            # get_db per request, so this and the endpoint share it.
            write_audit(
                db, action=AuditAction.ROLE_VIOLATION_ATTEMPT,
                user_id=current_user.id, entity_type="Route",
                entity_id=f"{request.method} {request.url.path}",
                details={"required_roles": [r.value for r in roles],
                         "actual_role": current_user.role.value,
                         "method": request.method, "path": request.url.path},
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                success=False, failure_reason="role not permitted",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {[r.value for r in roles]}",
            )
        return current_user
    return _checker


ManagerOnly = Annotated[User, Depends(require_roles(UserRole.AGENCY_MANAGER, UserRole.AGENCY_ADMIN))]
AgentOnly = Annotated[User, Depends(require_roles(UserRole.FIELD_AGENT))]
AnyRole = Annotated[User, Depends(require_roles(UserRole.FIELD_AGENT, UserRole.AGENCY_MANAGER, UserRole.AGENCY_ADMIN))]

