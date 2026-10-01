# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-10-01 — NEW (P2 G04, L9). Agency profile: read-only contract,
#   commission and SLA, for AGENCY_ADMIN only (plan §10, "Also for
#   AGENCY_ADMIN"). A separate router sharing manager.py's "/manager" prefix,
#   not a new route in manager.py (rule 6, standalone tasks).
#
#   2026-10-01 (later, audit) — typed response_model. manager.py's "no typed
#   responses" (known issue 2) is not restated here just because this route
#   shares its prefix.
# ────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.dependencies import DbSession
from app.core.permissions import require_perm
from app.models.user import User
from app.services.agency_profile_service import get_agency_profile

router = APIRouter(prefix="/manager", tags=["manager-agency-profile"])


class AgencyProfileIdentity(BaseModel):
    legal_name: str
    trade_name: Optional[str] = None
    rbi_registration_no: Optional[str] = None
    status: str
    hq_city: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None


class AgencyProfileContract(BaseModel):
    contract_no: str
    status: str
    # Whether this is the ACTIVE contract, not the "nothing better exists"
    # fallback to the most recent one regardless of status — see
    # services/agency_profile_service.build_agency_profile.
    is_current: bool
    start_date: str
    end_date: str
    max_agents: Optional[int] = None
    max_placed_cases: Optional[int] = None
    max_visits_per_month: Optional[int] = None
    sla_first_visit_days: int
    recall_no_activity_days: Optional[int] = None
    recall_on_sla_breach: bool
    recall_at_contract_end: bool
    performance_bonus_pct: Optional[float] = None
    performance_target_pct: Optional[float] = None
    security_deposit: Optional[float] = None


class AgencyCommissionTerm(BaseModel):
    loan_type: str
    dpd_bucket: str
    commission_pct: float
    fixed_fee_per_resolution: Optional[float] = None


class AgencyProfileResponse(BaseModel):
    agency: AgencyProfileIdentity
    contract: Optional[AgencyProfileContract] = None
    commission_terms: list[AgencyCommissionTerm]


@router.get("/agency-profile", response_model=AgencyProfileResponse)
def agency_profile(db: DbSession, current_user: User = require_perm("agency.profile.read")):
    return get_agency_profile(db, current_user)
