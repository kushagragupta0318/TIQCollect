from fastapi import APIRouter
from app.api.v1.endpoints import (
    accounts, auth, bank, bank_agencies_admin, health, agent, manager, manager_agents_admin, verify, events,
)

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
# 2026-09-28 (P2 D01/D02, ce): the onboarding wizard's bank-side routes.
api_router.include_router(bank_agencies_admin.router)
api_router.include_router(events.router)
# 2026-09-28 (P3 C01/C03, d4): the bank Command Center.
api_router.include_router(bank.router)
# 2026-09-29 (P3 D08, 2b): the bank's placement routes, /bank/placements. Imported on
# its own line so d4's p3-d4 edit of the import above merges without a conflict.
from app.api.v1.endpoints import bank_placements  # noqa: E402
api_router.include_router(bank_placements.router)
# 2026-09-30 (L5, AI showcase): the model pages, /bank/models and
# /bank/loans/{id}/explanation. Own line, for the same merge reason as above.
from app.api.v1.endpoints import bank_models  # noqa: E402
api_router.include_router(bank_models.router)
# 2026-10-01 (L5, C08): the bank's borrower page, /bank/customers/{id}/360 and
# /bank/cases/{id}/timeline. Own line, for the same merge reason as above.
from app.api.v1.endpoints import bank_customers  # noqa: E402
api_router.include_router(bank_customers.router)
# 2026-10-01 (P4 E01-E04, L2): the Monte Carlo strategy simulator, /bank/strategy/simulate.
# Own line, for the same merge reason as above.
from app.api.v1.endpoints import bank_strategy  # noqa: E402
api_router.include_router(bank_strategy.router)
# 2026-10-01 (P2 G04, L9): the agency's own read-only profile, /manager/agency-profile.
# Own line, same reason as bank_placements above.
from app.api.v1.endpoints import manager_agency_profile  # noqa: E402
api_router.include_router(manager_agency_profile.router)
# 2026-10-01 (#2, L7): payment reversal, two-stage agency→bank. Agency routes under
# /manager, bank routes under /bank. Own lines, for the same merge reason as above.
from app.api.v1.endpoints import payment_reversals  # noqa: E402
api_router.include_router(payment_reversals.agency_router)
api_router.include_router(payment_reversals.bank_router)
