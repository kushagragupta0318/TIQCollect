# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-10-01 — NEW (P2 G04). AGENCY_ADMIN's own read-only agency profile:
#   identity, the active contract's SLA/seat-limit/recall terms, and the
#   commission slabs it carries. The capability (agency.profile.read, AA
#   only) has been declared in core/permissions.py since the A01 catalog
#   landed; nothing called it until this file.
#
#   Picks the ACTIVE contract; falls back to the most recent contract by
#   start_date if the agency has none ACTIVE (lapsed/awaiting renewal), so an
#   agency between contracts still sees its last terms rather than a blank
#   page — its `status` field says plainly that it is not current.
#
#   Placements received/recalls (the other half of G04) is not here: it reads
#   the bank-side placement model (placement.read, already used by
#   bank_placements.py) rather than AgencyContract, and is large enough to be
#   its own change.
# ────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import AppException, ErrorCode
from app.models.tenancy import Agency, AgencyContract, AgencyContractTerm


def _not_found() -> AppException:
    return AppException(404, ErrorCode.NOT_FOUND, "Agency not found")


def get_agency_profile(db: Session, principal) -> dict:
    agency = db.query(Agency).filter(Agency.id == principal.agency_id).first()
    if agency is None:
        raise _not_found()

    contract = (
        db.query(AgencyContract)
        .filter(AgencyContract.agency_id == agency.id, AgencyContract.status == "ACTIVE")
        .order_by(AgencyContract.start_date.desc())
        .first()
    )
    if contract is None:
        contract = (
            db.query(AgencyContract)
            .filter(AgencyContract.agency_id == agency.id)
            .order_by(AgencyContract.start_date.desc())
            .first()
        )

    terms = []
    if contract is not None:
        rows = (
            db.query(AgencyContractTerm)
            .filter(AgencyContractTerm.contract_id == contract.id, AgencyContractTerm.is_authorised.is_(True))
            .order_by(AgencyContractTerm.loan_type, AgencyContractTerm.dpd_bucket)
            .all()
        )
        terms = [
            {
                "loan_type": t.loan_type,
                "dpd_bucket": t.dpd_bucket,
                "commission_pct": t.commission_pct,
                "fixed_fee_per_resolution": t.fixed_fee_per_resolution,
            }
            for t in rows
        ]

    return {
        "agency": {
            "legal_name": agency.legal_name,
            "trade_name": agency.trade_name,
            "rbi_registration_no": agency.rbi_registration_no,
            "status": agency.status,
            "hq_city": agency.hq_city,
            "contact_name": agency.contact_name,
            "contact_email": agency.contact_email,
            "contact_phone": agency.contact_phone,
        },
        "contract": None if contract is None else {
            "contract_no": contract.contract_no,
            "status": contract.status,
            "start_date": contract.start_date.isoformat(),
            "end_date": contract.end_date.isoformat(),
            "max_agents": contract.max_agents,
            "max_placed_cases": contract.max_placed_cases,
            "max_visits_per_month": contract.max_visits_per_month,
            "sla_first_visit_days": contract.sla_first_visit_days,
            "recall_no_activity_days": contract.recall_no_activity_days,
            "recall_on_sla_breach": contract.recall_on_sla_breach,
            "recall_at_contract_end": contract.recall_at_contract_end,
            "performance_bonus_pct": contract.performance_bonus_pct,
            "performance_target_pct": contract.performance_target_pct,
            "security_deposit": contract.security_deposit,
        },
        "commission_terms": terms,
    }
