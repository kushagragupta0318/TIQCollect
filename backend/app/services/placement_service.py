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

import base64
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models.case import Case, CaseStatus, ClosureReason, priority_for
from app.models.lending import BankFeedBatch, BankFeedRow
from app.models.loan import Loan, LoanStatus, dpd_bucket_for
from app.models.placement import PLACEMENT_SOURCES, Placement
from app.models.tenancy import Agency, AgencyContract, AgencyContractTerm, AgencyRegion, Branch, Region

# Refusal reasons. Stored in bank_feed_rows.dq_errors, so a released row can
# be re-tried once the reason is fixed (a contract signed, a slab authorised).
LOAN_NOT_OPEN = "LOAN_NOT_OPEN"
NO_AGENCY = "NO_AGENCY"
AGENCY_NOT_ACTIVE = "AGENCY_NOT_ACTIVE"
NO_CONTRACT_IN_FORCE = "NO_CONTRACT_IN_FORCE"
NOT_AUTHORISED = "NOT_AUTHORISED"
NOT_COVERED = "NOT_COVERED"
CONTRACT_FULL = "CONTRACT_FULL"
PLACED_ELSEWHERE = "PLACED_ELSEWHERE"
UNKNOWN_BRANCH = "UNKNOWN_BRANCH"
REFUSAL_REASONS = (LOAN_NOT_OPEN, NO_AGENCY, AGENCY_NOT_ACTIVE, NO_CONTRACT_IN_FORCE, NOT_AUTHORISED, NOT_COVERED,
                   CONTRACT_FULL, PLACED_ELSEWHERE, UNKNOWN_BRANCH)

# The hard gates, in the order a refusal is reported. `place_new_loan` raises
# the first failure in this order; `evaluate` reports all of them.
GATES = ("loan", "placement", "agency", "contract", "authorisation", "coverage", "capacity")


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


# A closed, settled or written-off loan has nothing left for an agency to collect.
PLACEABLE_LOAN_STATUSES = frozenset({LoanStatus.ACTIVE, LoanStatus.NPA})


def _path_parts(path: str | None) -> tuple[str, ...]:
    # Region paths are written dot-joined ("WEST.MH.PUNE", demo/world.py);
    # the model comment describes "/a/b/". Accept both, compare by segment.
    return tuple(p for p in re.split(r"[./]", path or "") if p)


def path_covers(covering: str | None, covered: str | None) -> bool:
    """True when region `covering` is `covered` or one of its ancestors."""
    top, leaf = _path_parts(covering), _path_parts(covered)
    return bool(top) and leaf[:len(top)] == top


@dataclass(frozen=True)
class LoanFacts:
    loan: Loan
    #: regions.path of the loan's branch; None when the branch has no region.
    region_path: str | None
    #: The loan's ACTIVE placement, with any agency.
    active: Placement | None


@dataclass(frozen=True)
class AgencyFacts:
    #: The id the caller named (may name nothing, or another bank's agency).
    agency_id: str | None
    on: date
    agency: Agency | None
    #: The contract in force on `on`; None when the agency is not ACTIVE or has none.
    contract: AgencyContract | None
    #: (loan_type, bucket, is_authorised) rows; None = no term rows (unrestricted).
    terms: tuple | None
    coverage: tuple[str, ...]
    placed: int
    #: commission_pct per (loan_type, bucket) from the contract's terms; empty when it has none.
    commission: dict = field(default_factory=dict)


def authorises(terms: tuple | None, loan: Loan) -> bool:
    if terms is None:
        return True            # unrestricted by product/bucket — see the changelog
    bucket = dpd_bucket_for(loan.dpd)
    return any(t == loan.loan_type and b == bucket and ok for t, b, ok in terms)


def covers(coverage: tuple[str, ...], region_path: str | None) -> bool:
    """Fails closed: no coverage rows, or a branch without a region."""
    return region_path is not None and any(path_covers(p, region_path) for p in coverage)


def judge(lf: LoanFacts, af: AgencyFacts, *, planned: int = 0) -> GateResult:
    """THE gates, over facts already loaded: no database, so one definition
    serves a single placement (evaluate) and a whole book (the engine).
    Every gate is judged even after one fails, so a preview can list all of
    a loan's problems. The verdict is `ok` / `first_failure`; a gate's own
    check says nothing about whether the loan was a candidate."""
    loan, res = lf.loan, GateResult()
    checks, lan = res.checks, lf.loan.loan_account_number

    if loan.status in PLACEABLE_LOAN_STATUSES:
        checks["loan"] = GateCheck("loan", True)
    else:
        checks["loan"] = GateCheck("loan", False, LOAN_NOT_OPEN, f"loan {lan} is {loan.status.value}")

    existing = lf.active
    if existing is not None and existing.agency_id != af.agency_id:
        checks["placement"] = GateCheck("placement", False, PLACED_ELSEWHERE,
                                        f"loan {lan} is already placed with another agency "
                                        f"(placement {existing.id})")
    else:
        res.existing = existing
        checks["placement"] = GateCheck("placement", True)

    agency = af.agency
    if agency is None or agency.bank_id != loan.bank_id:
        checks["agency"] = GateCheck("agency", False, NO_AGENCY,
                                     f"agency {af.agency_id} does not work for this loan's bank"
                                     if af.agency_id else f"loan {lan}: no agency named for placement")
        agency = None
    elif agency.status != "ACTIVE":
        checks["agency"] = GateCheck("agency", False, AGENCY_NOT_ACTIVE, f"agency {agency.code} is {agency.status}")
    else:
        checks["agency"] = GateCheck("agency", True)
    res.agency = agency

    contract = af.contract if agency is not None else None
    res.contract = contract
    if agency is None:
        for g in ("contract", "authorisation", "coverage", "capacity"):
            checks[g] = GateCheck(g, None, None, "not judged: no agency")
        return res
    if contract is None:
        checks["contract"] = GateCheck("contract", False, NO_CONTRACT_IN_FORCE,
                                       f"agency {agency.code} has no ACTIVE contract covering {af.on}")
        for g in ("authorisation", "coverage", "capacity"):
            checks[g] = GateCheck(g, None, None, "not judged: no contract in force")
        return res
    checks["contract"] = GateCheck("contract", True, None, contract.contract_no)

    if authorises(af.terms, loan):
        checks["authorisation"] = GateCheck("authorisation", True)
    else:
        checks["authorisation"] = GateCheck(
            "authorisation", False, NOT_AUTHORISED,
            f"contract {contract.contract_no} does not authorise "
            f"{loan.loan_type.value} / {dpd_bucket_for(loan.dpd).value}")

    if covers(af.coverage, lf.region_path):
        checks["coverage"] = GateCheck("coverage", True)
    else:
        checks["coverage"] = GateCheck(
            "coverage", False, NOT_COVERED,
            f"branch {loan.branch_code} has no region" if lf.region_path is None else
            f"contract {contract.contract_no} does not cover region {lf.region_path}")

    if res.existing is not None or contract.max_placed_cases is None:
        checks["capacity"] = GateCheck("capacity", True)       # already holds it, or uncapped
    elif af.placed + planned >= contract.max_placed_cases:
        checks["capacity"] = GateCheck(
            "capacity", False, CONTRACT_FULL,
            f"contract {contract.contract_no} is at its cap of {contract.max_placed_cases} placements")
    else:
        checks["capacity"] = GateCheck("capacity", True)
    return res


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
        return authorises(self.terms_of(contract), loan)

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
        return covers(self.coverage_paths(contract), self.branch_region_path(loan.bank_id, loan.branch_code))

    def covered_branch_codes(self, contract: AgencyContract) -> list[str]:
        """The bank's active branches whose region the contract covers, sorted:
        where a loan must be booked for this contract to take it."""
        tops = self.coverage_paths(contract)
        if not tops:
            return []
        rows = (self.db.query(Branch.branch_code, Region.path)
                .join(Region, Region.id == Branch.region_id)
                .filter(Branch.bank_id == contract.bank_id, Region.bank_id == contract.bank_id,
                        Branch.is_active.is_(True)))
        return sorted(code for code, path in rows if any(path_covers(t, path) for t in tops))

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

    # ── facts, loaded one at a time or in batch, then judged purely ─────────

    def commission_of(self, contract: AgencyContract) -> dict:
        return {(t, b): float(pct) for t, b, pct in
                self.db.query(AgencyContractTerm.loan_type, AgencyContractTerm.dpd_bucket,
                              AgencyContractTerm.commission_pct)
                .filter(AgencyContractTerm.contract_id == contract.id)}

    def terms_of(self, contract: AgencyContract) -> tuple | None:
        """(loan_type, bucket, is_authorised) rows; None when the contract has
        none (unrestricted by product/bucket, see the changelog)."""
        rows = (self.db.query(AgencyContractTerm.loan_type, AgencyContractTerm.dpd_bucket,
                              AgencyContractTerm.is_authorised)
                .filter(AgencyContractTerm.contract_id == contract.id).all())
        return tuple((t, b, bool(a)) for t, b, a in rows) or None

    def loan_facts(self, loans: list[Loan]) -> list[LoanFacts]:
        """Every loan's gate-relevant facts in two queries, whatever the count."""
        if not loans:
            return []
        ids = [l.id for l in loans]
        active = {p.loan_id: p for p in self.db.query(Placement)
                  .filter(Placement.loan_id.in_(ids), Placement.status == "ACTIVE")}
        return [LoanFacts(loan=l, region_path=self.branch_region_path(l.bank_id, l.branch_code),
                          active=active.get(l.id)) for l in loans]

    def agency_facts(self, agency_id: str | None, on: date, *, lock_contract: bool = False) -> AgencyFacts:
        agency = self.db.get(Agency, agency_id) if agency_id else None
        contract = (self.contract_in_force(agency.id, on, for_update=lock_contract)
                    if agency is not None and agency.status == "ACTIVE" else None)
        return AgencyFacts(
            agency_id=agency_id, on=on, agency=agency, contract=contract,
            terms=self.terms_of(contract) if contract is not None else None,
            coverage=self.coverage_paths(contract) if contract is not None else (),
            placed=self.placed_count(contract) if contract is not None else 0,
            commission=self.commission_of(contract) if contract is not None else {})

    def agency_facts_many(self, bank_id: str, on: date) -> dict[str, AgencyFacts]:
        """Every agency of the bank, for the engine: one query per kind of fact."""
        agencies = self.db.query(Agency).filter(Agency.bank_id == bank_id).all()
        contracts = {}
        for c in (self.db.query(AgencyContract)
                  .filter(AgencyContract.bank_id == bank_id, AgencyContract.status == "ACTIVE",
                          AgencyContract.start_date <= on, AgencyContract.end_date >= on)
                  .order_by(AgencyContract.agency_id, AgencyContract.start_date.desc())):
            contracts.setdefault(c.agency_id, c)             # the latest-starting, as contract_in_force
        cids = [c.id for c in contracts.values()]
        terms: dict[str, list] = {}
        commission: dict[str, dict] = {}
        for cid, t, b, a, pct in (self.db.query(AgencyContractTerm.contract_id, AgencyContractTerm.loan_type,
                                                AgencyContractTerm.dpd_bucket, AgencyContractTerm.is_authorised,
                                                AgencyContractTerm.commission_pct)
                                  .filter(AgencyContractTerm.contract_id.in_(cids))):
            terms.setdefault(cid, []).append((t, b, bool(a)))
            commission.setdefault(cid, {})[(t, b)] = float(pct)
        cover: dict[str, list] = {}
        for cid, path in (self.db.query(AgencyRegion.contract_id, Region.path)
                          .join(Region, Region.id == AgencyRegion.region_id)
                          .filter(AgencyRegion.contract_id.in_(cids), Region.bank_id == bank_id)):
            cover.setdefault(cid, []).append(path)
        placed = dict(self.db.query(Placement.contract_id, func.count(Placement.id))
                      .filter(Placement.contract_id.in_(cids), Placement.status == "ACTIVE")
                      .group_by(Placement.contract_id).all())
        out = {}
        for a in agencies:
            c = contracts.get(a.id) if a.status == "ACTIVE" else None
            out[a.id] = AgencyFacts(
                agency_id=a.id, on=on, agency=a, contract=c,
                terms=tuple(terms[c.id]) if c is not None and c.id in terms else None,
                coverage=tuple(sorted(cover.get(c.id, ()))) if c is not None else (),
                placed=int(placed.get(c.id, 0)) if c is not None else 0,
                commission=commission.get(c.id, {}) if c is not None else {})
        return out

    def evaluate(self, loan: Loan, agency_id: str | None, on: date, *, planned: int = 0,
                 lock_contract: bool = False) -> GateResult:
        """Every gate for placing `loan` with `agency_id` on `on`, without
        raising (judge()). `planned` is how many placements the caller has
        already decided against this agency's contract in the same batch but
        not yet written, so a preview of 10 loans against 3 free slots passes 3."""
        return judge(self.loan_facts([loan])[0], self.agency_facts(agency_id, on, lock_contract=lock_contract),
                     planned=planned)

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

    # ── what a bank-made placement carries ──────────────────────────────────

    @staticmethod
    def case_number_for(placement: Placement) -> str:
        """"PL" + yymmdd + 8 base32 characters of the placement id: 16 chars,
        inside cases.case_number VARCHAR(20); UNIQUE (bank_id, case_number)
        is the guard. 8, not 6: 30 bits collide at ~1% for 5,000 cases a day."""
        tag = base64.b32encode(uuid.UUID(str(placement.id)).bytes).decode()[:8]
        return f"PL{placement.placed_on:%y%m%d}{tag}"

    @staticmethod
    def case_target_amount(loan: Loan) -> float:
        """The overdue amount, or the whole outstanding when nothing is overdue.
        Every opener of a case uses it (ingest_daily, manual, engine)."""
        overdue = float(loan.overdue_amount or 0.0)
        return overdue if overdue > 0 else float(loan.total_outstanding or 0.0)

    def recovery_expectation(self, loan: Loan, on: date) -> tuple[float, str, date] | None:
        """(P(pay), prediction id, as_of) from the loan's newest modelled
        recovery_risk prediction on or before `on`; None when there is none.
        Abstains rather than scoring or imputing (ADR 0005). The stored
        probability is P(bad), so P(pay) is its complement
        (ml_scoring_service.score_cases)."""
        from app.ml.pipeline.config import RECOVERY_RISK
        from app.models.model_prediction import ModelPrediction
        row = (self.db.query(ModelPrediction.id, ModelPrediction.as_of_date, ModelPrediction.probability)
               .filter(ModelPrediction.loan_id == loan.id, ModelPrediction.bank_id == loan.bank_id,
                       ModelPrediction.model_name == RECOVERY_RISK.name,
                       ModelPrediction.is_modelled.is_(True), ModelPrediction.probability.isnot(None),
                       ModelPrediction.as_of_date <= on)
               .order_by(ModelPrediction.as_of_date.desc(), ModelPrediction.scored_at.desc())
               .first())
        if row is None:
            return None
        return round(1.0 - float(row.probability), 6), row.id, row.as_of_date

    def attach_expectation(self, placement: Placement, loan: Loan) -> None:
        exp = self.recovery_expectation(loan, placement.placed_on)
        if exp is None:
            return
        from app.services.bank.expected_recovery import expected_recovery_inr
        p_pay, pred_id, as_of = exp
        placement.expected_recovery_prob = p_pay
        # The amount is D06's one definition (ce), written here, never restated.
        placement.expected_recovery_inr = expected_recovery_inr(
            prob=p_pay, overdue_at_placement=float(placement.overdue_at_placement or 0.0),
            exposure_at_placement=float(placement.exposure_at_placement or 0.0),
            dpd_bucket=placement.dpd_bucket_at_placement, loan_type=loan.loan_type)
        placement.model_prediction_id, placement.model_prediction_as_of = pred_id, as_of

    # ── ending a placement ──────────────────────────────────────────────────

    def recall(self, placement: Placement, *, on: date, end_reason: str, note: str,
               ended_by: str | None) -> list[Case]:
        """End an ACTIVE placement as RECALLED and close its open cases the way
        a feed recall closes a case (scripts/ingest_daily._close_case_recall):
        CLOSED, closure_reason RECALLED, notes starting with the prefix the
        outcome labeller censors on (ml/pipeline/outcomes.RECALL_NOTE_PREFIX)."""
        from app.ml.pipeline.outcomes import RECALL_NOTE_PREFIX
        from app.models.case import RESOLVED_STATUSES, ClosureReason
        if placement.status != "ACTIVE":
            raise ValueError(f"placement {placement.id} is {placement.status}, not ACTIVE")
        placement.status = "RECALLED"
        placement.ended_on = on
        placement.ended_by = ended_by
        placement.end_reason = end_reason[:30]
        closed = []
        now = datetime.now(timezone.utc)
        for case in (self.db.query(Case)
                     .filter(Case.placement_id == placement.id, Case.agency_id == placement.agency_id)):
            if case.status in RESOLVED_STATUSES:
                continue
            case.status = CaseStatus.CLOSED
            case.resolved_at = now
            case.closure_reason = ClosureReason.RECALLED.value
            case.resolution_notes = f"{RECALL_NOTE_PREFIX} on {on}. Reason: {note}".strip()
            closed.append(case)
        self.db.flush()
        return closed

    # Feed events that end a placement without a recall (coordinator, 2026-09-29):
    # the bank was paid or settled, so the placement did its job; the loan was
    # written off, so it goes back to the bank; or the borrower died, so there
    # is nothing left to work (RESOLVED, not RETURNED: nobody failed).
    # Keyed by the feed action, spelled as the case's ClosureReason (the same
    # four codes); "DECEASED" here is that closure reason, not the customer
    # tag (CUSTOMER_TAG_DECEASED, test_deceased_tag).
    FEED_END_STATUS = {ClosureReason.PAID_DIRECT.value: "RESOLVED", ClosureReason.SETTLED.value: "RESOLVED",
                       ClosureReason.WRITTEN_OFF.value: "RETURNED", ClosureReason.DECEASED.value: "RESOLVED"}

    def end_from_feed(self, placement: Placement, *, bank_action: str, on: date) -> None:
        """End an ACTIVE placement because the bank's feed closed the loan's
        case (FEED_END_STATUS).
        Unlike a recall it closes no other case: what the feed closed, it
        closed with its own typed reason."""
        status = self.FEED_END_STATUS[bank_action]
        if placement.status != "ACTIVE":
            raise ValueError(f"placement {placement.id} is {placement.status}, not ACTIVE")
        placement.status = status
        placement.ended_on = on
        placement.ended_by = None
        placement.end_reason = f"FEED_{bank_action}"[:30]
        self.db.flush()

    # A loan the bank has closed cannot stay placed. Before the feed ended
    # placements (2026-09-29) a closure left them ACTIVE; this ends those once.
    RECONCILE_END_STATUS = {LoanStatus.CLOSED: "RESOLVED", LoanStatus.SETTLED: "RESOLVED",
                            LoanStatus.WRITTEN_OFF: "RETURNED"}
    RECONCILE_REASON = "FEED_RECONCILE"

    def reconcile_orphans(self, bank_id: str, *, on: date, dry_run: bool = False) -> dict:
        """End every ACTIVE placement of `bank_id` whose loan is no longer open
        (RESOLVED for closed/settled, RETURNED for written off), end_reason
        FEED_RECONCILE, system actor, one PLACEMENT_ENDED audit row each.
        Cases are not touched: the feed that closed the loan closed them, or a
        person must. Idempotent. The caller commits (dry_run changes nothing)."""
        from app.core.audit import stage_audit
        from app.models.audit_log import AuditAction
        rows = (self.db.query(Placement, Loan.status)
                .join(Loan, and_(Loan.id == Placement.loan_id, Loan.bank_id == Placement.bank_id))
                .filter(Placement.bank_id == bank_id, Placement.status == "ACTIVE",
                        Loan.status.notin_(PLACEABLE_LOAN_STATUSES))
                .order_by(Placement.id).all())
        by_status: dict[str, int] = {}
        for p, loan_status in rows:
            status = self.RECONCILE_END_STATUS.get(loan_status, "RESOLVED")
            by_status[status] = by_status.get(status, 0) + 1
            if dry_run:
                continue
            p.status, p.ended_on, p.ended_by, p.end_reason = status, on, None, self.RECONCILE_REASON
            stage_audit(self.db, action=AuditAction.PLACEMENT_ENDED, user_id=None, entity_type="Placement",
                        entity_id=p.id, bank_id=p.bank_id, agency_id=p.agency_id,
                        details={"source": "RECONCILE", "loan_status": loan_status.value, "status": status,
                                 "agency_id": p.agency_id, "loan_id": p.loan_id})
        if not dry_run:
            self.db.flush()
        return {"bank_id": bank_id, "ended": len(rows), "by_status": by_status, "dry_run": dry_run}

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
