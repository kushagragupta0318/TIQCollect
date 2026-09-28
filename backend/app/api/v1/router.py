from fastapi import APIRouter
from app.api.v1.endpoints import accounts, auth, health, agent, manager, verify, events

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
# 2026-09-28 (P1 A06-A08, d4): invites, passwords, MFA.
api_router.include_router(accounts.router)
api_router.include_router(accounts.admin_router)
api_router.include_router(health.router)
api_router.include_router(verify.router)
api_router.include_router(agent.router)
api_router.include_router(manager.router)
api_router.include_router(events.router)
