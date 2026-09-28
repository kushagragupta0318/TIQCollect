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
#
#   2026-09-28 (later still) — suspend / reactivate / reset-login. All three
#   resolve the agent through scope.agents_in_scope (the same frozen
#   interface agent_management_service.create_agent's tenant binding relies
#   on), so a foreign or peer-manager's agent id is the same 404 whether it
#   belongs to another agency or simply isn't this manager's own. Reset
#   login calls password_service.admin_reset (an agent who reaches this
#   action has signed in before, so issue_first_password's own guard — 409
#   on anything but a brand-new account — rules it out; create_agent's call
#   above is the one case that IS brand new).
#
#   2026-09-28 (later still) — rate limiting. `@limiter.limit` counts per URL
#   PATH, so with {agent_id} in the path a plain limit gives every agent
#   their own ten-a-minute bucket and a caller spraying resets across a
#   whole roster is never bounded (d4 measured this on the equivalent admin
#   route: 12 calls to 12 ids, 12 x 404, no 429 — same bug, same fix).
#   reset-login shares "admin-credential-links" with
#   /admin/users/{id}/password-reset and mfa-reset (d4's p1-d4@23755e9): one
#   caller, ten credential actions a minute, across every route that texts
#   one. create_agent sends one too (issue_first_password) and joins the
#   same bucket for the same reason — creating agents in a loop must not be
#   a free way past the limit reset-login is under. suspend/reactivate send
#   no SMS and touch no credential, so they are not in this scope.
#
#   2026-09-28 (later still) — edit. PATCH, not PUT: every field optional,
#   omitted means unchanged. No SMS, no credential, no rate limit, same as
#   suspend/reactivate. Unlike them, a no-op edit (nothing actually
#   differs from the current row) is not an error — it returns
#   {"changed": []} rather than a 409, because unlike "suspend an already-
#   suspended agent" there is nothing conflicting about submitting a form
#   with no changes on it.
# ────────────────────────────────────────────────────────────────────────────
# No `from __future__ import annotations` here (2026-09-28, found on a live
# v2-preview smoke test, not by pytest): with it, FastAPI/Pydantic 2.10 fails
# to resolve the CreateAgentRequest forward ref — GET /openapi.json 500s and
# POST /manager/agents 422s ("body"/"db" reported as missing QUERY params).
# Confirmed by removing this one line: both cleared, verified over real HTTP.
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.dependencies import DbSession
from app.core.emails import AccountEmail
from app.core.errors import AppException, ErrorCode
from app.core.ids import UUIDPath, UUIDStr
from app.core.permissions import require_perm
from app.core.ratelimit import AUTH_LIMIT, limiter
from app.models.agent import AgentSpecialization
from app.models.user import User
from app.services import password_service
from app.services.agent_management_service import (
    create_agent, edit_agent, reactivate_agent, reset_agent_login, suspend_agent,
)

router = APIRouter(prefix="/manager", tags=["manager-agents-admin"])


class CreateAgentRequest(BaseModel):
    full_name: str
    email: AccountEmail   # 43's B15/core.emails — .test demo domains allowed
    phone: str
    employee_code: str
    id_card_number: str
    base_latitude: float
    base_longitude: float
    territory: str
    territory_region_id: UUIDStr | None = None
    gender: str | None = None
    specialization: AgentSpecialization | None = None
    vehicle_type: str | None = None
    # agent_management_service.create_agent re-validates all four against
    # Agent's own CHECK constraints / column widths regardless — these are
    # the fast, boundary-level rejection, not the only one.
    max_cases_per_day: int | None = Field(default=None, ge=1, le=50)
    languages_spoken: list[str] | None = Field(default=None, max_length=10)


@router.post("/agents")
@limiter.shared_limit(AUTH_LIMIT, scope="admin-credential-links")
async def create_agent_route(
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
        # tiq-auditor (self-requested review): this used to catch every
        # AppException, so a 404 from can_manage or a 409 from
        # never_had_a_password — genuine bugs in this wiring, since the
        # agent just created must always satisfy both — would have read as
        # an ordinary "the SMS didn't send" instead of the loud failure the
        # comment already claimed this gave. Narrowed to the one expected
        # failure mode (no PUBLIC_BASE_URL configured); anything else
        # re-raises as the 500 it actually is.
        if exc.code != ErrorCode.CHANNEL_UNAVAILABLE:
            raise
        activation = {"sent": False, "error": exc.detail}
    result["activation"] = activation
    return result


class EditAgentRequest(BaseModel):
    """Every field optional (edit_agent.py's own docstring): omitted means
    "leave unchanged", never "clear it". email, employee_code and
    id_card_number are deliberately absent — see edit_agent's docstring for
    why each is out of scope here."""
    full_name: str | None = None
    phone: str | None = None
    territory: str | None = None
    base_latitude: float | None = None
    base_longitude: float | None = None
    territory_region_id: UUIDStr | None = None
    gender: str | None = None
    specialization: AgentSpecialization | None = None
    vehicle_type: str | None = None
    max_cases_per_day: int | None = Field(default=None, ge=1, le=50)
    languages_spoken: list[str] | None = Field(default=None, max_length=10)


@router.patch("/agents/{agent_id}")
def edit_agent_route(
    agent_id: UUIDPath, body: EditAgentRequest, request: Request, db: DbSession,
    current_user: User = require_perm("agents.manage"),
):
    return edit_agent(
        db, current_user, agent_id, full_name=body.full_name, phone=body.phone, territory=body.territory,
        base_latitude=body.base_latitude, base_longitude=body.base_longitude,
        territory_region_id=body.territory_region_id, gender=body.gender,
        specialization=body.specialization, vehicle_type=body.vehicle_type,
        max_cases_per_day=body.max_cases_per_day, languages_spoken=body.languages_spoken,
        request=request,
    )


class SuspendAgentRequest(BaseModel):
    reason: str


@router.post("/agents/{agent_id}/suspend")
def suspend_agent_route(
    agent_id: UUIDPath, body: SuspendAgentRequest, request: Request, db: DbSession,
    current_user: User = require_perm("agents.manage"),
):
    return suspend_agent(db, current_user, agent_id, reason=body.reason, request=request)


@router.post("/agents/{agent_id}/reactivate")
def reactivate_agent_route(
    agent_id: UUIDPath, request: Request, db: DbSession,
    current_user: User = require_perm("agents.manage"),
):
    return reactivate_agent(db, current_user, agent_id, request=request)


@router.post("/agents/{agent_id}/reset-login")
@limiter.shared_limit(AUTH_LIMIT, scope="admin-credential-links")
async def reset_agent_login_route(
    agent_id: UUIDPath, request: Request, db: DbSession,
    current_user: User = require_perm("agents.manage"),
):
    return reset_agent_login(db, current_user, agent_id, request=request)
