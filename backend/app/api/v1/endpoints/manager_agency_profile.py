# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-10-01 — NEW (P2 G04, L9). Agency profile: read-only contract,
#   commission and SLA, for AGENCY_ADMIN only (plan §10, "Also for
#   AGENCY_ADMIN"). A separate router sharing manager.py's "/manager" prefix,
#   not a new route in manager.py (rule 6, standalone tasks).
# ────────────────────────────────────────────────────────────────────────────
from fastapi import APIRouter

from app.core.dependencies import DbSession
from app.core.permissions import require_perm
from app.models.user import User
from app.services.agency_profile_service import get_agency_profile

router = APIRouter(prefix="/manager", tags=["manager-agency-profile"])


@router.get("/agency-profile")
def agency_profile(db: DbSession, current_user: User = require_perm("agency.profile.read")):
    return get_agency_profile(db, current_user)
