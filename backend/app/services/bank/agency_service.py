# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-09-28 (P2 D01/D02, ce) — NEW. The bank-side half of the onboarding
#   wizard: create a draft agency, fill it in step by step, upload documents,
#   invite the master login, and let two independent events — every required
#   document VERIFIED, and the master invite ACCEPTED — flip it to ACTIVE.
#
#   Deliberately does NOT build D01's full description (also: activate,
#   suspend, renew). Suspend/renew are agency-profile lifecycle actions,
#   closer to D04/G04 territory, and were scoped out by the coordinator for
#   this first cut. "Activate" here is never a direct action — it is the
#   automatic outcome of the two conditions above, from _maybe_activate,
#   never a route a bank user calls.
#
#   Document review is genuinely four-eyes: the verifier may not be the
#   uploader. Both "you uploaded this" and "already decided" refuse with the
#   SAME 409 (_refuse_review) — same reasoning as scope.py's uniform 404,
#   just one level up: a caller must not be able to tell which reason it was.
#
#   Nothing here trusts the client for evidence. The object key is always
#   server-generated (storage.agency_document_key); confirm_document HEADs
#   the object for its real size/content-type and downloads it to compute a
#   real sha256 — a client-reported hash is never written. scan_status stays
#   PENDING (this repo has no virus-scan hook yet; faking "CLEAN" would be
#   worse than saying nothing).
# ────────────────────────────────────────────────────────────────────────────
"""Bank-side agency onboarding: draft -> identity/coverage/contract ->
documents -> master-login invite -> automatic activation."""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone

from fastapi import Request
from minio.error import S3Error
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import storage
from app.core.audit import stage_audit, write_audit
from app.core.errors import AppException, ErrorCode
from app.models.audit_log import AuditAction
from app.models.loan import DPDBucket, LoanType
from app.models.tenancy import (
    DOC_TYPES,
    Agency,
    AgencyContract,
    AgencyContractTerm,
    AgencyDocument,
    AgencyRegion,
    Region,
)
from app.models.user import User, UserRole
from app.services.scope import agency_or_404, agencies_in_scope

REQUIRED_DOC_TYPES = ("REGISTRATION_CERT", "AGREEMENT", "INSURANCE", "POLICE_VERIFICATION_POLICY")

# What a document upload is allowed to be. A scanned certificate or a signed
# PDF, nothing executable, nothing open-ended — content_type is checked
# against the object MinIO actually stored, never the client's claim alone.
_ALLOWED_DOC_CONTENT_TYPES = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}
_MAX_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024   # 10 MB — certificates and signed agreements, not video

_IDENTITY_FIELDS = (
    "legal_name", "trade_name", "entity_type", "cin", "rbi_registration_no", "pan", "gstin",
    "registered_address", "hq_city", "website", "contacts", "contact_name", "contact_email", "contact_phone",
)


def _client_ip(request: Request | None) -> str | None:
    return request.client.host if request is not None and request.client else None


def _require_bank_admin(principal: User) -> None:
    if principal.role != UserRole.BANK_ADMIN or not principal.bank_id:
        raise AppException(403, ErrorCode.FORBIDDEN, "Not permitted.")


def _generate_code(db: Session, bank_id: str, legal_name: str) -> str:
    """AGY-<initials>-<4 hex>, retried on collision within the bank. Not a
    business identifier — agencies.code has no meaning the wizard's own
    fields don't already carry; it exists because the table needs one."""
    initials = "".join(w[0] for w in (legal_name or "").upper().split() if w.isalnum())[:4] or "AGY"
    for _ in range(10):
        code = f"AGY-{initials}-{uuid.uuid4().hex[:4].upper()}"
        if not db.query(Agency).filter(Agency.bank_id == bank_id, Agency.code == code).first():
            return code
    raise AppException(500, ErrorCode.CONFLICT, "Could not generate a unique agency code. Try again.")


def create_draft(db: Session, bank_admin: User, *, legal_name: str, trade_name: str | None = None,
                 entity_type: str | None = None, cin: str | None = None, rbi_registration_no: str | None = None,
                 pan: str | None = None, gstin: str | None = None, registered_address: dict | None = None,
                 hq_city: str | None = None, website: str | None = None, contacts: list | None = None,
                 contact_name: str | None = None, contact_email: str | None = None,
                 contact_phone: str | None = None, request: Request | None = None) -> dict:
    """Step 1 (Identity). Creates the Agency row in PENDING. Nothing else —
    coverage, contract, documents and the invite are all separate calls the
    wizard makes once it has an agency_id to attach them to."""
    _require_bank_admin(bank_admin)
    legal_name = (legal_name or "").strip()
    if not legal_name:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Legal name is required.")

    agency = Agency(
        bank_id=bank_admin.bank_id, code=_generate_code(db, bank_admin.bank_id, legal_name),
        legal_name=legal_name, trade_name=(trade_name or "").strip() or None,
        entity_type=entity_type, cin=cin, rbi_registration_no=rbi_registration_no, pan=pan, gstin=gstin,
        registered_address=registered_address, hq_city=hq_city, website=website,
        contacts=contacts or [], contact_name=contact_name, contact_email=contact_email,
        contact_phone=contact_phone, status="PENDING", created_by=bank_admin.id,
    )
    db.add(agency)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise AppException(409, ErrorCode.CONFLICT, "Could not create the agency. Try again.")
    stage_audit(db, action=AuditAction.AGENCY_ONBOARDED, user_id=bank_admin.id, entity_type="Agency",
               entity_id=agency.id, details={"step": "identity", "legal_name": legal_name})
    db.commit()
    return _agency_dict(agency)


def update_identity(db: Session, bank_admin: User, agency_id: str, **fields) -> dict:
    """Step 1 revisited. Every field optional — omitted means unchanged,
    same convention as edit_agent. Unknown keys are rejected rather than
    silently ignored, so a frontend typo fails loud."""
    _require_bank_admin(bank_admin)
    agency = agency_or_404(db, bank_admin, agency_id)
    unknown = set(fields) - set(_IDENTITY_FIELDS)
    if unknown:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, f"Unknown field(s): {', '.join(sorted(unknown))}")
    changed = {}
    for key, value in fields.items():
        if value is None:
            continue
        current = getattr(agency, key)
        if isinstance(value, str):
            value = value.strip()
        if value != current:
            changed[key] = {"from": current, "to": value}
            setattr(agency, key, value)
    if not changed:
        return {"agency_id": agency.id, "changed": []}
    stage_audit(db, action=AuditAction.AGENCY_ONBOARDED, user_id=bank_admin.id, entity_type="Agency",
               entity_id=agency.id, details={"step": "identity", "changed": list(changed)})
    db.commit()
    return {"agency_id": agency.id, "changed": list(changed)}


def _upsert_draft_contract(db: Session, agency: Agency) -> AgencyContract:
    contract = (db.query(AgencyContract)
               .filter(AgencyContract.agency_id == agency.id, AgencyContract.status == "DRAFT")
               .first())
    if contract is None:
        contract = AgencyContract(
            bank_id=agency.bank_id, agency_id=agency.id, contract_no=f"DRAFT-{uuid.uuid4().hex[:10].upper()}",
            start_date=date.today(), end_date=date.today(), status="DRAFT",
        )
        db.add(contract)
        db.flush()
    return contract


def update_coverage_and_contract(
    db: Session, bank_admin: User, agency_id: str, *,
    start_date: date | None = None, end_date: date | None = None,
    max_placed_cases: int | None = None, max_agents: int | None = None,
    max_visits_per_month: int | None = None, sla_first_visit_days: int | None = None,
    recall_no_activity_days: int | None = None, recall_on_sla_breach: bool | None = None,
    recall_at_contract_end: bool | None = None, performance_bonus_pct: float | None = None,
    performance_target_pct: float | None = None, security_deposit: float | None = None,
    region_ids: list[str] | None = None,
    contract_terms: list[dict] | None = None,
) -> dict:
    """Steps 2 (Coverage) and 3 (Contract) together — one DRAFT
    AgencyContract per agency, upserted, with its region and term rows
    replaced wholesale each call (the wizard resubmits the whole step, not a
    diff, so replace-in-place is simpler and cannot leave an orphaned row
    from an earlier, since-changed submission)."""
    _require_bank_admin(bank_admin)
    agency = agency_or_404(db, bank_admin, agency_id)
    contract = _upsert_draft_contract(db, agency)

    if start_date is not None:
        contract.start_date = start_date
    if end_date is not None:
        contract.end_date = end_date
    if contract.end_date < contract.start_date:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Contract end date must be on or after the start date.")
    for field, value in (
        ("max_placed_cases", max_placed_cases), ("max_agents", max_agents),
        ("max_visits_per_month", max_visits_per_month), ("recall_no_activity_days", recall_no_activity_days),
        ("performance_bonus_pct", performance_bonus_pct), ("performance_target_pct", performance_target_pct),
        ("security_deposit", security_deposit),
    ):
        if value is not None:
            setattr(contract, field, value)
    if sla_first_visit_days is not None:
        if sla_first_visit_days < 1:
            raise AppException(422, ErrorCode.VALIDATION_ERROR, "SLA first-visit window must be at least 1 day.")
        contract.sla_first_visit_days = sla_first_visit_days
    if recall_on_sla_breach is not None:
        contract.recall_on_sla_breach = recall_on_sla_breach
    if recall_at_contract_end is not None:
        contract.recall_at_contract_end = recall_at_contract_end

    if region_ids is not None:
        valid_ids = {r.id for r in db.query(Region.id)
                    .filter(Region.bank_id == agency.bank_id, Region.id.in_(region_ids)).all()}
        missing = set(region_ids) - valid_ids
        if missing:
            raise AppException(422, ErrorCode.VALIDATION_ERROR, f"Unknown region id(s): {', '.join(sorted(missing))}")
        db.query(AgencyRegion).filter(AgencyRegion.contract_id == contract.id).delete()
        for rid in region_ids:
            db.add(AgencyRegion(bank_id=agency.bank_id, agency_id=agency.id, contract_id=contract.id, region_id=rid))

    if contract_terms is not None:
        db.query(AgencyContractTerm).filter(AgencyContractTerm.contract_id == contract.id).delete()
        for term in contract_terms:
            loan_type, bucket = term.get("loan_type"), term.get("dpd_bucket")
            pct = term.get("commission_pct")
            if loan_type not in LoanType.__members__ and loan_type not in [m.value for m in LoanType]:
                raise AppException(422, ErrorCode.VALIDATION_ERROR, f"Unknown loan_type: {loan_type}")
            if bucket not in DPDBucket.__members__ and bucket not in [m.value for m in DPDBucket]:
                raise AppException(422, ErrorCode.VALIDATION_ERROR, f"Unknown dpd_bucket: {bucket}")
            if pct is None or not (0 <= pct <= 100):
                raise AppException(422, ErrorCode.VALIDATION_ERROR, "commission_pct must be between 0 and 100.")
            db.add(AgencyContractTerm(
                bank_id=agency.bank_id, agency_id=agency.id, contract_id=contract.id,
                loan_type=loan_type, dpd_bucket=bucket, commission_pct=pct,
                fixed_fee_per_resolution=term.get("fixed_fee_per_resolution"),
                is_authorised=term.get("is_authorised", True),
            ))

    stage_audit(db, action=AuditAction.AGENCY_ONBOARDED, user_id=bank_admin.id, entity_type="Agency",
               entity_id=agency.id, details={"step": "coverage_contract", "contract_id": contract.id})
    db.commit()
    return _agency_dict(agency)


def presign_document(db: Session, bank_admin: User, agency_id: str, doc_type: str, content_type: str) -> dict:
    """Step 4, part 1. Returns a server-generated key and a short-lived
    presigned PUT URL — the client uploads directly to MinIO and never
    chooses its own key."""
    _require_bank_admin(bank_admin)
    agency = agency_or_404(db, bank_admin, agency_id)
    if doc_type not in DOC_TYPES:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, f"doc_type must be one of: {', '.join(DOC_TYPES)}")
    if content_type not in _ALLOWED_DOC_CONTENT_TYPES:
        raise AppException(422, ErrorCode.VALIDATION_ERROR,
                           f"content_type must be one of: {', '.join(_ALLOWED_DOC_CONTENT_TYPES)}")
    ext = _ALLOWED_DOC_CONTENT_TYPES[content_type]
    key = storage.agency_document_key(agency.id, doc_type, ext=ext)
    upload_url = storage.presigned_upload_url(key, content_type=content_type, expires_minutes=15)
    return {"upload_url": upload_url, "key": key, "doc_type": doc_type}


def confirm_document(db: Session, bank_admin: User, agency_id: str, *, doc_type: str, key: str,
                     file_name: str | None = None, issued_on: date | None = None,
                     expires_on: date | None = None) -> dict:
    """Step 4, part 2. Trusts nothing the client says about the file it
    claims to have uploaded — only that the key came from presign_document
    (checked by prefix) and whatever MinIO itself reports."""
    _require_bank_admin(bank_admin)
    agency = agency_or_404(db, bank_admin, agency_id)
    if doc_type not in DOC_TYPES:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, f"doc_type must be one of: {', '.join(DOC_TYPES)}")
    if not key.startswith(f"agencies/{agency.id[:8]}/"):
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "That key was not issued for this agency.")

    try:
        stat = storage.stat_object(key)
    except S3Error:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Nothing was uploaded to that key yet.")
    content_type = (stat.content_type or "").split(";")[0].strip()
    if content_type not in _ALLOWED_DOC_CONTENT_TYPES:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, f"Unsupported file type: {content_type or 'unknown'}")
    if stat.size > _MAX_DOCUMENT_SIZE_BYTES:
        raise AppException(422, ErrorCode.VALIDATION_ERROR,
                           f"File is {stat.size // 1024} KB; the limit is {_MAX_DOCUMENT_SIZE_BYTES // 1024} KB.")
    sha256 = hashlib.sha256(storage.download_bytes(key)).hexdigest()

    doc = AgencyDocument(
        bank_id=agency.bank_id, agency_id=agency.id, doc_type=doc_type, storage_key=key,
        file_name=file_name, content_type=content_type, size_bytes=stat.size, sha256=sha256,
        scan_status="PENDING", status="UPLOADED", issued_on=issued_on, expires_on=expires_on,
        uploaded_by=bank_admin.id,
    )
    db.add(doc)
    db.flush()
    stage_audit(db, action=AuditAction.DOCUMENT_UPLOADED, user_id=bank_admin.id, entity_type="AgencyDocument",
               entity_id=doc.id, details={"agency_id": agency.id, "doc_type": doc_type, "size_bytes": stat.size})
    db.commit()
    return _document_dict(doc)


def _refuse_review() -> AppException:
    # Deliberately the same body whichever of the two reasons applies — see
    # this module's own changelog note on why (mirrors scope.py's uniform 404).
    return AppException(409, ErrorCode.CONFLICT, "This document cannot be reviewed.")


def _latest_document(db: Session, agency_id: str, doc_id: str) -> AgencyDocument:
    doc = (db.query(AgencyDocument)
          .filter(AgencyDocument.id == doc_id, AgencyDocument.agency_id == agency_id).first())
    if doc is None:
        raise AppException(404, ErrorCode.NOT_FOUND, "Not found")
    return doc


def verify_document(db: Session, verifier: User, agency_id: str, doc_id: str,
                    request: Request | None = None) -> dict:
    """Step 4 review, accept. Four-eyes: the verifier may never be the
    uploader. `_maybe_activate` runs inside the same transaction as the
    status flip — see its own docstring for why that matters."""
    _require_bank_admin(verifier)
    agency = agency_or_404(db, verifier, agency_id)
    doc = _latest_document(db, agency.id, doc_id)
    if doc.status != "UPLOADED" or doc.uploaded_by == verifier.id:
        raise _refuse_review()
    doc.status = "VERIFIED"
    doc.verified_by = verifier.id
    doc.verified_at = datetime.now(timezone.utc)
    stage_audit(db, action=AuditAction.DOCUMENT_VERIFIED, user_id=verifier.id, entity_type="AgencyDocument",
               entity_id=doc.id, details={"agency_id": agency.id, "doc_type": doc.doc_type},
               ip_address=_client_ip(request))
    _maybe_activate(db, agency.id)
    db.commit()
    return _document_dict(doc)


def reject_document(db: Session, verifier: User, agency_id: str, doc_id: str, *, reason: str,
                    request: Request | None = None) -> dict:
    _require_bank_admin(verifier)
    agency = agency_or_404(db, verifier, agency_id)
    doc = _latest_document(db, agency.id, doc_id)
    if doc.status != "UPLOADED" or doc.uploaded_by == verifier.id:
        raise _refuse_review()
    reason = (reason or "").strip()
    if not reason:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "A reason is required to reject a document.")
    doc.status = "REJECTED"
    doc.verified_by = verifier.id
    doc.verified_at = datetime.now(timezone.utc)
    doc.rejection_reason = reason
    stage_audit(db, action=AuditAction.DOCUMENT_REJECTED, user_id=verifier.id, entity_type="AgencyDocument",
               entity_id=doc.id, details={"agency_id": agency.id, "doc_type": doc.doc_type, "reason": reason},
               ip_address=_client_ip(request))
    db.commit()
    return _document_dict(doc)


def invite_master_login(db: Session, bank_admin: User, agency_id: str, *, full_name: str, email: str,
                        phone: str, channel: str = "LINK", request: Request | None = None) -> dict:
    """Step 5. Wraps invite_service.create_invite — can_invite's own rule
    already allows a BANK_ADMIN to invite an AGENCY_ADMIN into a named
    agency_id, so nothing new is needed there."""
    from app.services.invite_service import create_invite

    _require_bank_admin(bank_admin)
    agency = agency_or_404(db, bank_admin, agency_id)
    result = create_invite(db, bank_admin, email=email, role=UserRole.AGENCY_ADMIN, full_name=full_name,
                           phone=phone, agency_id=agency.id, bank_id=agency.bank_id, channel=channel,
                           request=request)
    write_audit(db, action=AuditAction.AGENCY_ONBOARDED, user_id=bank_admin.id, entity_type="Agency",
               entity_id=agency.id, details={"step": "master_login", "invited_email": email})
    return result


def _maybe_activate(db: Session, agency_id: str) -> bool:
    """The ONLY writer of Agency.status PENDING -> ACTIVE. Called from two
    places — verify_document (inside its own transaction, before that
    transaction's commit) and invite_service.accept_invite (same) — so
    whichever of the two conditions completes second is the one that fires
    it, in the same commit as the event that completed it. Never called on
    its own schedule; there is no polling path and no Celery task for this.

    Returns whether it activated, so a caller with its own audit trail
    (accept_invite's) can add "and this activated the agency" without a
    second read."""
    agency = db.query(Agency).filter(Agency.id == agency_id).first()
    if agency is None or agency.status != "PENDING":
        return False

    verified_types = {
        d.doc_type for d in
        db.query(AgencyDocument.doc_type)
        .filter(AgencyDocument.agency_id == agency_id, AgencyDocument.status == "VERIFIED").all()
    }
    if not set(REQUIRED_DOC_TYPES).issubset(verified_types):
        return False

    admin_accepted = (
        db.query(User)
        .filter(User.agency_id == agency_id, User.role == UserRole.AGENCY_ADMIN, User.is_active.is_(True))
        .first()
    ) is not None
    if not admin_accepted:
        return False

    agency.status = "ACTIVE"
    agency.activated_at = datetime.now(timezone.utc)
    stage_audit(db, action=AuditAction.AGENCY_ACTIVATED, user_id=None, entity_type="Agency", entity_id=agency.id,
               details={"required_docs_verified": sorted(REQUIRED_DOC_TYPES)})
    return True


def get_agency_detail(db: Session, principal: User, agency_id: str) -> dict:
    """The review step, and the resume-a-draft entry point: everything
    collected so far, read back in one call."""
    agency = agency_or_404(db, principal, agency_id)
    contract = (db.query(AgencyContract)
               .filter(AgencyContract.agency_id == agency.id).order_by(AgencyContract.created_at.desc()).first())
    documents = (db.query(AgencyDocument)
                .filter(AgencyDocument.agency_id == agency.id).order_by(AgencyDocument.created_at.desc()).all())
    region_ids = ([r.region_id for r in db.query(AgencyRegion.region_id)
                  .filter(AgencyRegion.contract_id == contract.id).all()] if contract else [])
    return {
        **_agency_dict(agency),
        "contract": _contract_dict(contract) if contract else None,
        "region_ids": region_ids,
        "documents": [_document_dict(d) for d in documents],
        "required_doc_types": list(REQUIRED_DOC_TYPES),
    }


def list_agencies(db: Session, principal: User, *, status: str | None = None) -> list[dict]:
    q = agencies_in_scope(db, principal)
    if status:
        q = q.filter(Agency.status == status)
    return [_agency_dict(a) for a in q.order_by(Agency.created_at.desc()).all()]


def list_regions(db: Session, principal: User) -> list[dict]:
    """The bank's region hierarchy, for the Coverage step's checklist.
    Read-only here — editing it is K01 (Admin > Regions), not this wizard.
    Bank-scoped like agencies_in_scope's BANK_* branch; a non-bank principal
    (an agency role) gets an empty list rather than a 403, since nothing in
    the wizard calls this except a bank user already gated by
    agency.contract.manage on the route that consumes the ids."""
    if not principal.bank_id or principal.role not in (
        UserRole.BANK_ADMIN, UserRole.BANK_ANALYST, UserRole.BANK_TECHOPS, UserRole.PLATFORM_ADMIN,
    ):
        return []
    regions = (db.query(Region).filter(Region.bank_id == principal.bank_id, Region.is_active.is_(True))
              .order_by(Region.path).all())
    return [
        {"region_id": r.id, "parent_id": r.parent_id, "level": r.level, "code": r.code, "name": r.name,
         "path": r.path, "latitude": r.latitude, "longitude": r.longitude}
        for r in regions
    ]


def _agency_dict(agency: Agency) -> dict:
    return {
        "agency_id": agency.id, "code": agency.code, "legal_name": agency.legal_name,
        "trade_name": agency.trade_name, "entity_type": agency.entity_type, "cin": agency.cin,
        "rbi_registration_no": agency.rbi_registration_no, "pan": agency.pan, "gstin": agency.gstin,
        "registered_address": agency.registered_address, "hq_city": agency.hq_city, "website": agency.website,
        "contacts": agency.contacts, "contact_name": agency.contact_name, "contact_email": agency.contact_email,
        "contact_phone": agency.contact_phone, "status": agency.status,
        "activated_at": agency.activated_at.isoformat() if agency.activated_at else None,
    }


def _contract_dict(contract: AgencyContract) -> dict:
    return {
        "contract_id": contract.id, "contract_no": contract.contract_no, "status": contract.status,
        "start_date": contract.start_date.isoformat(), "end_date": contract.end_date.isoformat(),
        "max_placed_cases": contract.max_placed_cases, "max_agents": contract.max_agents,
        "max_visits_per_month": contract.max_visits_per_month,
        "sla_first_visit_days": contract.sla_first_visit_days,
        "recall_no_activity_days": contract.recall_no_activity_days,
        "recall_on_sla_breach": contract.recall_on_sla_breach,
        "recall_at_contract_end": contract.recall_at_contract_end,
    }


def _document_dict(doc: AgencyDocument) -> dict:
    return {
        "document_id": doc.id, "doc_type": doc.doc_type, "file_name": doc.file_name,
        "content_type": doc.content_type, "size_bytes": doc.size_bytes, "status": doc.status,
        "scan_status": doc.scan_status, "issued_on": doc.issued_on.isoformat() if doc.issued_on else None,
        "expires_on": doc.expires_on.isoformat() if doc.expires_on else None,
        "rejection_reason": doc.rejection_reason,
        "uploaded_by": doc.uploaded_by, "verified_by": doc.verified_by,
        "verified_at": doc.verified_at.isoformat() if doc.verified_at else None,
    }
