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
import re
import uuid
from datetime import date, datetime, timezone

from fastapi import Request
from minio.error import S3Error
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import storage
from app.core.audit import stage_audit
from app.core.errors import AppException, ErrorCode
from app.core.ids import parse_uuid
from app.core.security import create_agency_doc_upload_token, decode_token
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


def _require_editable(agency: Agency) -> None:
    """coordinator audit LOW: nothing stopped a step from being resubmitted
    after the agency had already gone ACTIVE — identity, coverage, contract
    and documents are onboarding-time only. Once active, a change here needs
    its own amendment flow (a later task), not a silent rewrite through the
    wizard's own PATCH routes."""
    if agency.status != "PENDING":
        raise AppException(409, ErrorCode.CONFLICT,
                           f"This agency is {agency.status.lower()} — the onboarding wizard no longer applies.")


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


_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_BARE_ROOT_TRAILING_SLASH_RE = re.compile(r"^(https?://[^/]+)/$")


def _normalise_website(raw: str | None) -> str | None:
    """Mirrored on the frontend (onboardingLogic.normaliseWebsite) — owner-
    reported: the wizard's Website field used to be `<input type="url">`,
    which demands a scheme, so typing "google.com" (how most people actually
    write it) failed native browser validation before this API was ever
    reached. Applied here too, not just client-side, so a value pasted
    straight into a request (not typed through the form) normalises the
    same way: no scheme -> prefix https://, an explicit http:// is left
    alone (never silently upgraded), and a bare-root trailing slash is
    trimmed so "https://foo.com/" and "https://foo.com" store identically.
    Deliberately does not touch "www." — that changes which host is
    actually named, not this function's call to make."""
    if raw is None:
        return None
    trimmed = raw.strip()
    if not trimmed:
        return None
    with_scheme = trimmed if _SCHEME_RE.match(trimmed) else f"https://{trimmed}"
    return _BARE_ROOT_TRAILING_SLASH_RE.sub(r"\1", with_scheme)


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
        registered_address=registered_address, hq_city=hq_city, website=_normalise_website(website),
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
               entity_id=agency.id, bank_id=agency.bank_id, agency_id=agency.id, details={"step": "identity", "legal_name": legal_name})
    db.commit()
    return _agency_dict(agency)


def update_identity(db: Session, bank_admin: User, agency_id: str, **fields) -> dict:
    """Step 1 revisited. Every field optional — omitted means unchanged,
    same convention as edit_agent. Unknown keys are rejected rather than
    silently ignored, so a frontend typo fails loud."""
    _require_bank_admin(bank_admin)
    agency = agency_or_404(db, bank_admin, agency_id)
    _require_editable(agency)
    unknown = set(fields) - set(_IDENTITY_FIELDS)
    if unknown:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, f"Unknown field(s): {', '.join(sorted(unknown))}")
    changed = {}
    for key, value in fields.items():
        if value is None:
            continue
        current = getattr(agency, key)
        if key == "website":
            value = _normalise_website(value)
        elif isinstance(value, str):
            value = value.strip()
        if value != current:
            changed[key] = {"from": current, "to": value}
            setattr(agency, key, value)
    if not changed:
        return {"agency_id": agency.id, "changed": []}
    stage_audit(db, action=AuditAction.AGENCY_ONBOARDED, user_id=bank_admin.id, entity_type="Agency",
               entity_id=agency.id, bank_id=agency.bank_id, agency_id=agency.id, details={"step": "identity", "changed": list(changed)})
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
    _require_editable(agency)
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
        parsed_region_ids = [parse_uuid(rid) for rid in region_ids]
        if any(p is None for p in parsed_region_ids):
            raise AppException(422, ErrorCode.VALIDATION_ERROR, "region_ids must all be valid ids.")
        valid_ids = {r.id for r in db.query(Region.id)
                    .filter(Region.bank_id == agency.bank_id, Region.id.in_(parsed_region_ids)).all()}
        missing = set(parsed_region_ids) - valid_ids
        if missing:
            raise AppException(422, ErrorCode.VALIDATION_ERROR, f"Unknown region id(s): {', '.join(sorted(missing))}")
        db.query(AgencyRegion).filter(AgencyRegion.contract_id == contract.id).delete()
        for rid in parsed_region_ids:
            db.add(AgencyRegion(bank_id=agency.bank_id, agency_id=agency.id, contract_id=contract.id, region_id=rid))

    if contract_terms is not None:
        # coordinator audit LOW: a duplicate (loan_type, dpd_bucket) pair in
        # the submitted list used to reach agency_contract_terms' own
        # UniqueConstraint("contract_id", "loan_type", "dpd_bucket") and
        # fail as an unhandled IntegrityError — a 500 for a mistake the API
        # should refuse cleanly.
        seen_pairs: set[tuple[str, str]] = set()
        for term in contract_terms:
            pair = (term.get("loan_type"), term.get("dpd_bucket"))
            if pair in seen_pairs:
                raise AppException(422, ErrorCode.VALIDATION_ERROR,
                                   f"Duplicate slab for {pair[0]} / {pair[1]} — each loan type and DPD bucket "
                                   "combination may appear once.")
            seen_pairs.add(pair)
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
               entity_id=agency.id, bank_id=agency.bank_id, agency_id=agency.id, details={"step": "coverage_contract", "contract_id": contract.id})
    db.commit()
    return _agency_dict(agency)


# The bytes every allowed content-type actually starts with. `stat.content_type`
# is whatever the CLIENT's PUT request claimed in its own Content-Type header
# — MinIO does not verify it — so accepting that claim at face value would
# make the allowlist purely cosmetic. Checked against what was actually
# downloaded to hash, so this costs nothing extra to read.
_MAGIC_BYTES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
}


def presign_document(db: Session, bank_admin: User, agency_id: str, doc_type: str, content_type: str) -> dict:
    """Step 4, part 1. Returns a server-generated key, a short-lived
    presigned PUT URL, and a signed upload_token binding that exact key to
    this (bank, agency, doc_type) — confirm_document requires it back and
    verifies it cryptographically, rather than trusting a prefix match on
    the key alone (coordinator audit HIGH, 51d165b: a key-prefix check does
    not stop the same uploaded object being confirmed into a different
    agency or doc_type, or confirmed twice)."""
    _require_bank_admin(bank_admin)
    agency = agency_or_404(db, bank_admin, agency_id)
    _require_editable(agency)
    if doc_type not in DOC_TYPES:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, f"doc_type must be one of: {', '.join(DOC_TYPES)}")
    if content_type not in _ALLOWED_DOC_CONTENT_TYPES:
        raise AppException(422, ErrorCode.VALIDATION_ERROR,
                           f"content_type must be one of: {', '.join(_ALLOWED_DOC_CONTENT_TYPES)}")
    ext = _ALLOWED_DOC_CONTENT_TYPES[content_type]
    key = storage.agency_document_key(agency.id, doc_type, ext=ext)
    upload_url = storage.presigned_upload_url(key, content_type=content_type, expires_minutes=15)
    upload_token = create_agency_doc_upload_token(key, bank_id=agency.bank_id, agency_id=agency.id,
                                                  doc_type=doc_type)
    return {"upload_url": upload_url, "key": key, "doc_type": doc_type, "upload_token": upload_token}


def confirm_document(db: Session, bank_admin: User, agency_id: str, *, doc_type: str, key: str,
                     upload_token: str, file_name: str | None = None, issued_on: date | None = None,
                     expires_on: date | None = None) -> dict:
    """Step 4, part 2. Trusts nothing the client says about the file it
    claims to have uploaded:
      - the upload_token (from presign_document) is decoded and its
        bank_id/agency_id/doc_type/key claims must match this call EXACTLY
        — a prefix match on the key is not identity;
      - the object must not already be confirmed under any document row
        (storage_key is checked for an existing row before insert — a
        UNIQUE constraint at the DB level is tracked as a follow-up, 43);
      - content-type and size come from a HEAD on the object MinIO actually
        stored, never the client's claim, and are cross-checked against the
        object's real magic bytes;
      - a rejected upload is deleted from storage rather than left orphaned,
        so a confirm that fails for a bad reason does not silently consume
        the bucket."""
    _require_bank_admin(bank_admin)
    agency = agency_or_404(db, bank_admin, agency_id)
    _require_editable(agency)
    if doc_type not in DOC_TYPES:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, f"doc_type must be one of: {', '.join(DOC_TYPES)}")

    try:
        payload = decode_token(upload_token)
    except ValueError:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "That upload link has expired. Upload again.")
    if (payload.get("type") != "agency_doc_upload" or payload.get("sub") != key
            or payload.get("bank_id") != agency.bank_id or payload.get("agency_id") != agency.id
            or payload.get("doc_type") != doc_type):
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "That upload was not issued for this document.")

    if db.query(AgencyDocument.id).filter(AgencyDocument.storage_key == key).first() is not None:
        raise AppException(409, ErrorCode.CONFLICT, "This upload has already been confirmed.")

    try:
        stat = storage.stat_object(key)
    except S3Error:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Nothing was uploaded to that key yet.")
    content_type = (stat.content_type or "").split(";")[0].strip()
    if content_type not in _ALLOWED_DOC_CONTENT_TYPES:
        storage.delete_object(key)
        raise AppException(422, ErrorCode.VALIDATION_ERROR, f"Unsupported file type: {content_type or 'unknown'}")
    if stat.size > _MAX_DOCUMENT_SIZE_BYTES:
        storage.delete_object(key)
        raise AppException(422, ErrorCode.VALIDATION_ERROR,
                           f"File is {stat.size // 1024} KB; the limit is {_MAX_DOCUMENT_SIZE_BYTES // 1024} KB.")
    content = storage.download_bytes(key)
    if not content.startswith(_MAGIC_BYTES[content_type]):
        storage.delete_object(key)
        raise AppException(422, ErrorCode.VALIDATION_ERROR,
                           f"The file's contents don't match its declared type ({content_type}).")
    sha256 = hashlib.sha256(content).hexdigest()

    doc = AgencyDocument(
        bank_id=agency.bank_id, agency_id=agency.id, doc_type=doc_type, storage_key=key,
        file_name=file_name, content_type=content_type, size_bytes=stat.size, sha256=sha256,
        scan_status="PENDING", status="UPLOADED", issued_on=issued_on, expires_on=expires_on,
        uploaded_by=bank_admin.id,
    )
    db.add(doc)
    try:
        db.flush()
    except IntegrityError:
        # A concurrent confirm of the same key won the race between our own
        # pre-check above and this insert — the belt to that brace. Once
        # 43's UNIQUE(storage_key) migration lands this is what actually
        # catches it; today it is unreachable in SQLite (no such
        # constraint yet) and reachable only under real concurrency.
        db.rollback()
        raise AppException(409, ErrorCode.CONFLICT, "This upload has already been confirmed.")
    stage_audit(db, action=AuditAction.DOCUMENT_UPLOADED, user_id=bank_admin.id, entity_type="AgencyDocument",
               entity_id=doc.id, bank_id=agency.bank_id, agency_id=agency.id, details={"agency_id": agency.id, "doc_type": doc_type, "size_bytes": stat.size})
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
    _require_editable(agency)
    doc = _latest_document(db, agency.id, doc_id)
    if doc.status != "UPLOADED" or doc.uploaded_by == verifier.id:
        raise _refuse_review()
    doc.status = "VERIFIED"
    doc.verified_by = verifier.id
    doc.verified_at = datetime.now(timezone.utc)
    stage_audit(db, action=AuditAction.DOCUMENT_VERIFIED, user_id=verifier.id, entity_type="AgencyDocument",
               entity_id=doc.id, bank_id=agency.bank_id, agency_id=agency.id, details={"agency_id": agency.id, "doc_type": doc.doc_type},
               ip_address=_client_ip(request))
    _maybe_activate(db, agency.id)
    db.commit()
    return _document_dict(doc)


def reject_document(db: Session, verifier: User, agency_id: str, doc_id: str, *, reason: str,
                    request: Request | None = None) -> dict:
    _require_bank_admin(verifier)
    agency = agency_or_404(db, verifier, agency_id)
    _require_editable(agency)
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
               entity_id=doc.id, bank_id=agency.bank_id, agency_id=agency.id, details={"agency_id": agency.id, "doc_type": doc.doc_type, "reason": reason},
               ip_address=_client_ip(request))
    db.commit()
    return _document_dict(doc)


def invite_master_login(db: Session, bank_admin: User, agency_id: str, *, full_name: str, email: str,
                        phone: str, channel: str = "LINK", request: Request | None = None) -> dict:
    """Step 5. Wraps invite_service.create_invite — can_invite's own rule
    already allows a BANK_ADMIN to invite an AGENCY_ADMIN into a named
    agency_id, so nothing new is needed there.

    The AGENCY_ONBOARDED row is STAGED before create_invite runs, not
    written after (coordinator audit LOW, 51d165b) — create_invite commits
    its own transaction internally, so a row added to the same session
    before that call rides along in the SAME commit as the invite and its
    own USER_INVITED row, rather than a second, separate commit that could
    succeed even if this one never ran, or vice versa."""
    from app.services.invite_service import create_invite

    _require_bank_admin(bank_admin)
    agency = agency_or_404(db, bank_admin, agency_id)
    _require_editable(agency)
    stage_audit(db, action=AuditAction.AGENCY_ONBOARDED, user_id=bank_admin.id, entity_type="Agency",
               entity_id=agency.id, bank_id=agency.bank_id, agency_id=agency.id, details={"step": "master_login", "invited_email": email})
    result = create_invite(db, bank_admin, email=email, role=UserRole.AGENCY_ADMIN, full_name=full_name,
                           phone=phone, agency_id=agency.id, bank_id=agency.bank_id, channel=channel,
                           request=request)
    return result


def _maybe_activate(db: Session, agency_id: str) -> bool:
    """The ONLY writer of Agency.status PENDING -> ACTIVE. Called from two
    places — verify_document (inside its own transaction, before that
    transaction's commit) and invite_service.accept_invite (same) — so
    whichever of the two conditions completes second is the one that fires
    it, in the same commit as the event that completed it. Never called on
    its own schedule; there is no polling path and no Celery task for this.

    LOCKS the Agency row first (coordinator audit HIGH, 51d165b): both
    callers read-check-write against the SAME row from two different
    request transactions that can genuinely overlap — the last required
    document being verified and the master invite being accepted are
    ordinarily two different actions by two different people. Without a
    lock, both transactions can pass their own "is the other condition
    already true?" read before either commits, and neither sees the other's
    not-yet-committed write: the agency then never activates (both callers
    conclude "not yet, my half is done but the other one isn't" and stop),
    or — if the interleaving lands differently — the code between the read
    and the write runs twice, both branches try to fire ACTIVE. The Postgres
    FOR UPDATE lock serialises the two callers: whichever gets here second
    blocks until the first commits, then re-reads a row that already
    reflects the first caller's write. On SQLite (tests without a Postgres
    fixture) with_for_update() is a documented no-op — there is no real
    concurrent writer to serialise against there anyway.

    Returns whether it activated, so a caller with its own audit trail
    (accept_invite's) can add "and this activated the agency" without a
    second read."""
    agency = db.query(Agency).filter(Agency.id == agency_id).with_for_update().first()
    if agency is None or agency.status != "PENDING":
        return False

    verified_types = {
        d.doc_type for d in
        db.query(AgencyDocument.doc_type)
        .filter(AgencyDocument.agency_id == agency_id, AgencyDocument.status == "VERIFIED").all()
    }
    if not set(REQUIRED_DOC_TYPES).issubset(verified_types):
        return False

    # coordinator audit LOW: "any active AGENCY_ADMIN of this agency" is a
    # proxy for "the master invite was accepted", and a wrong one — it says
    # nothing about WHICH invite, or that this wizard's invite (rather than
    # some other admin account reaching AGENCY_ADMIN some other way) is what
    # the bank user is waiting on. The real condition is the UserInvite row
    # itself: this purpose, this agency, accepted.
    from app.models.identity import UserInvite
    invite_accepted = (
        db.query(UserInvite)
        .filter(UserInvite.agency_id == agency_id, UserInvite.role == UserRole.AGENCY_ADMIN,
                UserInvite.purpose == "AGENCY_MASTER_LOGIN", UserInvite.accepted_at.isnot(None))
        .first()
    ) is not None
    if not invite_accepted:
        return False

    agency.status = "ACTIVE"
    agency.activated_at = datetime.now(timezone.utc)
    stage_audit(db, action=AuditAction.AGENCY_ACTIVATED, user_id=None, entity_type="Agency", entity_id=agency.id,
               bank_id=agency.bank_id, agency_id=agency.id,
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


def list_agency_directory(
    db: Session, principal: User, *, region_id: str | None = None, status: str | None = None,
    loan_type: str | None = None, contract_expiring_before: date | None = None,
) -> list[dict]:
    """D05 (P3 fast-forward, thin first cut): one row per agency with its
    latest contract's dates/capacity, its covered regions (for the
    directory's map) and its authorised products — everything the table and
    map need in one call, no N+1 from the frontend.

    `score` is deliberately absent (not null-filled, not faked) — Agency
    Performance Index is D06's, reads mv_agency_scorecard_monthly (via
    analytics.agency_scorecard_monthly_scoped, per 43), and does not exist
    yet. A frontend column for it renders as pending until D06 lands, not
    as a fabricated zero."""
    q = agencies_in_scope(db, principal)
    if status:
        q = q.filter(Agency.status == status)
    agencies = q.order_by(Agency.legal_name).all()

    region_prefix = None
    if region_id:
        # coordinator audit LOW: this used to query Region with no bank_id
        # filter at all — a bank admin could pass ANOTHER bank's region id
        # and have its `.path` used as a filter prefix. Region rows are
        # tenant data the same as everything else; scoped like every other
        # Region read in this module.
        parsed_region_id = parse_uuid(region_id)
        if parsed_region_id is None:
            raise AppException(422, ErrorCode.VALIDATION_ERROR, f"Unknown region id: {region_id}")
        target = db.query(Region).filter(Region.id == parsed_region_id, Region.bank_id == principal.bank_id).first()
        if target is None:
            raise AppException(422, ErrorCode.VALIDATION_ERROR, f"Unknown region id: {region_id}")
        region_prefix = target.path

    rows = []
    for agency in agencies:
        contract = (db.query(AgencyContract)
                   .filter(AgencyContract.agency_id == agency.id)
                   .order_by(AgencyContract.created_at.desc()).first())
        if contract_expiring_before is not None:
            if contract is None or contract.end_date > contract_expiring_before:
                continue

        covered = []
        authorised_products: set[str] = set()
        if contract is not None:
            covered = (db.query(Region)
                      .join(AgencyRegion, AgencyRegion.region_id == Region.id)
                      .filter(AgencyRegion.contract_id == contract.id).all())
            authorised_products = {
                t.loan_type for t in
                db.query(AgencyContractTerm.loan_type)
                .filter(AgencyContractTerm.contract_id == contract.id, AgencyContractTerm.is_authorised.is_(True))
                .all()
            }
        if region_prefix is not None and not any(r.path.startswith(region_prefix) for r in covered):
            continue
        if loan_type is not None and loan_type not in authorised_products:
            continue

        rows.append({
            **_agency_dict(agency),
            "contract": _contract_dict(contract) if contract is not None else None,
            "covered_regions": [{"region_id": r.id, "name": r.name, "level": r.level,
                                 "latitude": r.latitude, "longitude": r.longitude} for r in covered],
            "authorised_products": sorted(authorised_products),
        })
    return rows


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
