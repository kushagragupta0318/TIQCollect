from fastapi import APIRouter
from app.api.v1.endpoints import accounts, auth, health, agent, manager, manager_agents_admin, verify, events

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
# 2026-09-28 (P1 A06-A08, d4): invites, passwords, MFA.
api_router.include_router(accounts.router)
api_router.include_router(accounts.admin_router)
api_router.include_router(health.router)
api_router.include_router(verify.router)
api_router.include_router(agent.router)
api_router.include_router(manager.router)
# 2026-09-28 (P2 G02, ce): Manage Agents' write side, sharing manager.py's
# "/manager" prefix from its own file (rule 6 — see that file's own header).
api_router.include_router(manager_agents_admin.router)
api_router.include_router(events.router)
# 2026-09-29 (P3 D08, 2b): the bank's placement routes, /bank/placements. Imported on
# its own line so d4's p3-d4 edit of the import above merges without a conflict.
from app.api.v1.endpoints import bank_placements  # noqa: E402
api_router.include_router(bank_placements.router)
