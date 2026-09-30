"""The placement engine, bank → agency (plan §6.3, task D09, ADR 0010).

    recalls  = recall_verdicts(db, bank, day)           the contract-driven rules
    plan     = build_plan(db, bank, day, effects=...)   pool → options → min-cost flow → exploration
    run      = record_run(db, plan, ...)                 a placement_runs row + one decision per loan

Nothing here applies a decision; apply is a separate, four-eyes step.

A recall rule comes from the placement's OWN contract (the terms the agency
signed for it):

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

import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.core.geo import IST
from app.models.call_log import CallLog
from app.models.case import Case
from app.models.loan import Loan, dpd_bucket_for
from app.models.model_prediction import ModelPrediction
from app.models.payment import Payment
from app.models.placement import Placement
from app.models.planning import PlacementDecision, PlacementRun
from app.models.tenancy import AgencyContract, Bank, Branch, Region
from app.models.visit import Visit
from app.services.placement_service import (
    PLACEABLE_LOAN_STATUSES, AgencyFacts, LoanFacts, PlacementService, judge,
)

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


# ═══ The plan: pool → options → min-cost flow → exploration → decisions ═══════

#: The run's wall-clock budget (ADR 0010): a bigger book is refused, not left hanging.
TIME_LIMIT_S = 30.0
#: Rupees become integer arc costs in paise.
COST_SCALE = 100
MIN_DPD = 1
UNCAPPED = 10**9
LIMITATIONS = ("The agency effect is regional collection efficiency shrunk toward regional peers; it is not "
               "adjusted for product or DPD-bucket mix, which enters only through each loan's P(pay) (ADR 0010).")
DEMO_BOOK_NOTE = "Agency history comes from a demo book, not real collections."


class EngineTooSlow(Exception):
    """The run passed TIME_LIMIT_S; nothing was written."""


@dataclass
class Candidate:
    loan: Loan
    facts: LoanFacts
    region_id: str | None
    expected: float                  # E: expected recovery, or the overdue amount when not modelled
    is_modelled: bool
    p_pay: float | None
    prediction: tuple | None         # (id, as_of, model_version)
    recall: RecallVerdict | None = None

    @property
    def previous_agency_id(self) -> str | None:
        return self.recall.placement.agency_id if self.recall is not None else None


@dataclass(frozen=True)
class Option:
    agency_id: str
    value: float
    multiplier: float
    effect_n: int
    commission_pct: float
    commission_known: bool


@dataclass
class Plan:
    candidates: list[Candidate]
    options: dict[str, list[Option]]              # loan_id -> eligible agencies, best value first
    refusals: dict[str, dict[str, str]]           # loan_id -> agency_id -> first failed gate's reason
    assignment: dict[str, str | None]             # loan_id -> agency_id | None
    exploration: dict[str, dict]                  # loan_id -> the ADR 0003 keys
    capacity: dict[str, int]
    kept: int
    seed: int
    elapsed_s: float = 0.0


def _latest_predictions(db: Session, bank_id: str, loan_ids: list[str], on: date) -> dict[str, tuple]:
    """loan_id -> (P(pay), id, as_of, version) from the newest modelled
    recovery_risk prediction on or before `on`. P(pay) = 1 - the stored P(bad),
    as placement_service.recovery_expectation reads it."""
    from app.ml.pipeline.config import RECOVERY_RISK
    out: dict[str, tuple] = {}
    for i in range(0, len(loan_ids), 900):
        best: dict[str, tuple] = {}
        for lid, pid, as_of, scored, prob, ver in (
                db.query(ModelPrediction.loan_id, ModelPrediction.id, ModelPrediction.as_of_date,
                         ModelPrediction.scored_at, ModelPrediction.probability, ModelPrediction.model_version)
                .filter(ModelPrediction.loan_id.in_(loan_ids[i:i + 900]), ModelPrediction.bank_id == bank_id,
                        ModelPrediction.model_name == RECOVERY_RISK.name, ModelPrediction.is_modelled.is_(True),
                        ModelPrediction.probability.isnot(None), ModelPrediction.as_of_date <= on)):
            key = (as_of, str(scored or ""), str(pid))
            if lid not in best or key > best[lid][0]:
                best[lid] = (key, (round(1.0 - float(prob), 6), pid, as_of, ver))
        out.update({lid: v for lid, (_, v) in best.items()})
    return out


def build_pool(db: Session, bank_id: str, plan_date: date, recalls: list[RecallVerdict]) -> list[Candidate]:
    """Open, delinquent, unplaced loans of the bank, plus the loans of this
    run's recalls. A recalled loan's placement ends at apply, so it is judged
    as unplaced, with its previous agency excluded."""
    from app.services.bank.expected_recovery import expected_recovery_inr
    recalled = {v.placement.loan_id: v for v in recalls if v.recalled}
    placed = (db.query(Placement.id)
              .filter(Placement.loan_id == Loan.id, Placement.bank_id == bank_id, Placement.status == "ACTIVE")
              .exists())
    q = db.query(Loan).filter(Loan.bank_id == bank_id, Loan.status.in_(PLACEABLE_LOAN_STATUSES),
                              Loan.dpd >= MIN_DPD)
    unplaced = q.filter(~placed).all()
    # This run's recalls are placed until apply; they join the pool too.
    again = q.filter(Loan.id.in_(list(recalled))).all() if recalled else []
    # Ordered by id: two runs on the same book give identical decisions.
    loans = sorted({l.id: l for l in [*unplaced, *again]}.values(), key=lambda l: str(l.id))
    branch = {code: (rid, path) for code, rid, path in
              db.query(Branch.branch_code, Branch.region_id, Region.path)
              .outerjoin(Region, and_(Region.id == Branch.region_id, Region.bank_id == Branch.bank_id))
              .filter(Branch.bank_id == bank_id)}
    preds = _latest_predictions(db, bank_id, [l.id for l in loans], plan_date)
    out = []
    for l in loans:
        rid, path = branch.get(l.branch_code, (None, None))
        pred = preds.get(l.id)
        expected = None
        if pred is not None:
            expected = expected_recovery_inr(
                prob=pred[0], overdue_at_placement=float(l.overdue_amount or 0.0),
                exposure_at_placement=float(l.total_outstanding or 0.0),
                dpd_bucket=dpd_bucket_for(l.dpd), loan_type=l.loan_type)
        out.append(Candidate(
            loan=l, facts=LoanFacts(loan=l, region_path=path, active=None), region_id=rid,
            # ADR 0005: with no model score the value falls back to the overdue
            # amount and says so (is_modelled False); no probability is imputed.
            expected=float(expected) if expected is not None else float(l.overdue_amount or 0.0),
            is_modelled=expected is not None, p_pay=pred[0] if pred else None,
            prediction=(pred[1], pred[2], pred[3]) if pred else None, recall=recalled.get(l.id)))
    return out


def options_for(c: Candidate, agencies: dict[str, AgencyFacts], effects: dict) -> tuple[list[Option], dict]:
    """The agencies `c` may go to, valued E x m x (1 - c); and the first
    failed gate of each one it may not."""
    opts, refused = [], {}
    bucket = dpd_bucket_for(c.loan.dpd)
    for aid in sorted(agencies):
        if aid == c.previous_agency_id:
            refused[aid] = "RECALLED_FROM"
            continue
        res = judge(c.facts, agencies[aid])
        failed = [g for g, chk in res.checks.items() if chk.passed is False]
        # Capacity is the flow's constraint here (recalls in this run free
        # slots judge() cannot see), so a loan failing ONLY capacity stays eligible.
        if failed and failed != ["capacity"]:
            refused[aid] = res.first_failure.reason
            continue
        m, n = effects.get((aid, c.region_id), (1.0, 0))
        pct = agencies[aid].commission.get((c.loan.loan_type, bucket))
        value = c.expected * m * (1.0 - (pct or 0.0) / 100.0)
        opts.append(Option(aid, round(value, 2), m, n, pct or 0.0, pct is not None))
    opts.sort(key=lambda o: (-o.value, o.agency_id))
    return opts, refused


def solve(candidates: list[Candidate], options: dict[str, list[Option]], capacity: dict[str, int]) -> dict:
    """Min-cost flow (OR-Tools): each loan to at most one of its eligible
    agencies, within each agency's capacity, maximising the total value.
    source → loan (1) → agency (−value) → sink (capacity); loan → UNPLACED (0)."""
    from ortools.graph.python import min_cost_flow
    assignment: dict[str, str | None] = {c.loan.id: None for c in candidates}
    live = sorted((c for c in candidates if options.get(c.loan.id)), key=lambda c: str(c.loan.id))
    if not live:
        return assignment
    smcf = min_cost_flow.SimpleMinCostFlow()
    n = len(live)
    source, sink, unplaced = 0, 1, 2
    loan_node = {c.loan.id: 3 + i for i, c in enumerate(live)}
    agency_ids = sorted({o.agency_id for c in live for o in options[c.loan.id]})
    agency_node = {a: 3 + n + j for j, a in enumerate(agency_ids)}
    arcs = []
    for c in live:
        ln = loan_node[c.loan.id]
        smcf.add_arc_with_capacity_and_unit_cost(source, ln, 1, 0)
        smcf.add_arc_with_capacity_and_unit_cost(ln, unplaced, 1, 0)
        for o in options[c.loan.id]:
            arcs.append((smcf.add_arc_with_capacity_and_unit_cost(
                ln, agency_node[o.agency_id], 1, -int(round(o.value * COST_SCALE))), c.loan.id, o.agency_id))
    smcf.add_arc_with_capacity_and_unit_cost(unplaced, sink, n, 0)
    for a in agency_ids:
        smcf.add_arc_with_capacity_and_unit_cost(agency_node[a], sink, max(0, min(capacity.get(a, 0), n)), 0)
    smcf.set_node_supply(source, n)
    smcf.set_node_supply(sink, -n)
    status = smcf.solve()
    if status != smcf.OPTIMAL:
        raise RuntimeError(f"min-cost flow did not solve: status {status}")
    for arc, lid, aid in arcs:
        if smcf.flow(arc) > 0:
            assignment[lid] = aid
    return assignment


def solve_within(candidates, options, capacity, seconds: float) -> dict:
    """solve() under a coarse deadline. SimpleMinCostFlow (ortools 9.11.4210)
    has no time limit, so it runs in a worker thread; past the deadline the
    request gets EngineTooSlow and nothing is written. The C++ solve cannot be
    interrupted: that thread finishes in the background and its result is
    dropped. So TIME_LIMIT_S is a soft budget for the response, not a CPU cap."""
    import concurrent.futures as cf
    if seconds <= 0:
        raise EngineTooSlow(f"The run took over {TIME_LIMIT_S:.0f} s and was stopped; nothing was written.")
    pool = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="placement-solve")
    try:
        return pool.submit(solve, candidates, options, capacity).result(timeout=seconds)
    except cf.TimeoutError:
        raise EngineTooSlow(f"The solve passed the {TIME_LIMIT_S:.0f} s budget and was abandoned; "
                            "nothing was written.") from None
    finally:
        pool.shutdown(wait=False)


def explore(assignment: dict, options: dict[str, list[Option]], rate: float, seed: int) -> dict[str, dict]:
    """ADR 0003's epsilon-greedy slice, for agencies. Swaps between two
    agencies (capacity stays exact), only to agencies each loan is eligible
    for; `rate` is the share of placements randomised, so initiators are
    halved (a swap randomises two); reproducible from `seed`. Mutates
    `assignment`; returns the keys to record per explored loan."""
    if rate <= 0:
        return {}
    rng = random.Random(seed)
    eligible = {lid: {o.agency_id for o in opts} for lid, opts in options.items()}
    placed = sorted(lid for lid, a in assignment.items() if a is not None and len(eligible.get(lid, ())) >= 2)
    n_explore = int(round(len(placed) * rate / 2.0))
    if n_explore <= 0:
        return {}
    log: dict[str, dict] = {}
    swapped: set[str] = set()
    for lid in rng.sample(placed, min(n_explore, len(placed))):
        if lid in swapped:
            continue
        current = assignment[lid]
        allowed = sorted(eligible[lid] - {current})
        if not allowed:
            continue
        target = rng.choice(allowed)
        partners = sorted(p for p, a in assignment.items()
                          if a == target and p not in swapped and current in eligible.get(p, ()))
        if not partners:
            continue
        partner = rng.choice(partners)
        assignment[lid], assignment[partner] = target, current
        for loan_id, frm in ((lid, current), (partner, target)):
            swapped.add(loan_id)
            log[loan_id] = {"exploration": True, "exploration_propensity": round(1.0 / len(eligible[loan_id]), 6),
                            "exploration_from_agency": frm, "exploration_n_eligible": len(eligible[loan_id]),
                            "exploration_seed": seed, "exploration_rate": rate}
    return log


def build_plan(db: Session, bank_id: str, plan_date: date, *, effects: dict, exploration_rate: float = 0.0,
               clock=time.monotonic) -> Plan:
    """Everything the engine decides, in memory; nothing is written.
    `effects` maps (agency_id, region_id) -> (multiplier, n), from
    agency_effect; a pair it lacks scores neutral (1.0, no evidence)."""
    start = clock()

    def check():
        if clock() - start > TIME_LIMIT_S:
            raise EngineTooSlow(f"The run took over {TIME_LIMIT_S:.0f} s and was stopped; nothing was written.")

    rules = PlacementService(db)
    verdicts = recall_verdicts(db, bank_id, plan_date)
    recalls = [v for v in verdicts if v.recalled]
    check()
    agencies = rules.agency_facts_many(bank_id, plan_date)
    freed: dict[str, int] = {}
    for v in recalls:
        freed[v.placement.contract_id] = freed.get(v.placement.contract_id, 0) + 1
    capacity = {}
    for aid, af in agencies.items():
        if af.contract is None:
            continue
        cap = af.contract.max_placed_cases
        # A recall frees a slot only on the contract its placement held.
        capacity[aid] = UNCAPPED if cap is None else max(0, cap - af.placed + freed.get(af.contract.id, 0))
    pool = build_pool(db, bank_id, plan_date, recalls)
    check()
    options, refusals = {}, {}
    for c in pool:
        options[c.loan.id], refusals[c.loan.id] = options_for(c, agencies, effects)
    check()
    assignment = solve_within(pool, options, capacity, TIME_LIMIT_S - (clock() - start))
    seed = int(plan_date.strftime("%Y%m%d"))
    log = explore(assignment, options, exploration_rate, seed)
    check()
    return Plan(candidates=pool, options=options, refusals=refusals, assignment=assignment, exploration=log,
                capacity=capacity, kept=len(verdicts) - len(recalls), seed=seed,
                elapsed_s=round(clock() - start, 3))


def effects_map(rows: list[dict]) -> dict:
    """agency_effect(pooled=True) rows -> {(agency_id, region_id): (multiplier, n)}."""
    return {(r["agency_id"], r["region_id"]): (float(r["multiplier"]), int(r["n"])) for r in rows}


def _versions() -> dict:
    from app.services.bank.agency_effect import VERSION as EFFECT_VERSION
    from app.services.bank.expected_recovery import VERSION as EXPECTED_VERSION
    return {"engine": ENGINE_VERSION, "agency_effect": EFFECT_VERSION, "expected_recovery": EXPECTED_VERSION}


def record_run(db: Session, plan: Plan, *, bank_id: str, plan_date: date, simulate: bool,
               exploration_rate: float, created_by: str | None, effect_window: dict | None = None) -> PlacementRun:
    """Write the run and one decision per pool loan; KEPT placements are
    counted, not written (ADR 0010). A recalled loan's row is RECALLED, with
    previous_agency_id, and chosen_agency_id set when it is re-placed in the
    same run. The caller commits."""
    from app.ml.pipeline.config import RECOVERY_RISK
    from app.services.placement_read_service import artifact_synthetic_warning
    versions = _versions()
    run = PlacementRun(bank_id=bank_id, plan_date=plan_date, strategy="MIN_COST_FLOW",
                       status="SIMULATED" if simulate else "PLANNED", simulate=simulate,
                       exploration_rate=exploration_rate, seed=plan.seed, created_by=created_by,
                       parameters={"exploration_rate": exploration_rate, "effect_window": effect_window or {},
                                   "versions": versions})
    db.add(run)
    db.flush()
    counts = {"PLACED": 0, "DEFERRED": 0, "BLOCKED": 0, "RECALLED": 0}
    gross = net = 0.0
    model_versions: set[str] = set()
    for c in plan.candidates:
        lid, opts = c.loan.id, plan.options.get(c.loan.id, [])
        chosen = plan.assignment.get(lid)
        opt = next((o for o in opts if o.agency_id == chosen), None)
        if opt is not None:
            outcome = "PLACED"
            reason = ("placed: exploration, a random eligible agency (ADR 0003/0010)" if lid in plan.exploration
                      else "placed: highest value within capacity")
            gross += c.expected * opt.multiplier
            net += opt.value
        elif opts:
            outcome, reason = "DEFERRED", "eligible agencies had no room left for it this run"
        else:
            codes = sorted(set(plan.refusals.get(lid, {}).values())) or ["NO_AGENCY"]
            outcome, reason = "BLOCKED", "no agency passes the gates: " + ", ".join(codes)
        counts[outcome] += 1
        row_outcome = outcome
        if c.recall is not None:
            counts["RECALLED"] += 1
            row_outcome = "RECALLED"
            reason = (f"recalled ({c.recall.end_reason}: {'; '.join(c.recall.details)}); "
                      + ("re-placed" if opt is not None else f"not re-placed: {reason}"))
        if c.prediction is not None:
            model_versions.add(c.prediction[2])
        breakdown = {
            "expected": round(c.expected, 2), "is_modelled": c.is_modelled, "p_pay": c.p_pay,
            "multiplier": opt.multiplier if opt else None, "effect_n": opt.effect_n if opt else None,
            "commission_pct": opt.commission_pct if opt else None,
            "commission_known": opt.commission_known if opt else None,
            "n_eligible": len(opts), "versions": versions,
            # An unmodelled loan with nothing overdue is worth 0 to the solve and
            # waits; say so rather than leave a silent DEFERRED.
            "zero_value_unmodelled": (not c.is_modelled) and c.expected <= 0,
            **({"replacement": outcome, "recall_rules": c.recall.rules} if c.recall is not None else {}),
            **plan.exploration.get(lid, {"exploration": False}),
        }
        db.add(PlacementDecision(
            plan_date=plan_date, bank_id=bank_id, run_id=run.id, loan_id=lid,
            chosen_agency_id=opt.agency_id if opt else None, previous_agency_id=c.previous_agency_id,
            outcome=row_outcome, reason=reason[:2000], score=opt.value if opt else None,
            score_breakdown=breakdown,
            gate_results={"refused": plan.refusals.get(lid, {}), "eligible": [o.agency_id for o in opts]},
            model_prediction_id=c.prediction[0] if c.prediction else None,
            model_prediction_as_of=c.prediction[1] if c.prediction else None))
    warnings = [w for v in sorted(model_versions) if (w := artifact_synthetic_warning(RECOVERY_RISK.name, v))]
    bank = db.get(Bank, bank_id)
    if bank is not None and bank.is_demo:
        warnings.append(DEMO_BOOK_NOTE)
    run.total_loans_evaluated = len(plan.candidates)
    run.total_placed, run.total_deferred = counts["PLACED"], counts["DEFERRED"]
    run.total_blocked, run.total_recalled, run.total_kept = counts["BLOCKED"], counts["RECALLED"], plan.kept
    run.expected_recovery_total = round(gross, 2)
    run.summary = {"counts": counts, "kept": plan.kept, "expected_recovery_gross": round(gross, 2),
                   "value_net_of_commission": round(net, 2), "explored": len(plan.exploration),
                   "elapsed_s": plan.elapsed_s, "time_budget_s": TIME_LIMIT_S,
                   "time_budget_note": "soft: the solver cannot be interrupted (ortools 9.11 has no time limit)",
                   "limitations": LIMITATIONS,
                   "synthetic_warning": " ".join(dict.fromkeys(warnings)) or None}
    db.flush()
    return run


# ═══ Apply: a second person turns a PLANNED run into placements ═══════════════

def apply_run(db: Session, *, bank_id: str, run_id: str, actor_id: str, today: date,
              ip_address: str | None = None) -> PlacementRun:
    """Apply a PLANNED run (ADR 0010): four-eyes (applied_by != created_by),
    only on its plan date, gates re-judged at apply time (a decision whose
    gates now fail is skipped and listed, not forced), recalls before
    placements so their slots are free, one transaction."""
    from app.core.audit import stage_audit
    from app.core.errors import AppException, ErrorCode
    from app.models.audit_log import AuditAction

    run = (db.query(PlacementRun).filter(PlacementRun.id == run_id, PlacementRun.bank_id == bank_id)
           .with_for_update().first())
    if run is None:
        raise AppException(404, ErrorCode.NOT_FOUND, "Not found")
    if run.simulate or run.status != "PLANNED":
        raise AppException(409, ErrorCode.CONFLICT, f"Only a PLANNED run can be applied; this one is {run.status}")
    if run.created_by is None or run.created_by == actor_id:
        raise AppException(409, ErrorCode.CONFLICT,
                           "A placement run must be applied by a different person from the one who planned it")
    if run.plan_date != today:
        raise AppException(409, ErrorCode.CONFLICT,
                           f"This run was planned for {run.plan_date}; plan again for today ({today})")

    rules = PlacementService(db)
    decisions = (db.query(PlacementDecision).filter(PlacementDecision.run_id == run.id)
                 .order_by(PlacementDecision.loan_id).all())
    placed, recalled, skipped = [], [], []
    try:
        for d in decisions:
            if d.outcome != "RECALLED":
                continue
            p = (db.query(Placement).filter(Placement.loan_id == d.loan_id, Placement.bank_id == bank_id,
                                            Placement.status == "ACTIVE").with_for_update().first())
            if p is None or p.agency_id != d.previous_agency_id:
                skipped.append({"loan_id": d.loan_id, "step": "recall", "why": "no longer placed with that agency"})
                continue
            end_reason = (d.score_breakdown.get("recall_rules") or [NO_ACTIVITY])[0]
            closed = rules.recall(p, on=today, end_reason=end_reason, note=d.reason, ended_by=actor_id)
            stage_audit(db, action=AuditAction.PLACEMENT_RECALLED, user_id=actor_id, entity_type="Placement",
                        entity_id=p.id, bank_id=p.bank_id, agency_id=p.agency_id, ip_address=ip_address,
                        details={"source": "ENGINE", "run_id": run.id, "rule": end_reason,
                                 "agency_id": p.agency_id, "loan_id": p.loan_id,
                                 "cases_closed": [c.id for c in closed]})
            recalled.append(p.id)
        for d in decisions:
            if d.chosen_agency_id is None:
                continue
            loan = db.get(Loan, d.loan_id)
            res = rules.evaluate(loan, d.chosen_agency_id, today, lock_contract=True)
            if not res.ok or res.existing is not None:
                why = "already placed with this agency" if res.ok else f"{res.first_failure.reason}: {res.first_failure.detail}"
                skipped.append({"loan_id": d.loan_id, "step": "place", "why": why})
                continue
            source = "RE_PLACEMENT" if d.outcome == "RECALLED" else "ENGINE"
            p = rules.place_new_loan(loan, agency_id=d.chosen_agency_id, on=today, source=source,
                                     placed_by=actor_id, placement_run_id=run.id)
            rules.attach_expectation(p, loan)
            case = rules.open_case(p, loan, case_number=rules.case_number_for(p),
                                   target_amount=rules.case_target_amount(loan))
            stage_audit(db, action=AuditAction.PLACEMENT_CREATED, user_id=actor_id, entity_type="Placement",
                        entity_id=p.id, bank_id=p.bank_id, agency_id=p.agency_id, ip_address=ip_address,
                        details={"source": source, "run_id": run.id, "loan_id": loan.id,
                                 "agency_id": d.chosen_agency_id, "case_id": case.id})
            placed.append(p.id)
        run.status = "APPLIED"
        run.applied_by = actor_id
        run.applied_at = datetime.now(timezone.utc)
        run.summary = {**(run.summary or {}),
                       "apply": {"placed": len(placed), "recalled": len(recalled), "skipped": skipped,
                                 "skipped_total": len(skipped)}}
        db.commit()
    except Exception:
        db.rollback()
        raise
    return run


# ═══ The use case the API calls, and the reads behind the Engine tab ══════════

EFFECT_MONTHS = 3


def plan_run(db: Session, adb: Session | None, *, bank_id: str, actor_id: str, today: date, simulate: bool,
             exploration_rate: float) -> PlacementRun:
    """Plan (or simulate) today's placement run and record it; one commit.
    `adb` is the tenant-bound analytics session the agency effect reads; with
    none, every agency scores neutral and the summary says so."""
    from app.services.bank.agency_effect import agency_effect
    rows, effect_error = [], None
    if adb is not None:
        try:
            rows = agency_effect(adb, bank_id=bank_id, months=EFFECT_MONTHS, pooled=True)
        except Exception as exc:  # noqa: BLE001 — the scorecard is an input, not a gate; say it was missing
            adb.rollback()
            effect_error = type(exc).__name__
    plan = build_plan(db, bank_id, today, effects=effects_map(rows), exploration_rate=exploration_rate)
    try:
        run = record_run(db, plan, bank_id=bank_id, plan_date=today, simulate=simulate,
                         exploration_rate=exploration_rate, created_by=actor_id,
                         effect_window={"months": EFFECT_MONTHS, "cells": len(rows),
                                        "latest_month": rows[0]["month_start"] if rows else None})
        if not rows:
            run.summary = {**run.summary, "effect_note": (
                "No agency history in the scorecard: every agency scored neutral (multiplier 1.0)."
                + (f" The scorecard could not be read ({effect_error})." if effect_error else ""))}
        db.commit()
    except Exception:
        db.rollback()
        raise
    return run


def run_out(run: PlacementRun) -> dict:
    return {
        "run_id": run.id, "plan_date": run.plan_date.isoformat(), "status": run.status, "simulate": run.simulate,
        "strategy": run.strategy, "exploration_rate": run.exploration_rate, "seed": run.seed,
        "created_by": run.created_by, "applied_by": run.applied_by,
        "applied_at": run.applied_at.isoformat() if run.applied_at else None,
        "totals": {"evaluated": run.total_loans_evaluated, "placed": run.total_placed, "kept": run.total_kept,
                   "blocked": run.total_blocked, "deferred": run.total_deferred, "recalled": run.total_recalled},
        "expected_recovery_total": run.expected_recovery_total, "summary": run.summary or {},
    }


def agency_run_out(run: PlacementRun) -> dict:
    """What an agency sees of a run: when it was applied, nothing bank-wide."""
    return {"run_id": run.id, "plan_date": run.plan_date.isoformat(), "status": run.status,
            "applied_at": run.applied_at.isoformat() if run.applied_at else None}


def list_runs(db: Session, bank_id: str, *, agency_id: str | None = None, limit: int = 30) -> list[dict]:
    """The bank's runs; for an agency (agency_id set), only APPLIED runs that
    placed something with it, without bank-wide figures."""
    q = db.query(PlacementRun).filter(PlacementRun.bank_id == bank_id)
    if agency_id is not None:
        mine = (db.query(PlacementDecision.id)
                .filter(PlacementDecision.run_id == PlacementRun.id, PlacementDecision.chosen_agency_id == agency_id)
                .exists())
        q = q.filter(PlacementRun.status == "APPLIED", mine)
    rows = q.order_by(PlacementRun.created_at.desc()).limit(max(1, min(limit, 100))).all()
    return [agency_run_out(r) if agency_id is not None else run_out(r) for r in rows]


def run_decisions(db: Session, *, bank_id: str, run_id: str, outcome: str | None, page: int, page_size: int,
                  agency_id: str | None = None) -> dict:
    """A run's decisions. For an agency (agency_id set): only an APPLIED run,
    only the loans it was given, and nothing about other agencies (their
    names, refusals and the agency a loan was recalled from are withheld)."""
    from app.core.errors import AppException, ErrorCode
    from app.models.tenancy import Agency
    run = db.query(PlacementRun).filter(PlacementRun.id == run_id, PlacementRun.bank_id == bank_id).first()
    if run is None or (agency_id is not None and run.status != "APPLIED"):
        raise AppException(404, ErrorCode.NOT_FOUND, "Not found")
    q = (db.query(PlacementDecision, Loan.loan_account_number)
         .join(Loan, and_(Loan.id == PlacementDecision.loan_id, Loan.bank_id == PlacementDecision.bank_id))
         .filter(PlacementDecision.run_id == run.id, PlacementDecision.bank_id == bank_id))
    if agency_id is not None:
        q = q.filter(PlacementDecision.chosen_agency_id == agency_id)
    if outcome:
        q = q.filter(PlacementDecision.outcome == outcome)
    total = q.count()
    rows = (q.order_by(PlacementDecision.score.desc().nullslast(), Loan.loan_account_number)
            .offset((page - 1) * page_size).limit(page_size).all())
    names = {a.id: (a.trade_name or a.legal_name) for a in db.query(Agency).filter(Agency.bank_id == bank_id)}
    items = [{
        "loan_id": d.loan_id, "loan_account_number": lan, "outcome": d.outcome, "reason": d.reason,
        "score": d.score, "chosen_agency_id": d.chosen_agency_id,
        "chosen_agency_name": names.get(d.chosen_agency_id), "previous_agency_id": d.previous_agency_id,
        "previous_agency_name": names.get(d.previous_agency_id), "score_breakdown": d.score_breakdown,
        "gate_results": {**d.gate_results,
                         "refused": {names.get(a, a): r for a, r in (d.gate_results.get("refused") or {}).items()}},
    } for d, lan in rows]
    if agency_id is not None:
        for it in items:
            it["previous_agency_id"] = it["previous_agency_name"] = None
            it["gate_results"] = {}
            it["reason"] = "placed with your agency" if it["outcome"] == "PLACED" else "re-placed with your agency"
        return {"run": agency_run_out(run), "items": items, "total": total, "page": page, "page_size": page_size}
    return {"run": run_out(run), "items": items, "total": total, "page": page, "page_size": page_size}
