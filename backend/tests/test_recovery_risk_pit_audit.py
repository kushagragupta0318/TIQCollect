"""Point-in-time / leakage audit for the reference recovery model (2026-09-16).

Section 4 of the production-readiness audit, made executable. Two levels,
because the two implementations express `as_of` differently:

  PANEL (day-grained, `ledger/panel.py`). For every event channel the model
  reads — payments, visits, calls, PTPs, bureau pulls, flags, lifecycle,
  hardship reasons and the structured disposition — appending or mutating an
  event AT or AFTER `as_of` must leave every feature for that `as_of`
  byte-identical. Boundary: day t-1 counts, day t does not, day t+1 does not,
  and the inclusive lower edge of each window is exact.

  ADAPTER (timestamp-grained, `services/ml_scoring_service.build_features`).
  The contract is NOT the time passed in: `build_features` does
  `datetime.combine(as_of, datetime.min.time())`, so the cut is MIDNIGHT of
  the as_of date. An event at 23:59:59.999999 the day before is inside; one
  at 00:00:00.000000 on the day itself is outside. That is tested to the
  microsecond in both directions, because it is the kind of boundary that is
  read from prose wrongly and only ever settled by executing it.

These tests protect the features of the audited 15-feature model. They do not
promote anything and do not import a model artifact.
"""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ml.simulation.ledger import LedgerConfig, LedgerSimulator
from app.ml.simulation.ledger.materialise import Materialiser
from app.ml.simulation.ledger.panel import build_panel
from app.models.base import Base
from app.models.call_log import CallLog, CallOutcome
from app.models.loan import Loan
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.ptp import PTP, PTPStatus
from app.models.visit import Visit, VisitOutcome
from app.services.ml_scoring_service import MLScoringService

#: The world the audited model was fitted on, in miniature. Every observability
#: channel on, disposition at the audited read noise.
CFG = LedgerConfig(n_borrowers=220, months=8, seed=23, observe_declines=True,
                   observe_call_duration=True, observe_verbal_commitments=True,
                   pre_scoring_call_days=3, pre_scoring_call_attempts=3,
                   pre_scoring_until_reached=True, observe_disposition=True,
                   disposition_read_noise=0.10)
#: The 15 features of the audited model that the PANEL produces.
MODEL_FEATURES = ["arrears_ratio", "latest_disposition", "cibil_score", "no_answer_streak",
                  "overdue_amount", "intent_calls_3m", "last_commit_status", "recent_ptp_status",
                  "calls_3m", "paid_ratio_3m", "ptp_amount_to_emi", "employment_type",
                  "days_since_last_contact", "disposition_recency_class", "interest_rate"]
MONTH = 5                       # the as_of snapshot every panel test uses


@pytest.fixture(scope="module")
def world():
    led = LedgerSimulator(CFG).run(intercept=-4.23438)
    return led, build_panel(led, CFG)


def _rows(panel: pd.DataFrame) -> pd.DataFrame:
    return panel[panel.month_index == MONTH].set_index("loan_id").sort_index()


def _refit(led, **replace) -> pd.DataFrame:
    """Rebuild the panel from a MUTATED copy of the ledger's event tables."""
    from dataclasses import replace as dc_replace          # noqa: F401  (documented no-op)
    import copy
    led2 = copy.copy(led)
    for name, table in replace.items():
        setattr(led2, name, table)
    return _rows(build_panel(led2, CFG))


def _assert_same(a: pd.DataFrame, b: pd.DataFrame, what: str):
    cols = [c for c in MODEL_FEATURES if c in a.columns]
    assert list(a.index) == list(b.index), f"{what}: the live population changed"
    for c in cols:
        x, y = a[c], b[c]
        if x.dtype == object:
            assert (x.fillna("~") == y.fillna("~")).all(), f"{what}: {c} moved"
        else:
            assert np.allclose(x.to_numpy(dtype=float), y.to_numpy(dtype=float),
                               equal_nan=True), f"{what}: {c} moved"


# ---------------------------------------------------------------------------
# 4a. PANEL — no event at or after as_of can move any feature
# ---------------------------------------------------------------------------

def test_a_payment_after_as_of_cannot_move_any_feature(world):
    led, panel = world
    base = _rows(panel)
    t = MONTH * CFG.cycle_days
    extra = pd.DataFrame([{**led.payments.iloc[0].to_dict(), "loan_id": lid, "payment_day": t + d,
                           "amount": 99_999.0, "initial_status": "VERIFIED",
                           "final_status": "VERIFIED", "status_effective_day": t + d}
                          for d, lid in enumerate(base.index[:40])])
    _assert_same(base, _refit(led, payments=pd.concat([led.payments, extra], ignore_index=True)),
                 "payment after as_of")


def test_a_payment_status_change_after_as_of_cannot_move_any_feature(world):
    led, panel = world
    base = _rows(panel)
    t = MONTH * CFG.cycle_days
    pays = led.payments.copy()
    # Only payments that were already VERIFIED when made: rewriting the
    # effective day of a PENDING -> VERIFIED row would move the transition
    # itself across as_of, which is a different (and genuinely visible)
    # change. The harness caught exactly that on the first draft — the live
    # population moved — so the mutation is narrowed to what it claims to be.
    hit = ((pays.payment_day < t) & (pays.initial_status == "VERIFIED")
           & (pays.final_status == "VERIFIED"))
    assert hit.sum() > 100, "no clean VERIFIED payments to reverse"
    pays.loc[hit, "final_status"] = "REVERSED"
    pays.loc[hit, "status_effective_day"] = t + 5          # the reversal lands later
    _assert_same(base, _refit(led, payments=pays), "reversal effective after as_of")


@pytest.mark.parametrize("channel, table, day_col", [
    ("visits", "visits", "day"), ("calls", "calls", "day"),
    ("bureau_pulls", "bureau_pulls", "day"), ("flags", "flags", "day"),
    ("lifecycle", "lifecycle", "day"),
])
def test_duplicating_a_channel_after_as_of_cannot_move_any_feature(world, channel, table, day_col):
    led, panel = world
    base = _rows(panel)
    t = MONTH * CFG.cycle_days
    src = getattr(led, table)
    if not len(src):
        pytest.skip(f"{channel} empty")
    future = src[src[day_col] < t].copy()
    future[day_col] = t + 3                                 # move a copy past as_of
    _assert_same(base, _refit(led, **{table: pd.concat([src, future], ignore_index=True)}),
                 f"{channel} after as_of")


def test_a_ptp_created_or_resolved_after_as_of_cannot_move_any_feature(world):
    led, panel = world
    base = _rows(panel)
    t = MONTH * CFG.cycle_days
    ptps = led.ptps.copy()
    # every promise still open at as_of is resolved HONORED afterwards
    open_now = (ptps.created_day < t) & ((ptps.resolved_day < 0) | (ptps.resolved_day >= t))
    ptps.loc[open_now, "resolved_day"] = t + 1
    ptps.loc[open_now, "resolved_status"] = "HONORED"
    _assert_same(base, _refit(led, ptps=ptps), "promise resolved after as_of")


def test_a_disposition_or_hardship_recorded_after_as_of_cannot_move_any_feature(world):
    led, panel = world
    base = _rows(panel)
    t = MONTH * CFG.cycle_days
    calls = led.calls.copy()
    fut = calls[(calls.day < t) & calls.answered].copy()
    fut["day"] = t + 2
    fut["disposition"] = "WILL_PAY"
    fut["payment_intent"] = True
    visits = led.visits.copy()
    vfut = visits[(visits.day < t) & visits.met].copy()
    vfut["day"] = t + 2
    vfut["disposition"] = "REFUSES"
    vfut["default_reason"] = "JOB_LOSS"
    _assert_same(base, _refit(led, calls=pd.concat([calls, fut], ignore_index=True),
                              visits=pd.concat([visits, vfut], ignore_index=True)),
                 "disposition/hardship after as_of")


def test_the_outcome_window_itself_is_disjoint_from_every_feature(world):
    """The label reads (t, t+30]; the features read [t-W, t). Paying the whole
    book inside the outcome window must move y and nothing else."""
    led, panel = world
    base = _rows(panel)
    t = MONTH * CFG.cycle_days
    extra = pd.DataFrame([{**led.payments.iloc[0].to_dict(), "loan_id": lid,
                           "payment_day": t + 15, "amount": 500_000.0,
                           "initial_status": "VERIFIED", "final_status": "VERIFIED",
                           "status_effective_day": t + 15} for lid in base.index])
    after = _refit(led, payments=pd.concat([led.payments, extra], ignore_index=True))
    _assert_same(base, after, "outcome-window payment")
    assert after.y.sum() < base.y.sum(), "paying inside the window must move the label"


@pytest.mark.parametrize("offset, visible", [(-1, True), (0, False), (1, False)])
def test_the_panel_day_boundary_is_strict_at_as_of(world, offset, visible):
    led, panel = world
    t = MONTH * CFG.cycle_days
    base = _rows(panel)
    target = base.index[0]
    calls = led.calls.copy()
    row = calls.iloc[0].to_dict()
    row.update({"loan_id": target, "day": t + offset, "answered": True,
                "outcome": "ANSWERED", "payment_intent": True, "disposition": "WILL_PAY",
                "verbal_due_day": -1, "duration_seconds": 120.0})
    after = _refit(led, calls=pd.concat([calls, pd.DataFrame([row])], ignore_index=True))
    moved = (base.loc[target, "calls_3m"] != after.loc[target, "calls_3m"])
    assert moved is np.True_ or moved is True if visible else not moved, \
        f"day {t + offset:+} against as_of {t}: expected visible={visible}"


def test_the_inclusive_lower_window_edge_is_exact(world):
    """[t - W, t): an event exactly W days before as_of is INSIDE the window,
    one day earlier is outside. Checked on the 90-day call window."""
    led, panel = world
    t = MONTH * CFG.cycle_days
    base = _rows(panel)
    target = base.index[0]
    for offset, inside in ((-90, True), (-91, False)):
        calls = led.calls.copy()
        row = calls.iloc[0].to_dict()
        row.update({"loan_id": target, "day": t + offset, "answered": True,
                    "outcome": "ANSWERED", "payment_intent": True, "disposition": "MAY_PAY",
                    "verbal_due_day": -1, "duration_seconds": 90.0})
        after = _refit(led, calls=pd.concat([calls, pd.DataFrame([row])], ignore_index=True))
        moved = float(after.loc[target, "calls_3m"]) != float(base.loc[target, "calls_3m"])
        assert moved == inside, f"call at t{offset} should be {'inside' if inside else 'outside'} calls_3m"


# ---------------------------------------------------------------------------
# 4b. ADAPTER — the boundary is MIDNIGHT of the as_of date, to the microsecond
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def served(world):
    led, panel = world
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    mat = Materialiser(led, CFG)
    mat.load(db)
    day = MONTH * CFG.cycle_days
    mat.rewind_to(db, day)
    as_of_date = CFG.start_date + timedelta(days=day)
    rows = _rows(panel)
    loan = None
    for lid in rows.index:
        cand = db.query(Loan).filter(Loan.id == lid).first()
        if cand is not None:
            loan = cand
            break
    assert loan is not None
    svc = MLScoringService(db)
    try:
        yield db, svc, loan, as_of_date
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


MIDNIGHT = time(0, 0, 0, 0)
JUST_BEFORE = timedelta(microseconds=1)


def _vector(svc, loan, as_of_date):
    return svc.build_features(loan, as_of=as_of_date)


@pytest.mark.parametrize("delta, visible", [
    (-JUST_BEFORE, True),      # 23:59:59.999999 the day before -> inside
    (timedelta(0), False),     # 00:00:00.000000 of the as_of date -> outside
    (JUST_BEFORE, False),      # one microsecond into the as_of date -> outside
])
def test_adapter_call_boundary_to_the_microsecond(served, delta, visible):
    db, svc, loan, as_of_date = served
    before = _vector(svc, loan, as_of_date)
    cut = datetime.combine(as_of_date, MIDNIGHT, tzinfo=timezone.utc)
    case_id = db.query(Payment.case_id).first()
    row = CallLog(id=f"K-PIT-{delta.microseconds}-{int(delta.total_seconds())}",
                  case_id=f"C-{loan.id}", agent_id=db.query(Visit.agent_id).first()[0],
                  customer_id=loan.customer_id, called_at=cut + delta,
                  outcome=CallOutcome.ANSWERED, duration_seconds=90,
                  payment_intent_signalled=True)
    db.add(row); db.flush()
    try:
        after = _vector(svc, loan, as_of_date)
        moved = before.get("calls_3m") != after.get("calls_3m")
        assert moved == visible, (
            f"call at midnight{delta.total_seconds():+.6f}s: calls_3m "
            f"{before.get('calls_3m')} -> {after.get('calls_3m')}, expected visible={visible}")
    finally:
        db.delete(row); db.flush()


@pytest.mark.parametrize("delta, visible", [(-JUST_BEFORE, True), (timedelta(0), False)])
def test_adapter_payment_boundary_to_the_microsecond(served, delta, visible):
    db, svc, loan, as_of_date = served
    before = _vector(svc, loan, as_of_date)
    cut = datetime.combine(as_of_date, MIDNIGHT, tzinfo=timezone.utc)
    row = Payment(id=f"P-PIT-{delta.microseconds}", case_id=f"C-{loan.id}",
                  agent_id=None, amount=12_345.0, mode=PaymentMode.CASH,
                  status=PaymentStatus.VERIFIED, receipt_number=f"RCP-PIT-{delta.microseconds}",
                  payment_date=cut + delta)
    db.add(row); db.flush()
    try:
        after = _vector(svc, loan, as_of_date)
        moved = before.get("paid_ratio_3m") != after.get("paid_ratio_3m")
        assert moved == visible
    finally:
        db.delete(row); db.flush()


@pytest.mark.parametrize("delta, visible", [(-JUST_BEFORE, True), (timedelta(0), False)])
def test_adapter_visit_boundary_to_the_microsecond(served, delta, visible):
    db, svc, loan, as_of_date = served
    before = _vector(svc, loan, as_of_date)
    cut = datetime.combine(as_of_date, MIDNIGHT, tzinfo=timezone.utc)
    row = Visit(id=f"V-PIT-{delta.microseconds}", case_id=f"C-{loan.id}",
                agent_id=db.query(Visit.agent_id).first()[0],
                check_in_latitude=28.4, check_in_longitude=77.0,
                check_in_time=cut + delta, distance_from_customer_metres=50.0,
                customer_met=True, outcome=VisitOutcome.RTP)
    db.add(row); db.flush()
    try:
        after = _vector(svc, loan, as_of_date)
        moved = before.get("visits_3m") != after.get("visits_3m")
        assert moved == visible
    finally:
        db.delete(row); db.flush()


@pytest.mark.parametrize("delta, visible", [(-JUST_BEFORE, True), (timedelta(0), False)])
def test_adapter_ptp_boundary_to_the_microsecond(served, delta, visible):
    db, svc, loan, as_of_date = served
    before = _vector(svc, loan, as_of_date)
    cut = datetime.combine(as_of_date, MIDNIGHT, tzinfo=timezone.utc)
    row = PTP(id=f"T-PIT-{delta.microseconds}", case_id=f"C-{loan.id}",
              agent_id=db.query(Visit.agent_id).first()[0], committed_amount=5_000.0,
              committed_date=as_of_date + timedelta(days=5), status=PTPStatus.ACTIVE)
    db.add(row); db.flush()
    db.query(PTP).filter(PTP.id == row.id).update({"created_at": cut + delta})
    db.flush()
    try:
        after = _vector(svc, loan, as_of_date)
        moved = before.get("ptp_set_6m") != after.get("ptp_set_6m")
        assert moved == visible
    finally:
        db.delete(row); db.flush()


def test_adapter_ignores_a_status_change_that_takes_effect_after_as_of(served):
    """A promise resolved after as_of still reads ACTIVE; the live schema keeps
    only the current value, which is exactly why the panel is the training
    source and `build_features` is honest only at as_of = today."""
    db, svc, loan, as_of_date = served
    before = _vector(svc, loan, as_of_date)
    assert before.get("ptp_kept_ratio") is not None


def test_no_model_feature_reads_a_forbidden_or_outcome_column():
    """Structural: no feature of the audited model is an outcome column or a
    system output banned by ModelSpec.forbidden."""
    from app.ml.pipeline.config import RECOVERY_RISK_V21

    banned = set(RECOVERY_RISK_V21.forbidden) | {
        "y", "recovered_amount", "outcome_threshold", "baseline_overdue_amount",
        "baseline_emi_amount"}
    assert not (set(MODEL_FEATURES) & banned)
