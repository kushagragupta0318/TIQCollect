# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-10-06 — NEW (P3 D07). The bank's agency profile: clicking any
#   ACTIVE/SUSPENDED/OFFBOARDED row in the Directory used to do nothing
#   (AgencyDirectoryPage.openRow's own comment named this gap). Reuses
#   services.agency_profile_service.build_agency_profile — the agency's own
#   G04 view and this one read the same contract/commission rule, never two
#   copies of it — and adds the two things only the bank sees: placed volume
#   and the agency's people (managers/agents). Performance is NOT recomputed
#   here: D06's agency_scorecard already owns it; this profile links to that
#   page instead (frontend/src/bank/pages/directory, ?agency=).
# ────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.agent import Agent, AgentStatus
from app.models.placement import Placement
from app.models.tenancy import Agency
from app.models.user import User, UserRole
from app.services.agency_profile_service import build_agency_profile


def bank_agency_profile(db: Session, agency: Agency) -> dict:
    """`agency` is already resolved and bank-scoped by the caller
    (scope.agency_or_404) — same contract as build_agency_profile itself:
    no tenant logic lives here."""
    out = build_agency_profile(db, agency)

    active_count, active_exposure = (
        db.query(func.count(Placement.id), func.coalesce(func.sum(Placement.exposure_at_placement), 0.0))
        .filter(Placement.agency_id == agency.id, Placement.status == "ACTIVE")
        .first()
    )
    lifetime_count = db.query(func.count(Placement.id)).filter(Placement.agency_id == agency.id).scalar() or 0

    agents = db.query(Agent).filter(Agent.agency_id == agency.id).all()
    managers = (
        db.query(User)
        .filter(User.agency_id == agency.id, User.role.in_([UserRole.AGENCY_MANAGER, UserRole.AGENCY_ADMIN]))
        .order_by(User.full_name)
        .all()
    )

    out["placed_volume"] = {
        "active_count": int(active_count or 0),
        "active_exposure": round(float(active_exposure or 0.0), 2),
        "lifetime_count": int(lifetime_count),
    }
    out["people"] = {
        "agent_count": len(agents),
        "agents_on_duty": sum(1 for a in agents if a.status == AgentStatus.ON_DUTY),
        "managers": [
            {"id": m.id, "full_name": m.full_name, "email": m.email, "role": m.role.value}
            for m in managers
        ],
    }
    return out
