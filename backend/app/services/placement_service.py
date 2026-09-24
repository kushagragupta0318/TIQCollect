# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B06/B15, coordinator audit item 2) — NEW. The one way a new
#   loan becomes an agency's work.
#
#   In v2 `cases.agency_id` is NOT NULL: a case is an AGENCY's work item on a
#   PLACEMENT the bank made. Four places created cases — ingest_daily,
#   demo_daily_feed, seed_data and the synthetic generator — and each would
#   have had to learn contracts, authorisation, capacity and the frozen
#   at-placement figures on its own. That is the seven-copies-of-the-DPD-rule
#   shape this repo keeps paying for, so they call this instead.
#
#   THE RULE: a case is opened only on a placement, and a placement is made
#   only against a contract IN FORCE on the day (ACTIVE, start <= day <= end)
#   that authorises the loan's (product, DPD bucket) and has room under its
#   placement cap. Anything else is REFUSED with a typed reason, and the caller
#   quarantines the feed row (lending.bank_feed_rows, status QUARANTINED)
#   rather than invent an owner. "Never an unowned case" is enforced here, not
#   by hoping each caller remembers.
#
#   A contract with NO term rows is read as unrestricted by product and bucket.
#   That is a deliberate reading, not a default nobody chose: the demo
#   contracts are onboarded with full term tables (B16), and a bank that
#   writes a contract before its commission slabs must still be able to place.
#   Once ANY term row exists, only an authorised (product, bucket) row lets a
#   loan through.
# ────────────────────────────────────────────────────────────────────────────
"""Placing a bank's loan with an agency, and opening the agency's case on it.

    svc = PlacementService(db)
    try:
        placement = svc.place_new_loan(loan, agency_id=agency.id, on=today, source="FEED")
    except PlacementRefused as refused:
        svc.quarantine(batch, row_no=i, raw=row, reason=refused.reason, detail=str(refused))
    else:
        case = svc.open_case(placement, loan, case_number=..., target_amount=...)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.case import Case, CaseStatus, priority_for
from app.models.lending import BankFeedBatch, BankFeedRow
from app.models.loan import Loan, dpd_bucket_for
from app.models.placement import PLACEMENT_SOURCES, Placement
from app.models.tenancy import Agency, AgencyContract, AgencyContractTerm, Branch

# Refusal reasons. Stored in bank_feed_rows.dq_errors, so a released row can
# be re-tried once the reason is fixed (a contract signed, a slab authorised).
NO_AGENCY = "NO_AGENCY"
AGENCY_NOT_ACTIVE = "AGENCY_NOT_ACTIVE"
NO_CONTRACT_IN_FORCE = "NO_CONTRACT_IN_FORCE"
NOT_AUTHORISED = "NOT_AUTHORISED"
CONTRACT_FULL = "CONTRACT_FULL"
PLACED_ELSEWHERE = "PLACED_ELSEWHERE"
UNKNOWN_BRANCH = "UNKNOWN_BRANCH"
REFUSAL_REASONS = (NO_AGENCY, AGENCY_NOT_ACTIVE, NO_CONTRACT_IN_FORCE, NOT_AUTHORISED, CONTRACT_FULL,
                   PLACED_ELSEWHERE, UNKNOWN_BRANCH)


class PlacementRefused(Exception):
    def __init__(self, reason: str, detail: str):
        assert reason in REFUSAL_REASONS, reason
        self.reason = reason
        super().__init__(detail)


class PlacementService:
    def __init__(self, db: Session):
        self.db = db

    # ── the gates ────────────────────────────────────────────────────────────

    def contract_in_force(self, agency_id: str, on: date) -> AgencyContract | None:
        """The agency's ACTIVE contract covering `on`; the latest-starting one
        if a renewal overlaps its predecessor."""
        return (
            self.db.query(AgencyContract)
            .filter(AgencyContract.agency_id == agency_id, AgencyContract.status == "ACTIVE",
                    AgencyContract.start_date <= on, AgencyContract.end_date >= on)
            .order_by(AgencyContract.start_date.desc())
            .first()
        )

    def is_authorised(self, contract: AgencyContract, loan: Loan) -> bool:
        terms = (self.db.query(AgencyContractTerm)
                 .filter(AgencyContractTerm.contract_id == contract.id).all())
        if not terms:
            return True        # unrestricted by product/bucket — see the changelog
        bucket = dpd_bucket_for(loan.dpd)
        return any(t.loan_type == loan.loan_type and t.dpd_bucket == bucket and t.is_authorised for t in terms)

    def active_placement(self, loan_id: str) -> Placement | None:
        return (self.db.query(Placement)
                .filter(Placement.loan_id == loan_id, Placement.status == "ACTIVE").first())

    def branch_known(self, bank_id: str, branch_code: str) -> bool:
        return self.db.query(Branch.id).filter(
            Branch.bank_id == bank_id, Branch.branch_code == branch_code).first() is not None

    # ── placing ──────────────────────────────────────────────────────────────

    def place_new_loan(self, loan: Loan, *, agency_id: str | None, on: date, source: str,
                       placed_by: str | None = None) -> Placement:
        """An ACTIVE placement of `loan` with `agency_id` on `on`, or
        PlacementRefused. Idempotent: the loan's existing ACTIVE placement with
        the same agency is returned as it is."""
        assert source in PLACEMENT_SOURCES, source
        if not agency_id:
            raise PlacementRefused(NO_AGENCY, f"loan {loan.loan_account_number}: no agency named for placement")

        existing = self.active_placement(loan.id)
        if existing is not None:
            if existing.agency_id == agency_id:
                return existing
            raise PlacementRefused(
                PLACED_ELSEWHERE,
                f"loan {loan.loan_account_number} is already placed with another agency "
                f"(placement {existing.id})")

        agency = self.db.get(Agency, agency_id)
        if agency is None or agency.bank_id != loan.bank_id:
            raise PlacementRefused(NO_AGENCY, f"agency {agency_id} does not work for this loan's bank")
        if agency.status != "ACTIVE":
            raise PlacementRefused(AGENCY_NOT_ACTIVE, f"agency {agency.code} is {agency.status}")

        contract = self.contract_in_force(agency_id, on)
        if contract is None:
            raise PlacementRefused(NO_CONTRACT_IN_FORCE, f"agency {agency.code} has no ACTIVE contract covering {on}")
        if not self.is_authorised(contract, loan):
            raise PlacementRefused(
                NOT_AUTHORISED,
                f"contract {contract.contract_no} does not authorise "
                f"{loan.loan_type.value} / {dpd_bucket_for(loan.dpd).value}")
        if contract.max_placed_cases is not None:
            live = (self.db.query(func.count(Placement.id))
                    .filter(Placement.contract_id == contract.id, Placement.status == "ACTIVE").scalar())
            if live >= contract.max_placed_cases:
                raise PlacementRefused(
                    CONTRACT_FULL,
                    f"contract {contract.contract_no} is at its cap of {contract.max_placed_cases} placements")

        placement = Placement(
            bank_id=loan.bank_id, agency_id=agency_id, loan_id=loan.id, contract_id=contract.id,
            source=source, status="ACTIVE", placed_on=on, placed_by=placed_by,
            expected_end_on=contract.end_date if contract.recall_at_contract_end else None,
            dpd_at_placement=int(loan.dpd or 0), dpd_bucket_at_placement=dpd_bucket_for(loan.dpd),
            exposure_at_placement=float(loan.total_outstanding or 0.0),
            overdue_at_placement=float(loan.overdue_amount or 0.0),
            sla_first_visit_due=on + timedelta(days=int(contract.sla_first_visit_days or 7)),
        )
        self.db.add(placement)
        self.db.flush()
        return placement

    def open_case(self, placement: Placement, loan: Loan, *, case_number: str, target_amount: float,
                  **fields: Any) -> Case:
        """The agency's UNASSIGNED case on `placement`. Tenant ids come from
        the placement, never from the caller."""
        for forbidden in ("bank_id", "agency_id", "placement_id", "loan_id", "customer_id"):
            if forbidden in fields:
                raise TypeError(f"open_case sets {forbidden} itself")
        fields.setdefault("status", CaseStatus.UNASSIGNED)
        fields.setdefault("agent_id", None)
        fields.setdefault("priority", priority_for(loan.dpd))
        fields.setdefault("collected_amount", 0.0)
        fields.setdefault("allocation_date", placement.placed_on)
        case = Case(
            bank_id=placement.bank_id, agency_id=placement.agency_id, placement_id=placement.id,
            loan_id=loan.id, customer_id=loan.customer_id,
            case_number=case_number, target_amount=float(target_amount), **fields,
        )
        self.db.add(case)
        self.db.flush()
        return case

    # ── quarantine ───────────────────────────────────────────────────────────

    def quarantine(self, batch: BankFeedBatch, *, row_no: int, raw: dict, reason: str, detail: str,
                   loan_id: str | None = None) -> BankFeedRow:
        """Hold a feed row for a person to fix and release. Never raises for
        a row already held in this batch: the latest reason is appended."""
        assert reason in REFUSAL_REASONS, reason
        row = (self.db.query(BankFeedRow)
               .filter(BankFeedRow.batch_id == batch.id, BankFeedRow.row_no == row_no).first())
        error = {"reason": reason, "detail": detail}
        if row is None:
            row = BankFeedRow(
                bank_id=batch.bank_id, batch_id=batch.id, row_no=row_no,
                loan_account_number=(raw.get("loan_account_number") or "").strip()[:30] or None,
                customer_ref=(raw.get("customer_ref") or "").strip()[:30] or None,
                case_number=(raw.get("case_number") or "").strip()[:20] or None,
                raw=dict(raw), status="QUARANTINED", dq_errors=[error], loan_id=loan_id,
                processed_at=datetime.now(timezone.utc),
            )
            self.db.add(row)
        else:
            row.status = "QUARANTINED"
            row.dq_errors = [*(row.dq_errors or []), error]
            row.loan_id = row.loan_id or loan_id
        self.db.flush()
        return row
