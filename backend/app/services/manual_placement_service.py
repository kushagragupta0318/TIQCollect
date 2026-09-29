"""A bank placing loans with an agency by hand, and recalling a placement by
hand (plan §6.3, STANDALONE-TASKS D08).

The rules are placement_service's; this module owns the transaction. A
batch is recorded the way the engine records a run (DATA-MODEL-V2
placements.placement_run_id, coordinator Q2): one `placement_runs` row,
strategy MANUAL_BATCH, and one `placement_decisions` row per loan with every
gate's verdict, so "why was this loan placed (or not)" has one answer
whichever path placed it.

One person acts alone here (no four-eyes, coordinator Q3); the audit row
names them. The engine's apply is the four-eyes path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.core.audit import stage_audit
from app.core.errors import AppException, ErrorCode
from app.models.audit_log import AuditAction
from app.models.loan import Loan
from app.models.placement import Placement
from app.models.planning import PlacementDecision, PlacementRun
from app.models.tenancy import Agency
from app.services.placement_read_service import apply_region_limit
from app.services.placement_service import GateResult, PlacementService

MAX_BATCH = 500
MAX_RECALL_NOTE = 500
MANUAL_RECALL = "MANUAL_RECALL"


@dataclass
class LoanVerdict:
    loan_id: str
    loan_account_number: str
    outcome: str                     # placement_outcomes: PLACED / KEPT / BLOCKED
    reason: str
    gates: dict
    placement_id: str | None = None
    case_id: str | None = None
    case_number: str | None = None


@dataclass
class BatchResult:
    agency_id: str
    on: date
    verdicts: list[LoanVerdict] = field(default_factory=list)
    run_id: str | None = None
    headroom_before: int | None = None

    def count(self, outcome: str) -> int:
        return sum(1 for v in self.verdicts if v.outcome == outcome)


def _not_found(_what: str) -> AppException:
    # "Not found" and "not yours" are the same answer (A03), and the same body
    # core/ids gives a malformed id.
    return AppException(404, ErrorCode.NOT_FOUND, "Not found")


def _verdict(loan: Loan, res: GateResult) -> tuple[str, str]:
    if res.ok:
        if res.existing is not None:
            return "KEPT", "already placed with this agency"
        return "PLACED", "every gate passed"
    failed = res.first_failure
    return "BLOCKED", f"{failed.reason}: {failed.detail}"


class ManualPlacementService:
    def __init__(self, db: Session):
        self.db = db
        self.rules = PlacementService(db)

    # ── lookups, always inside the caller's bank ────────────────────────────

    def _agency(self, bank_id: str, agency_id: str) -> Agency:
        agency = self.db.get(Agency, agency_id)
        if agency is None or agency.bank_id != bank_id:
            raise _not_found("Agency")
        return agency

    def _loans(self, bank_id: str, loan_ids: list[str], region_limit=None) -> list[Loan]:
        ids = list(dict.fromkeys(loan_ids))                  # de-duplicated, order kept
        if not ids:
            raise AppException(422, ErrorCode.VALIDATION_ERROR, "No loans named")
        if len(ids) > MAX_BATCH:
            raise AppException(422, ErrorCode.VALIDATION_ERROR,
                               f"At most {MAX_BATCH} loans per batch; {len(ids)} named")
        # A loan outside the caller's region limit is "not found", like another bank's.
        rows = {l.id: l for l in apply_region_limit(
            self.db.query(Loan).filter(Loan.bank_id == bank_id, Loan.id.in_(ids)), region_limit)}
        if len(rows) != len(ids):
            raise _not_found("Loan")
        return [rows[i] for i in ids]

    # ── preview: every gate, no writes ──────────────────────────────────────

    def preview(self, *, bank_id: str, agency_id: str, loan_ids: list[str], on: date,
                region_limit=None) -> BatchResult:
        agency = self._agency(bank_id, agency_id)
        loans = self._loans(bank_id, loan_ids, region_limit)
        out = BatchResult(agency_id=agency.id, on=on)
        planned = 0
        for loan in loans:
            res = self.rules.evaluate(loan, agency.id, on, planned=planned)
            if out.headroom_before is None and res.contract is not None:
                out.headroom_before = self.rules.headroom(res.contract)
            outcome, reason = _verdict(loan, res)
            planned += outcome == "PLACED"
            out.verdicts.append(LoanVerdict(loan.id, loan.loan_account_number, outcome, reason, res.as_json()))
        return out

    # ── apply: place what passes, record every verdict, one commit ──────────

    def apply(self, *, bank_id: str, actor_id: str, agency_id: str, loan_ids: list[str], on: date,
              ip_address: str | None = None, region_limit=None) -> BatchResult:
        agency = self._agency(bank_id, agency_id)
        loans = self._loans(bank_id, loan_ids, region_limit)
        now = datetime.now(timezone.utc)
        run = PlacementRun(bank_id=bank_id, plan_date=on, strategy="MANUAL_BATCH", status="APPLIED",
                           simulate=False, exploration_rate=0.0, created_by=actor_id, applied_by=actor_id,
                           applied_at=now, total_loans_evaluated=len(loans),
                           parameters={"agency_id": agency.id, "loans_named": len(loans)})
        self.db.add(run)
        self.db.flush()
        out = BatchResult(agency_id=agency.id, on=on, run_id=run.id)
        try:
            for loan in loans:
                # lock_contract: the cap is counted under the contract's row lock.
                res = self.rules.evaluate(loan, agency.id, on, lock_contract=True)
                if out.headroom_before is None and res.contract is not None:
                    out.headroom_before = self.rules.headroom(res.contract)
                outcome, reason = _verdict(loan, res)
                v = LoanVerdict(loan.id, loan.loan_account_number, outcome, reason, res.as_json())
                if outcome == "PLACED":
                    self._place(loan, agency, on, actor_id, run, v, ip_address)
                elif outcome == "KEPT":
                    v.placement_id = res.existing.id
                self.db.add(PlacementDecision(
                    plan_date=on, bank_id=bank_id, run_id=run.id, loan_id=loan.id,
                    chosen_agency_id=agency.id if outcome != "BLOCKED" else None,
                    previous_agency_id=None, outcome=outcome, reason=reason, gate_results=v.gates,
                    score_breakdown={"source": "MANUAL"}))
                out.verdicts.append(v)
            run.total_placed = out.count("PLACED")
            run.total_kept = out.count("KEPT")
            run.total_blocked = out.count("BLOCKED")
            run.summary = {"placed": run.total_placed, "kept": run.total_kept, "blocked": run.total_blocked}
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return out

    def _place(self, loan: Loan, agency: Agency, on: date, actor_id: str, run: PlacementRun,
               v: LoanVerdict, ip_address: str | None) -> None:
        placement = self.rules.place_new_loan(loan, agency_id=agency.id, on=on, source="MANUAL",
                                              placed_by=actor_id, placement_run_id=run.id)
        self.rules.attach_expectation(placement, loan)
        case = self.rules.open_case(placement, loan, case_number=self.rules.case_number_for(placement),
                                    target_amount=self.rules.case_target_amount(loan))
        v.placement_id, v.case_id, v.case_number = placement.id, case.id, case.case_number
        stage_audit(self.db, action=AuditAction.PLACEMENT_CREATED, user_id=actor_id,
                    entity_type="Placement", entity_id=placement.id,
                    details={"source": "MANUAL", "run_id": run.id, "loan_id": loan.id,
                             "agency_id": agency.id, "case_id": case.id},
                    ip_address=ip_address)

    # ── manual recall of one placement ──────────────────────────────────────

    def recall(self, *, bank_id: str, actor_id: str, placement_id: str, note: str, on: date,
               ip_address: str | None = None, region_limit=None) -> tuple[Placement, list]:
        note = " ".join((note or "").split())
        if not note:
            raise AppException(422, ErrorCode.VALIDATION_ERROR, "A reason is required to recall a placement")
        if len(note) > MAX_RECALL_NOTE:
            raise AppException(422, ErrorCode.VALIDATION_ERROR,
                               f"The reason is limited to {MAX_RECALL_NOTE} characters")
        placement = (self.db.query(Placement)
                     .filter(Placement.id == placement_id, Placement.bank_id == bank_id)
                     .with_for_update().first())
        if placement is not None and region_limit is not None:
            in_region = apply_region_limit(self.db.query(Loan.id).filter(
                Loan.id == placement.loan_id, Loan.bank_id == bank_id), region_limit).first()
            placement = placement if in_region else None
        if placement is None:
            raise _not_found("Placement")
        if placement.status != "ACTIVE":
            raise AppException(409, ErrorCode.CONFLICT, f"Placement is already {placement.status}")
        try:
            closed = self.rules.recall(placement, on=on, end_reason=MANUAL_RECALL, note=note, ended_by=actor_id)
            stage_audit(self.db, action=AuditAction.PLACEMENT_RECALLED, user_id=actor_id,
                        entity_type="Placement", entity_id=placement.id,
                        details={"source": "MANUAL", "reason": note, "agency_id": placement.agency_id,
                                 "loan_id": placement.loan_id, "cases_closed": [c.id for c in closed]},
                        ip_address=ip_address)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return placement, closed
