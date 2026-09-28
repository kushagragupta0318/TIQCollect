# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-09-28 — NEW (P2 G02, ce). Manage Agents: create. Deliberately a
#   separate router file sharing manager.py's own "/manager" prefix, not new
#   routes added to manager.py itself (rule 6, standalone tasks: "No new
#   logic in manager.py. New code goes to services/ and new routers.") —
#   GET /manager/agents (the list) stays exactly where it is, in manager.py;
#   POST /manager/agents (this file) answers the same path from a router
#   FastAPI mounts alongside it.
#
#   Gated by require_perm("agents.manage"), not ManagerOnly — the capability
#   registry (A01) is the one place "who may create an agent" is declared;
#   this route does not restate AGENCY_MANAGER/AGENCY_ADMIN itself.
#
#   2026-09-28 (later) — wired to password_service.issue_first_password
#   (d4's p1-d4@12c3232, built for exactly this ask) once it landed. The two
#   calls are deliberately separate, not one transaction: create_agent
#   commits the agent first, so an SMS delivery failure can never roll back
#   an agent record the manager can already see in their roster. The route
#   reports `activation` alongside the agent so the UI can tell the manager
#   "created, but the link could not be sent — use Reset Login" rather than
#   silently losing that information.
# ────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.dependencies import DbSession
from app.core.errors import AppException
from app.core.permissions import require_perm
from app.models.agent import AgentSpecialization
from app.models.user import User
from app.services import password_service
from app.services.agent_management_service import create_agent

router = APIRouter(prefix="/manager", tags=["manager-agents-admin"])


class CreateAgentRequest(BaseModel):
    full_name: str
    email: str
    phone: str
    employee_code: str
    id_card_number: str
    base_latitude: float
    base_longitude: float
    territory: str
    territory_region_id: str | None = None
    gender: str | None = None
    specialization: AgentSpecialization | None = None
    vehicle_type: str | None = None
    max_cases_per_day: int | None = None
    languages_spoken: list[str] | None = None


@router.post("/agents")
def create_agent_route(
    body: CreateAgentRequest, request: Request, db: DbSession,
    current_user: User = require_perm("agents.manage"),
):
    result = create_agent(
        db, current_user, full_name=body.full_name, email=body.email, phone=body.phone,
        employee_code=body.employee_code, id_card_number=body.id_card_number,
        base_latitude=body.base_latitude, base_longitude=body.base_longitude, territory=body.territory,
        territory_region_id=body.territory_region_id, gender=body.gender,
        specialization=body.specialization, vehicle_type=body.vehicle_type,
        max_cases_per_day=body.max_cases_per_day, languages_spoken=body.languages_spoken,
        request=request,
    )
    # The agent row is already committed at this point (create_agent's own
    # commit) — everything below is best-effort on top of a row that exists
    # either way, matching this codebase's "notification delivery never
    # rolls back the thing it's about" convention (NotificationService).
    agent_user = db.get(User, result["user_id"])
    try:
        activation = password_service.issue_first_password(db, current_user, agent_user, request=request)
    except AppException as exc:
        # The expected failure mode is 503 (no PUBLIC_BASE_URL configured) —
        # caught narrowly so a real bug in this wiring (anything else) still
        # surfaces as a 500 instead of reading as "the SMS didn't send".
        activation = {"sent": False, "error": exc.detail}
    result["activation"] = activation
    return result
