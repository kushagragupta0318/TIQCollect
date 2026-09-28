# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-09-28 — NEW (P2 D01/D02, ce). The bank portal's onboarding-wizard
#   routes: create draft, update identity, update coverage/contract, upload
#   and review documents, invite the master login, read back the draft.
#
#   Capability per route, not one blanket "agencies.manage" (coordinator's
#   call): agency.onboard for create/upload/invite, agency.update for
#   identity edits, agency.contract.manage for coverage/contract,
#   agency.documents.verify for the review actions, agency.read for GETs.
#   Every route already declared in core/permissions.py — nothing new there.
#
#   No `from __future__ import annotations` — see manager_agents_admin.py's
#   own note (2026-09-28): it breaks FastAPI/Pydantic 2.10's body-vs-query
#   resolution on a forward-referenced request model, live-server only,
#   invisible to TestClient. Learned once, not repeating it here.
# ────────────────────────────────────────────────────────────────────────────
from datetime import date

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.dependencies import DbSession
from app.core.emails import AccountEmail
from app.core.ids import UUIDPath
from app.core.permissions import require_perm
from app.core.ratelimit import AUTH_LIMIT, limiter
from app.models.user import User
from app.services.bank import agency_service

router = APIRouter(prefix="/bank", tags=["bank-agencies-admin"])


class CreateAgencyRequest(BaseModel):
    legal_name: str
    trade_name: str | None = None
    entity_type: str | None = None
    cin: str | None = None
    rbi_registration_no: str | None = None
    pan: str | None = None
    gstin: str | None = None
    registered_address: dict | None = None
    hq_city: str | None = None
    website: str | None = None
    contacts: list | None = None
    contact_name: str | None = None
    contact_email: AccountEmail | None = None
    contact_phone: str | None = None


@router.post("/agencies")
def create_agency_route(body: CreateAgencyRequest, request: Request, db: DbSession,
                        current_user: User = require_perm("agency.onboard")):
    return agency_service.create_draft(db, current_user, request=request, **body.model_dump())


class UpdateIdentityRequest(BaseModel):
    legal_name: str | None = None
    trade_name: str | None = None
    entity_type: str | None = None
    cin: str | None = None
    rbi_registration_no: str | None = None
    pan: str | None = None
    gstin: str | None = None
    registered_address: dict | None = None
    hq_city: str | None = None
    website: str | None = None
    contacts: list | None = None
    contact_name: str | None = None
    contact_email: AccountEmail | None = None
    contact_phone: str | None = None


@router.patch("/agencies/{agency_id}")
def update_identity_route(agency_id: UUIDPath, body: UpdateIdentityRequest, db: DbSession,
                          current_user: User = require_perm("agency.update")):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    return agency_service.update_identity(db, current_user, agency_id, **fields)


class ContractTermIn(BaseModel):
    loan_type: str
    dpd_bucket: str
    commission_pct: float
    fixed_fee_per_resolution: float | None = None
    is_authorised: bool = True


class UpdateCoverageContractRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    max_placed_cases: int | None = None
    max_agents: int | None = None
    max_visits_per_month: int | None = None
    sla_first_visit_days: int | None = None
    recall_no_activity_days: int | None = None
    recall_on_sla_breach: bool | None = None
    recall_at_contract_end: bool | None = None
    performance_bonus_pct: float | None = None
    performance_target_pct: float | None = None
    security_deposit: float | None = None
    region_ids: list[str] | None = None
    contract_terms: list[ContractTermIn] | None = None


@router.patch("/agencies/{agency_id}/coverage-contract")
def update_coverage_contract_route(agency_id: UUIDPath, body: UpdateCoverageContractRequest, db: DbSession,
                                   current_user: User = require_perm("agency.contract.manage")):
    fields = body.model_dump(exclude_none=True)
    if "contract_terms" in fields:
        fields["contract_terms"] = [t for t in fields["contract_terms"]]
    return agency_service.update_coverage_and_contract(db, current_user, agency_id, **fields)


class PresignDocumentRequest(BaseModel):
    doc_type: str
    content_type: str


@router.post("/agencies/{agency_id}/documents/presign")
def presign_document_route(agency_id: UUIDPath, body: PresignDocumentRequest, db: DbSession,
                           current_user: User = require_perm("agency.onboard")):
    return agency_service.presign_document(db, current_user, agency_id, body.doc_type, body.content_type)


class ConfirmDocumentRequest(BaseModel):
    doc_type: str
    key: str
    file_name: str | None = None
    issued_on: date | None = None
    expires_on: date | None = None


@router.post("/agencies/{agency_id}/documents")
def confirm_document_route(agency_id: UUIDPath, body: ConfirmDocumentRequest, db: DbSession,
                           current_user: User = require_perm("agency.onboard")):
    return agency_service.confirm_document(db, current_user, agency_id, **body.model_dump())


@router.post("/agencies/{agency_id}/documents/{doc_id}/verify")
def verify_document_route(agency_id: UUIDPath, doc_id: UUIDPath, request: Request, db: DbSession,
                          current_user: User = require_perm("agency.documents.verify")):
    return agency_service.verify_document(db, current_user, agency_id, doc_id, request=request)


class RejectDocumentRequest(BaseModel):
    reason: str


@router.post("/agencies/{agency_id}/documents/{doc_id}/reject")
def reject_document_route(agency_id: UUIDPath, doc_id: UUIDPath, body: RejectDocumentRequest, request: Request,
                          db: DbSession, current_user: User = require_perm("agency.documents.verify")):
    return agency_service.reject_document(db, current_user, agency_id, doc_id, reason=body.reason, request=request)


class InviteMasterLoginRequest(BaseModel):
    full_name: str
    email: AccountEmail
    phone: str
    channel: str = "LINK"


@router.post("/agencies/{agency_id}/invite-master-login")
@limiter.shared_limit(AUTH_LIMIT, scope="admin-credential-links")
def invite_master_login_route(agency_id: UUIDPath, body: InviteMasterLoginRequest, request: Request, db: DbSession,
                              current_user: User = require_perm("agency.onboard")):
    return agency_service.invite_master_login(
        db, current_user, agency_id, full_name=body.full_name, email=body.email, phone=body.phone,
        channel=body.channel, request=request,
    )


@router.get("/agencies/{agency_id}")
def get_agency_route(agency_id: UUIDPath, db: DbSession, current_user: User = require_perm("agency.read")):
    return agency_service.get_agency_detail(db, current_user, agency_id)


@router.get("/agencies")
def list_agencies_route(db: DbSession, status: str | None = None,
                        current_user: User = require_perm("agency.read")):
    return agency_service.list_agencies(db, current_user, status=status)


@router.get("/regions")
def list_regions_route(db: DbSession, current_user: User = require_perm("agency.contract.manage")):
    """The Coverage step's checklist source. Gated the same as the route
    that consumes the ids it returns (coverage-contract), not agency.read —
    reading the region hierarchy is closer to "who may set coverage" than
    "who may see an agency's onboarding progress"."""
    return agency_service.list_regions(db, current_user)


@router.get("/agencies-directory")
def list_agency_directory_route(
    db: DbSession, region_id: str | None = None, status: str | None = None, loan_type: str | None = None,
    contract_expiring_before: date | None = None, current_user: User = require_perm("agency.read"),
):
    """D05: the directory table + coverage map. A separate route from
    GET /agencies (not a query param on it) — that one is the wizard's own
    plain list and callers of it should not have to pay for a contract +
    coverage + product join they never asked for."""
    return agency_service.list_agency_directory(
        db, current_user, region_id=region_id, status=status, loan_type=loan_type,
        contract_expiring_before=contract_expiring_before,
    )
