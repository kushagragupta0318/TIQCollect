"""The placement engine, bank → agency (plan §6.3, task D09, ADR 0010).

This module is built in steps; this part holds the recall rules. A rule
comes from the placement's OWN contract (the terms the agency signed for it):

  CONTRACT_END  recall_at_contract_end, and the plan date is past
                expected_end_on or the contract is no longer in force
  SLA_BREACH    recall_on_sla_breach, the plan date is past
                sla_first_visit_due, and no visit came on or before it
  NO_ACTIVITY   recall_no_activity_days = N, and no visit, call or payment on
                the placement's cases for N days (counted from placed_on when
                there has been none)

Days are IST calendar days (core.geo.IST), as everywhere else a visit's day is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.geo import IST
from app.models.call_log import CallLog
from app.models.case import Case
from app.models.payment import Payment
from app.models.placement import Placement
from app.models.tenancy import AgencyContract
from app.models.visit import Visit

ENGINE_VERSION = "placement-engine-1.0.0"

CONTRACT_END = "CONTRACT_END"
SLA_BREACH = "SLA_BREACH"
NO_ACTIVITY = "NO_ACTIVITY"
#: When several rules fire, the first here is the recall's end_reason.
RECALL_RULES = (CONTRACT_END, SLA_BREACH, NO_ACTIVITY)


def ist_day(ts: datetime | None) -> date | None:
    """The IST calendar day of a timestamp; a naive one is read as UTC (how
    SQLite hands back a timezone-aware column)."""
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(IST).date()


@dataclass(frozen=True)
class PlacementActivity:
    """What the rules read about one ACTIVE placement."""
    placement: Placement
    contract: AgencyContract
    last_activity: date | None      # newest visit / call / payment day on its cases
    first_visit: date | None        # earliest visit day on its cases


@dataclass
class RecallVerdict:
    placement: Placement
    rules: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    @property
    def recalled(self) -> bool:
        return bool(self.rules)

    @property
    def end_reason(self) -> str | None:
        return next((r for r in RECALL_RULES if r in self.rules), None)


def contract_in_force_on(contract: AgencyContract, day: date) -> bool:
    """The contract_in_force rule (placement_service) for a known contract row."""
    return contract.status == "ACTIVE" and contract.start_date <= day <= contract.end_date


def judge_recall(a: PlacementActivity, plan_date: date) -> RecallVerdict:
    """The recall rules for one placement, pure."""
    p, c, v = a.placement, a.contract, RecallVerdict(a.placement)
    if c.recall_at_contract_end:
        if p.expected_end_on is not None and plan_date > p.expected_end_on:
            v.rules.append(CONTRACT_END)
            v.details.append(f"placement ran to {p.expected_end_on}")
        elif not contract_in_force_on(c, plan_date):
            v.rules.append(CONTRACT_END)
            v.details.append(f"contract {c.contract_no} is not in force on {plan_date} ({c.status})")
    if c.recall_on_sla_breach and p.sla_first_visit_due is not None and plan_date > p.sla_first_visit_due:
        if a.first_visit is None or a.first_visit > p.sla_first_visit_due:
            v.rules.append(SLA_BREACH)
            v.details.append(f"no visit by {p.sla_first_visit_due}"
                             + (f" (first on {a.first_visit})" if a.first_visit else ""))
    n = c.recall_no_activity_days
    if n is not None and n > 0:
        since = max(d for d in (p.placed_on, a.last_activity) if d is not None)
        idle = (plan_date - since).days
        if idle >= n:
            v.rules.append(NO_ACTIVITY)
            v.details.append(f"no visit, call or payment for {idle} days (limit {n})")
    return v


def placement_activity(db: Session, bank_id: str) -> list[PlacementActivity]:
    """Every ACTIVE placement of the bank with its contract and its cases'
    activity, in five queries whatever the book's size."""
    rows = (db.query(Placement, AgencyContract)
            .join(AgencyContract, AgencyContract.id == Placement.contract_id)
            .filter(Placement.bank_id == bank_id, Placement.status == "ACTIVE")
            .order_by(Placement.placed_on, Placement.id).all())
    if not rows:
        return []

    def newest(ts_col, model):
        return dict(db.query(Case.placement_id, func.max(ts_col)).select_from(model)
                    .join(Case, Case.id == model.case_id)
                    .filter(Case.bank_id == bank_id, Case.placement_id.isnot(None))
                    .group_by(Case.placement_id).all())

    last_visit, last_call, last_pay = newest(Visit.check_in_time, Visit), newest(CallLog.called_at, CallLog), \
        newest(Payment.payment_date, Payment)
    first_visit = dict(db.query(Case.placement_id, func.min(Visit.check_in_time)).select_from(Visit)
                       .join(Case, Case.id == Visit.case_id)
                       .filter(Case.bank_id == bank_id, Case.placement_id.isnot(None))
                       .group_by(Case.placement_id).all())
    out = []
    for p, c in rows:
        days = [ist_day(t) for t in (last_visit.get(p.id), last_call.get(p.id), last_pay.get(p.id)) if t is not None]
        out.append(PlacementActivity(placement=p, contract=c, last_activity=max(days) if days else None,
                                     first_visit=ist_day(first_visit.get(p.id))))
    return out


def recall_verdicts(db: Session, bank_id: str, plan_date: date) -> list[RecallVerdict]:
    """A verdict for every ACTIVE placement of the bank; `recalled` ones fired a rule."""
    return [judge_recall(a, plan_date) for a in placement_activity(db, bank_id)]
