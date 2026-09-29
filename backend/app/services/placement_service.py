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
#   that authorises the loan's (product, DPD bucket), covers the region of the
#   loan's branch, and has room under its placement cap. Anything else is
#   REFUSED with a typed reason, and the caller
#   quarantines the feed row (lending.bank_feed_rows, status QUARANTINED)
#   rather than invent an owner. "Never an unowned case" is enforced here, not
#   by hoping each caller remembers.
#
#   A contract with NO term rows is read as unrestricted by product and bucket.
#   That is a deliberate reading, not a default nobody chose: the demo
#   contracts are onboarded with full term tables (B16), and a bank that
#   writes a contract before its commission slabs must still be able to place.
#   Once ANY term row exists, only an authorised (product, bucket) row lets a
#   loan through. Coverage has no such reading: it fails CLOSED. A contract
#   with no agency_regions rows, or a loan whose branch has no region, is
#   not covered, because an agency working outside its territory is a
#   compliance breach, not a missing slab.
# ────────────────────────────────────────────────────────────────────────────
"""Placing a bank's loan with an agency, and opening the agency's case on it.

    svc = PlacementService(db)
    try:
        placement = svc.place_new_loan(loan, agency_id=agency.id, on=today, source="FEED")
    except PlacementRefused as refused:
        svc.quarantine(batch, row_no=i, raw=row, reason=refused.reason, detail=str(refused))
    else:
        case = svc.open_case(placement, loan, case_number=..., target_amount=...)

`evaluate()` runs every gate without raising, for a preview or a decision
record (`placement_decisions.gate_results`); `place_new_loan()` raises on the
first gate `evaluate()` fails. The gates exist once, here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.case import Case, CaseStatus, priority_for
from app.models.lending import BankFeedBatch, BankFeedRow
from app.models.loan import Loan, dpd_bucket_for
from app.models.placement import PLACEMENT_SOURCES, Placement
from app.models.tenancy import Agency, AgencyContract, AgencyContractTerm, AgencyRegion, Branch, Region

# Refusal reasons. Stored in bank_feed_rows.dq_errors, so a released row can
# be re-tried once the reason is fixed (a contract signed, a slab authorised).
NO_AGENCY = "NO_AGENCY"
AGENCY_NOT_ACTIVE = "AGENCY_NOT_ACTIVE"
NO_CONTRACT_IN_FORCE = "NO_CONTRACT_IN_FORCE"
NOT_AUTHORISED = "NOT_AUTHORISED"
NOT_COVERED = "NOT_COVERED"
CONTRACT_FULL = "CONTRACT_FULL"
PLACED_ELSEWHERE = "PLACED_ELSEWHERE"
UNKNOWN_BRANCH = "UNKNOWN_BRANCH"
REFUSAL_REASONS = (NO_AGENCY, AGENCY_NOT_ACTIVE, NO_CONTRACT_IN_FORCE, NOT_AUTHORISED, NOT_COVERED,
                   CONTRACT_FULL, PLACED_ELSEWHERE, UNKNOWN_BRANCH)

# The hard gates, in the order a refusal is reported. `place_new_loan` raises
# the first failure in this order; `evaluate` reports all of them.
GATES = ("placement", "agency", "contract", "authorisation", "coverage", "capacity")


class PlacementRefused(Exception):
    def __init__(self, reason: str, detail: str):
        assert reason in REFUSAL_REASONS, reason
        self.reason = reason
        super().__init__(detail)


@dataclass(frozen=True)
class GateCheck:
    """One gate's verdict. `passed` is None when an earlier gate failed and
    this one could not be judged (no agency means no contract to read)."""
    gate: str
    passed: bool | None
    reason: str | None = None
    detail: str = ""


@dataclass
class GateResult:
    checks: dict[str, GateCheck] = field(default_factory=dict)
    agency: Agency | None = None
    contract: AgencyContract | None = None
    #: The loan's ACTIVE placement with THIS agency, if it has one already.
    existing: Placement | None = None

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks.values())

    @property
    def first_failure(self) -> GateCheck | None:
        return next((self.checks[g] for g in GATES if self.checks[g].passed is False), None)

    def as_json(self) -> dict:
        """The `placement_decisions.gate_results` shape."""
        return {g: {"passed": c.passed, "reason": c.reason, "detail": c.detail}
                for g, c in self.checks.items()}


def _path_parts(path: str | None) -> tuple[str, ...]:
    # Region paths are written dot-joined ("WEST.MH.PUNE", demo/world.py);
    # the model comment describes "/a/b/". Accept both, compare by segment.
    return tuple(p for p in re.split(r"[./]", path or "") if p)


def path_covers(covering: str | None, covered: str | None) -> bool:
    """True when region `covering` is `covered` or one of its ancestors."""
    top, leaf = _path_parts(covering), _path_parts(covered)
    return bool(top) and leaf[:len(top)] == top


class PlacementService:
    def __init__(self, db: Session):
        self.db = db
        # Per-instance caches: a feed run evaluates thousands of rows against a
        # handful of contracts and branches. An instance lives for one run.
        self._coverage_paths: dict[str, tuple[str, ...]] = {}
        self._branch_paths: dict[tuple[str, str], str | None] = {}

    # ── the gates ────────────────────────────────────────────────────────────

    def contract_in_force(self, agency_id: str, on: date, *, for_update: bool = False) -> AgencyContract | None:
        """The agency's ACTIVE contract covering `on`; the latest-starting one
        if a renewal overlaps its predecessor. `for_update` row-locks it, so two
        writers placing against one contract count its capacity in turn."""
        q = (self.db.query(AgencyContract)
             .filter(AgencyContract.agency_id == agency_id, AgencyContract.status == "ACTIVE",
                     AgencyContract.start_date <= on, AgencyContract.end_date >= on)
             .order_by(AgencyContract.start_date.desc()))
        if for_update:
            q = q.with_for_update()
        return q.first()

    def is_authorised(self, contract: AgencyContract, loan: Loan) -> bool:
        terms = (self.db.query(AgencyContractTerm)
                 .filter(AgencyContractTerm.contract_id == contract.id).all())
        if not terms:
            return True        # unrestricted by product/bucket — see the changelog
        bucket = dpd_bucket_for(loan.dpd)
        return any(t.loan_type == loan.loan_type and t.dpd_bucket == bucket and t.is_authorised for t in terms)

    def coverage_paths(self, contract: AgencyContract) -> tuple[str, ...]:
        """The region paths the contract covers (each covers its subtree)."""
        if contract.id not in self._coverage_paths:
            rows = (self.db.query(Region.path)
                    .join(AgencyRegion, AgencyRegion.region_id == Region.id)
                    .filter(AgencyRegion.contract_id == contract.id, Region.bank_id == contract.bank_id)
                    .all())
            self._coverage_paths[contract.id] = tuple(sorted(r.path for r in rows))
        return self._coverage_paths[contract.id]

    def branch_region_path(self, bank_id: str, branch_code: str | None) -> str | None:
        key = (bank_id, branch_code or "")
        if key not in self._branch_paths:
            row = (self.db.query(Region.path)
                   .join(Branch, Branch.region_id == Region.id)
                   .filter(Branch.bank_id == bank_id, Branch.branch_code == branch_code,
                           Region.bank_id == bank_id)
                   .first()) if branch_code else None
            self._branch_paths[key] = row.path if row else None
        return self._branch_paths[key]

    def is_covered(self, contract: AgencyContract, loan: Loan) -> bool:
        """Fails closed: no coverage rows, or a branch without a region, is
        not covered."""
        leaf = self.branch_region_path(loan.bank_id, loan.branch_code)
        return leaf is not None and any(path_covers(p, leaf) for p in self.coverage_paths(contract))

    def placed_count(self, contract: AgencyContract) -> int:
        return (self.db.query(func.count(Placement.id))
                .filter(Placement.contract_id == contract.id, Placement.status == "ACTIVE").scalar()) or 0

    def headroom(self, contract: AgencyContract) -> int | None:
        """Placements the contract can still take; None when it has no cap."""
        if contract.max_placed_cases is None:
            return None
        return max(0, int(contract.max_placed_cases) - self.placed_count(contract))

    def active_placement(self, loan_id: str) -> Placement | None:
        return (self.db.query(Placement)
                .filter(Placement.loan_id == loan_id, Placement.status == "ACTIVE").first())

    def branch_known(self, bank_id: str, branch_code: str) -> bool:
        return self.db.query(Branch.id).filter(
            Branch.bank_id == bank_id, Branch.branch_code == branch_code).first() is not None

    def evaluate(self, loan: Loan, agency_id: str | None, on: date, *, planned: int = 0,
                 lock_contract: bool = False) -> GateResult:
        """Every gate for placing `loan` with `agency_id` on `on`, without
        raising. `planned` is how many placements the caller has already
        decided against this agency's contract in the same batch but not yet
        written, so a preview of 10 loans against 3 free slots passes 3."""
        res = GateResult()
        checks = res.checks
        lan = loan.loan_account_number

        existing = self.active_placement(loan.id)
        if existing is not None and existing.agency_id != agency_id:
            checks["placement"] = GateCheck("placement", False, PLACED_ELSEWHERE,
                                            f"loan {lan} is already placed with another agency "
                                            f"(placement {existing.id})")
        else:
            res.existing = existing
            checks["placement"] = GateCheck("placement", True)

        agency = self.db.get(Agency, agency_id) if agency_id else None
        if agency is None or agency.bank_id != loan.bank_id:
            checks["agency"] = GateCheck("agency", False, NO_AGENCY,
                                         f"agency {agency_id} does not work for this loan's bank"
                                         if agency_id else f"loan {lan}: no agency named for placement")
            agency = None
        elif agency.status != "ACTIVE":
            checks["agency"] = GateCheck("agency", False, AGENCY_NOT_ACTIVE, f"agency {agency.code} is {agency.status}")
        else:
            checks["agency"] = GateCheck("agency", True)
        res.agency = agency

        contract = self.contract_in_force(agency.id, on, for_update=lock_contract) if agency is not None else None
        res.contract = contract
        if agency is None:
            for g in ("contract", "authorisation", "coverage", "capacity"):
                checks[g] = GateCheck(g, None, None, "not judged: no agency")
            return res
        if contract is None:
            checks["contract"] = GateCheck("contract", False, NO_CONTRACT_IN_FORCE,
                                           f"agency {agency.code} has no ACTIVE contract covering {on}")
            for g in ("authorisation", "coverage", "capacity"):
                checks[g] = GateCheck(g, None, None, "not judged: no contract in force")
            return res
        checks["contract"] = GateCheck("contract", True, None, contract.contract_no)

        if self.is_authorised(contract, loan):
            checks["authorisation"] = GateCheck("authorisation", True)
        else:
            checks["authorisation"] = GateCheck(
                "authorisation", False, NOT_AUTHORISED,
                f"contract {contract.contract_no} does not authorise "
                f"{loan.loan_type.value} / {dpd_bucket_for(loan.dpd).value}")

        if self.is_covered(contract, loan):
            checks["coverage"] = GateCheck("coverage", True)
        else:
            leaf = self.branch_region_path(loan.bank_id, loan.branch_code)
            checks["coverage"] = GateCheck(
                "coverage", False, NOT_COVERED,
                f"branch {loan.branch_code} has no region" if leaf is None else
                f"contract {contract.contract_no} does not cover region {leaf}")

        if res.existing is not None or contract.max_placed_cases is None:
            checks["capacity"] = GateCheck("capacity", True)       # already holds it, or uncapped
        else:
            used = self.placed_count(contract) + planned
            if used >= contract.max_placed_cases:
                checks["capacity"] = GateCheck(
                    "capacity", False, CONTRACT_FULL,
                    f"contract {contract.contract_no} is at its cap of {contract.max_placed_cases} placements")
            else:
                checks["capacity"] = GateCheck("capacity", True)
        return res

    # ── placing ──────────────────────────────────────────────────────────────

    def place_new_loan(self, loan: Loan, *, agency_id: str | None, on: date, source: str,
                       placed_by: str | None = None, placement_run_id: str | None = None) -> Placement:
        """An ACTIVE placement of `loan` with `agency_id` on `on`, or
        PlacementRefused. Idempotent: the loan's existing ACTIVE placement with
        the same agency is returned as it is."""
        assert source in PLACEMENT_SOURCES, source
        if not agency_id:
            raise PlacementRefused(NO_AGENCY, f"loan {loan.loan_account_number}: no agency named for placement")

        existing = self.active_placement(loan.id)
        if existing is not None and existing.agency_id == agency_id:
            return existing

        res = self.evaluate(loan, agency_id, on, lock_contract=True)
        failed = res.first_failure
        if failed is not None:
            raise PlacementRefused(failed.reason, failed.detail)
        contract = res.contract
        placement = Placement(
            bank_id=loan.bank_id, agency_id=agency_id, loan_id=loan.id, contract_id=contract.id,
            source=source, status="ACTIVE", placed_on=on, placed_by=placed_by, placement_run_id=placement_run_id,
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
