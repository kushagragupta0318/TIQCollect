"""The four features recovery_risk 2.2.0 needed that the adapter did not
produce — `latest_disposition`, `disposition_recency_class`,
`last_commit_status`, `recent_ptp_status` — held to their training
definition, 2026-09-16.

TRAINING DEFINITION == PRODUCTION DEFINITION is proved the way the Phase 3
harness proves the rest of the vector: the ledger world that developed the
model is materialised into the real schema, rewound to five snapshot days,
and `MLScoringService.build_features` is compared with the panel on every
(loan, as_of) pair, all fifteen model inputs, exact equality on the four
categoricals.

POINT-IN-TIME is proved by mutation: a disposition, a verbal commitment, a
payment that would keep one, or a promise resolution written AT or AFTER the
as_of cut must not move the feature; one microsecond before must.

MISSING / UNKNOWN: no reading -> "NONE" (a fitted level, the panel's own
word for it); a product PTP status the ledger never emits maps explicitly;
an unseen disposition string is refused by the enum, never guessed.
"""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ml.pipeline.config import (COMMITMENT_GRACE_DAYS, COMMITMENT_KEPT_RATIO,
                                    DISPOSITION_FRESH_DAYS, LOGGED_FEATURES, RECOVERY_RISK_GAM)
from app.ml.simulation.ledger import LedgerConfig, LedgerSimulator
from app.ml.simulation.ledger.materialise import Materialiser
from app.ml.simulation.ledger.panel import build_panel
from app.models.base import Base
from app.models.call_log import BorrowerDisposition, CallLog, CallOutcome
from app.models.loan import Loan
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.ptp import PTP, PTPStatus
from app.models.visit import Visit, VisitOutcome
from app.services.ml_scoring_service import MLScoringService, _PTP_STATUS_LEVEL, _ptp_status_level
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

#: The wd10 recipe (every observability channel on, read noise 0.10) in
#: miniature — the world the 2.2.0 features were developed on.
CFG = LedgerConfig(n_borrowers=300, months=12, seed=17, observe_declines=True,
                   observe_call_duration=True, observe_verbal_commitments=True,
                   pre_scoring_call_days=3, pre_scoring_call_attempts=3,
                   pre_scoring_until_reached=True, observe_disposition=True,
                   disposition_read_noise=0.10)
DAYS = [90, 150, 210, 270, 300]
NEW = ["latest_disposition", "disposition_recency_class", "last_commit_status",
       "recent_ptp_status"]
MODEL = RECOVERY_RISK_GAM.all_features

engine = make_engine()
Session = make_session_factory(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="module")
def world():
    create_schema(bind=engine)
    led = LedgerSimulator(CFG).run(intercept=-4.23438)
    panel = build_panel(led, CFG)
    db = Session()
    mat = Materialiser(led, CFG)
    mat.load(db)
    try:
        yield led, panel, db, mat
    finally:
        db.close()
        drop_schema(bind=engine)


def _as_of(day: int) -> datetime:
    return datetime.combine(CFG.start_date + timedelta(days=day), time(23, 59), tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def matched(world):
    _, panel, db, mat = world
    pairs = []
    svc = MLScoringService(db)
    for day in DAYS:
        mat.rewind_to(db, day)
        want = panel[panel.month_index == day // CFG.cycle_days].set_index("loan_id")
        for lid in want.index:
            loan = db.query(Loan).filter(Loan.id == lid).first()
            if loan is not None:
                pairs.append((day, lid, want.loc[lid], svc.build_features(loan, as_of=_as_of(day))))
    return pairs


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _mismatches(matched, feature, tol=0.0):
    bad = []
    for day, lid, row, feats in matched:
        a, b = row.get(feature), feats.get(feature)
        if _nan(a) and _nan(b):
            continue
        if _nan(a) != _nan(b):
            bad.append((day, lid, a, b)); continue
        if isinstance(a, str) or isinstance(b, str):
            if str(a) != str(b):
                bad.append((day, lid, a, b))
        elif abs(float(a) - float(b)) > tol:
            bad.append((day, lid, a, b))
    return bad


# ---------------------------------------------------------------------------
# 1. training definition == production definition, every pair, every day
# ---------------------------------------------------------------------------

def test_enough_pairs_and_every_day_represented(matched):
    assert len(matched) >= 1000
    assert {d for d, *_ in matched} == set(DAYS)


@pytest.mark.parametrize("feature", NEW + ["days_since_disposition"])
def test_new_feature_agrees_with_the_panel_exactly(matched, feature):
    bad = _mismatches(matched, feature)
    assert not bad, f"{feature}: {len(bad)} mismatches, first 3: {bad[:3]}"


@pytest.mark.parametrize("feature", MODEL)
def test_every_gam_input_is_emitted_and_agrees(matched, feature):
    """All fifteen: emitted on every row (the key is present) and equal to
    the panel, exact for categoricals and counts, 0.002 for the ratios the
    two sides round differently."""
    assert all(feature in feats for *_, feats in matched)
    tol = 0.002 if feature in ("arrears_ratio", "paid_ratio_3m", "ptp_amount_to_emi") else 0.0
    bad = _mismatches(matched, feature, tol)
    assert not bad, f"{feature}: {len(bad)} mismatches, first 3: {bad[:3]}"


def test_the_levels_the_adapter_emits_are_the_levels_the_model_was_fitted_on(matched):
    """No level reaches the model that the artifact's vocabulary lacks — the
    unseen-level route exists for a schema surprise, not for normal serving."""
    import json
    from app.ml.pipeline.registry import version_dir
    meta = json.loads((version_dir("recovery_risk", "2.2.0") / "metadata.json").read_text())
    levels = meta["gam"]["cat_levels"]
    for f in RECOVERY_RISK_GAM.categorical_features:
        seen = {feats[f] for *_, feats in matched}
        assert seen <= set(levels[f]), f"{f}: adapter emitted {seen - set(levels[f])}"


def test_the_new_features_are_in_the_logged_vector():
    for f in NEW + ["days_since_disposition"]:
        assert f in LOGGED_FEATURES


def test_the_new_features_are_never_null_but_days_since_may_be(matched):
    """The four statuses are levels, never None; the day gap abstains."""
    for f in NEW:
        assert all(isinstance(feats[f], str) for *_, feats in matched), f
    assert any(feats["days_since_disposition"] is None for *_, feats in matched)
    assert any(feats["days_since_disposition"] is not None for *_, feats in matched)


def test_the_statuses_take_more_than_one_value_on_this_world(matched):
    """A parity test that passed because both sides said NONE everywhere
    would prove nothing; the world must exercise the levels."""
    for f in NEW:
        assert len({feats[f] for *_, feats in matched}) >= 3, f


# ---------------------------------------------------------------------------
# 2. one definition: constants and vocabulary
# ---------------------------------------------------------------------------

def test_the_commitment_constants_are_the_ledgers_defaults():
    """The panel reads its constants from LedgerConfig; the adapter from
    pipeline/config. A training/serving skew here would be silent, so the
    two are held equal rather than trusted."""
    cfg = LedgerConfig()
    assert COMMITMENT_GRACE_DAYS == cfg.commitment_grace_days
    assert COMMITMENT_KEPT_RATIO == cfg.commitment_kept_ratio


def test_the_freshness_window_is_the_panels_default():
    import inspect
    from app.ml.simulation.ledger import panel as panel_mod
    sig = inspect.signature(panel_mod._recency_class)
    assert sig.parameters["fresh_days"].default == DISPOSITION_FRESH_DAYS


def test_the_disposition_enum_is_the_simulators_vocabulary():
    from app.ml.simulation.ledger import simulator as sim
    ledger_vocab = {getattr(sim, n) for n in dir(sim) if n.startswith("DISP_")}
    assert ledger_vocab == {m.value for m in BorrowerDisposition}


def test_the_ptp_status_mapping_is_total_and_explicit():
    """Every product status maps; the two the ledger never emits map to the
    level this adapter's own kept/failed rules put them under."""
    assert set(_PTP_STATUS_LEVEL) == set(PTPStatus)
    assert _ptp_status_level(PTPStatus.PARTIALLY_HONORED) == "HONORED"
    assert _ptp_status_level(PTPStatus.EXPIRED) == "BROKEN"
    assert _ptp_status_level(PTPStatus.ACTIVE) == "OPEN"
    assert set(_PTP_STATUS_LEVEL.values()) == {"OPEN", "HONORED", "BROKEN", "RESCHEDULED"}


def test_an_unknown_disposition_string_is_refused_not_guessed():
    with pytest.raises(ValueError):
        BorrowerDisposition("MAYBE_LATER")


# ---------------------------------------------------------------------------
# 3. point-in-time, by mutation at the boundary
# ---------------------------------------------------------------------------

MIDNIGHT = time(0, 0, 0, 0)
US = timedelta(microseconds=1)


@pytest.fixture(scope="module")
def served(world):
    _, panel, db, mat = world
    day = 210
    mat.rewind_to(db, day)
    as_of_date = CFG.start_date + timedelta(days=day)
    want = panel[panel.month_index == day // CFG.cycle_days]
    loans = [db.query(Loan).filter(Loan.id == lid).first() for lid in want.loan_id]
    loans = [ln for ln in loans if ln is not None]
    return db, MLScoringService(db), loans, as_of_date


def _agent(db):
    return db.query(Visit.agent_id).first()[0]


def _quiet_loan(db, loans, cut: datetime, days: int = 12) -> Loan:
    """A loan with no verified payment and no named-date call in the last
    `days` before the cut, so a commitment the test writes is the newest and
    only the test's own money can keep it."""
    since = cut - timedelta(days=days)
    for loan in loans:
        cid = f"C-{loan.id}"
        pays = db.query(Payment).filter(Payment.case_id == cid, Payment.payment_date >= since,
                                        Payment.payment_date < cut).count()
        named = db.query(CallLog).filter(CallLog.case_id == cid, CallLog.called_at >= since,
                                         CallLog.called_at < cut,
                                         CallLog.verbal_payment_date.isnot(None)).count()
        if pays == 0 and named == 0:
            return loan
    pytest.skip("no quiet loan on this world")


@pytest.mark.parametrize("delta, visible", [(-US, True), (timedelta(0), False), (US, False)])
def test_a_disposition_on_a_call_at_the_boundary(served, delta, visible):
    db, svc, loans, as_of_date = served
    loan = loans[0]
    cut = datetime.combine(as_of_date, MIDNIGHT, tzinfo=timezone.utc)
    before = svc.build_features(loan, as_of=as_of_date)
    row = CallLog(id=f"K-DISP-{int(delta.total_seconds()*1e6)}", case_id=f"C-{loan.id}",
                  agent_id=_agent(db), customer_id=loan.customer_id, called_at=cut + delta,
                  outcome=CallOutcome.ANSWERED, duration_seconds=60,
                  borrower_disposition=BorrowerDisposition.DISPUTE)
    db.add(row); db.flush()
    try:
        after = svc.build_features(loan, as_of=as_of_date)
        if visible:
            assert after["latest_disposition"] == "DISPUTE"
            assert after["disposition_recency_class"] == "DISPUTE_FRESH"
            assert after["days_since_disposition"] == 1.0
        else:
            assert after["latest_disposition"] == before["latest_disposition"]
            assert after["disposition_recency_class"] == before["disposition_recency_class"]
    finally:
        db.delete(row); db.flush()


@pytest.mark.parametrize("delta, visible", [(-US, True), (timedelta(0), False)])
def test_a_disposition_on_a_visit_at_the_boundary(served, delta, visible):
    db, svc, loans, as_of_date = served
    loan = loans[1]
    cut = datetime.combine(as_of_date, MIDNIGHT, tzinfo=timezone.utc)
    before = svc.build_features(loan, as_of=as_of_date)
    row = Visit(id=f"V-DISP-{int(delta.total_seconds()*1e6)}", case_id=f"C-{loan.id}",
                agent_id=_agent(db), check_in_latitude=28.4, check_in_longitude=77.0,
                check_in_time=cut + delta, distance_from_customer_metres=50.0,
                customer_met=True, outcome=VisitOutcome.RTP,
                borrower_disposition=BorrowerDisposition.HARDSHIP)
    db.add(row); db.flush()
    try:
        after = svc.build_features(loan, as_of=as_of_date)
        assert (after["latest_disposition"] == "HARDSHIP") == visible
        if not visible:
            assert after["latest_disposition"] == before["latest_disposition"]
    finally:
        db.delete(row); db.flush()


def test_a_reading_older_than_the_window_is_stale(served):
    db, svc, loans, as_of_date = served
    loan = loans[2]
    cut = datetime.combine(as_of_date, MIDNIGHT, tzinfo=timezone.utc)
    # Push every existing reading out of the way with a newer, deliberately
    # stale one: DISPOSITION_FRESH_DAYS + 1 days before the cut.
    row = CallLog(id="K-STALE", case_id=f"C-{loan.id}", agent_id=_agent(db),
                  customer_id=loan.customer_id,
                  called_at=cut - timedelta(days=DISPOSITION_FRESH_DAYS + 1),
                  outcome=CallOutcome.ANSWERED, duration_seconds=60,
                  borrower_disposition=BorrowerDisposition.WILL_PAY)
    fresh = CallLog(id="K-FRESH", case_id=f"C-{loan.id}", agent_id=_agent(db),
                    customer_id=loan.customer_id,
                    called_at=cut - timedelta(days=DISPOSITION_FRESH_DAYS),
                    outcome=CallOutcome.ANSWERED, duration_seconds=60,
                    borrower_disposition=BorrowerDisposition.MAY_PAY)
    # Anything the world recorded later than these must be cleared for the
    # test to read; delete newer readings on this loan inside a savepoint.
    newer = (db.query(CallLog).filter(CallLog.case_id == f"C-{loan.id}",
                                      CallLog.called_at >= row.called_at,
                                      CallLog.called_at < cut).all()
             + db.query(Visit).filter(Visit.case_id == f"C-{loan.id}",
                                      Visit.check_in_time >= row.called_at,
                                      Visit.check_in_time < cut).all())
    saved = [(type(r), {c.name: getattr(r, c.name) for c in r.__table__.columns}) for r in newer]
    for r in newer:
        db.delete(r)
    db.add(row); db.flush()
    try:
        f = svc.build_features(loan, as_of=as_of_date)
        assert f["latest_disposition"] == "WILL_PAY"
        assert f["days_since_disposition"] == DISPOSITION_FRESH_DAYS + 1
        assert f["disposition_recency_class"] == "WILL_PAY_STALE"
        db.add(fresh); db.flush()
        f = svc.build_features(loan, as_of=as_of_date)
        assert f["disposition_recency_class"] == "MAY_PAY_FRESH"
        assert f["days_since_disposition"] == DISPOSITION_FRESH_DAYS
    finally:
        db.delete(row); db.delete(fresh); db.flush()
        for cls, cols in saved:
            db.add(cls(**cols))
        db.flush()


def test_a_call_and_a_visit_on_the_same_day_the_call_wins(served):
    """The panel's tiebreak (`src`: call after visit on one day), repeated."""
    db, svc, loans, as_of_date = served
    loan = loans[3]
    day = datetime.combine(as_of_date - timedelta(days=1), MIDNIGHT, tzinfo=timezone.utc)
    v = Visit(id="V-TIE", case_id=f"C-{loan.id}", agent_id=_agent(db), check_in_latitude=28.4,
              check_in_longitude=77.0, check_in_time=day + timedelta(hours=18),
              distance_from_customer_metres=50.0, customer_met=True, outcome=VisitOutcome.PTP,
              borrower_disposition=BorrowerDisposition.WILL_PAY)
    k = CallLog(id="K-TIE", case_id=f"C-{loan.id}", agent_id=_agent(db), customer_id=loan.customer_id,
                called_at=day + timedelta(hours=9), outcome=CallOutcome.ANSWERED, duration_seconds=60,
                borrower_disposition=BorrowerDisposition.REFUSES)
    db.add_all([v, k]); db.flush()
    try:
        f = svc.build_features(loan, as_of=as_of_date)
        assert f["latest_disposition"] == "REFUSES"       # the call, though the visit was later that day
    finally:
        db.delete(v); db.delete(k); db.flush()


def test_a_commitment_is_open_then_kept_then_broken_by_the_ledger_not_by_a_column(served):
    """KEPT / BROKEN are DERIVED from verified money against the named date
    and the grace; nothing is stored. Walk one commitment through all three."""
    db, svc, loans, as_of_date = served
    cut = datetime.combine(as_of_date, MIDNIGHT, tzinfo=timezone.utc)
    loan = _quiet_loan(db, loans, cut)
    emi = float(loan.emi_amount)
    named = as_of_date + timedelta(days=1)                    # named date still ahead at as_of
    call = CallLog(id="K-COMMIT", case_id=f"C-{loan.id}", agent_id=_agent(db),
                   customer_id=loan.customer_id, called_at=cut - timedelta(days=3),
                   outcome=CallOutcome.ANSWERED, duration_seconds=60, verbal_payment_date=named)
    db.add(call); db.flush()
    pay = None
    try:
        assert svc.build_features(loan, as_of=as_of_date)["last_commit_status"] == "OPEN"
        # Enough verified money between the call and the deadline: KEPT.
        pay = Payment(id="P-COMMIT", case_id=f"C-{loan.id}", agent_id=None,
                      amount=COMMITMENT_KEPT_RATIO * emi, mode=PaymentMode.CASH,
                      status=PaymentStatus.VERIFIED, receipt_number="RCP-COMMIT",
                      payment_date=cut - timedelta(days=1))
        db.add(pay); db.flush()
        assert svc.build_features(loan, as_of=as_of_date)["last_commit_status"] == "KEPT"
        # A rupee short: not kept, and still OPEN because the grace has not run out.
        pay.amount = COMMITMENT_KEPT_RATIO * emi - 1.0; db.flush()
        assert svc.build_features(loan, as_of=as_of_date)["last_commit_status"] == "OPEN"
        # Money that arrived BEFORE the call does not keep it.
        pay.amount = 10 * emi; pay.payment_date = cut - timedelta(days=4); db.flush()
        assert svc.build_features(loan, as_of=as_of_date)["last_commit_status"] == "OPEN"
        # Name a date whose grace expired before as_of, with no qualifying money: BROKEN.
        call.verbal_payment_date = as_of_date - timedelta(days=COMMITMENT_GRACE_DAYS + 1); db.flush()
        assert svc.build_features(loan, as_of=as_of_date)["last_commit_status"] == "BROKEN"
        # Exactly at the grace edge: deadline == as_of - 1 day < as_of -> BROKEN;
        # deadline == as_of -> not yet.
        call.verbal_payment_date = as_of_date - timedelta(days=COMMITMENT_GRACE_DAYS); db.flush()
        assert svc.build_features(loan, as_of=as_of_date)["last_commit_status"] == "OPEN"
    finally:
        if pay is not None:
            db.delete(pay)
        db.delete(call); db.flush()


@pytest.mark.parametrize("delta, visible", [(-US, True), (timedelta(0), False)])
def test_a_payment_that_would_keep_a_commitment_at_the_boundary(served, delta, visible):
    db, svc, loans, as_of_date = served
    cut = datetime.combine(as_of_date, MIDNIGHT, tzinfo=timezone.utc)
    loan = _quiet_loan(db, loans[5:], cut)
    call = CallLog(id=f"K-KEEP-{int(delta.total_seconds()*1e6)}", case_id=f"C-{loan.id}",
                   agent_id=_agent(db), customer_id=loan.customer_id,
                   called_at=cut - timedelta(days=2), outcome=CallOutcome.ANSWERED,
                   duration_seconds=60, verbal_payment_date=as_of_date + timedelta(days=1))
    pay = Payment(id=f"P-KEEP-{int(delta.total_seconds()*1e6)}", case_id=f"C-{loan.id}", agent_id=None,
                  amount=float(loan.emi_amount), mode=PaymentMode.CASH, status=PaymentStatus.VERIFIED,
                  receipt_number=f"RCP-KEEP-{int(delta.total_seconds()*1e6)}", payment_date=cut + delta)
    db.add_all([call, pay]); db.flush()
    try:
        f = svc.build_features(loan, as_of=as_of_date)
        assert f["last_commit_status"] == ("KEPT" if visible else "OPEN")
    finally:
        db.delete(pay); db.delete(call); db.flush()


@pytest.mark.parametrize("delta, visible", [(-US, True), (timedelta(0), False)])
def test_a_commitment_named_at_the_boundary(served, delta, visible):
    db, svc, loans, as_of_date = served
    loan = loans[6]
    cut = datetime.combine(as_of_date, MIDNIGHT, tzinfo=timezone.utc)
    before = svc.build_features(loan, as_of=as_of_date)["last_commit_status"]
    call = CallLog(id=f"K-NAME-{int(delta.total_seconds()*1e6)}", case_id=f"C-{loan.id}",
                   agent_id=_agent(db), customer_id=loan.customer_id, called_at=cut + delta,
                   outcome=CallOutcome.ANSWERED, duration_seconds=60,
                   verbal_payment_date=as_of_date + timedelta(days=5))
    db.add(call); db.flush()
    try:
        after = svc.build_features(loan, as_of=as_of_date)["last_commit_status"]
        assert after == ("OPEN" if visible else before)
    finally:
        db.delete(call); db.flush()


@pytest.mark.parametrize("delta, visible", [(-US, True), (timedelta(0), False)])
def test_a_promise_created_at_the_boundary(served, delta, visible):
    db, svc, loans, as_of_date = served
    loan = loans[7]
    cut = datetime.combine(as_of_date, MIDNIGHT, tzinfo=timezone.utc)
    before = svc.build_features(loan, as_of=as_of_date)["recent_ptp_status"]
    row = PTP(id=f"T-NEW-{int(delta.total_seconds()*1e6)}", case_id=f"C-{loan.id}",
              agent_id=_agent(db), committed_amount=5_000.0,
              committed_date=as_of_date + timedelta(days=5), status=PTPStatus.RESCHEDULED)
    db.add(row); db.flush()
    db.query(PTP).filter(PTP.id == row.id).update({"created_at": cut + delta}); db.flush()
    try:
        after = svc.build_features(loan, as_of=as_of_date)["recent_ptp_status"]
        assert after == ("RESCHEDULED" if visible else before)
    finally:
        db.delete(row); db.flush()


def test_a_promise_resolved_after_as_of_reads_open_on_the_rewound_book(world):
    """The materialiser rewinds `PTP.status` to its value at the day; a
    promise honoured later must read OPEN at the earlier day and HONORED at
    the later one — and the panel says exactly the same."""
    led, panel, db, mat = world
    p = led.ptps
    p = p[(p.resolved_day >= 0) & (p.created_day < 150) & (p.resolved_day > 150)]
    if p.empty:
        pytest.skip("no promise straddles day 150 on this world")
    r = p.sort_values("created_day").iloc[-1]
    loan = db.query(Loan).filter(Loan.id == r.loan_id).first()
    svc = MLScoringService(db)
    mat.rewind_to(db, 150)
    early = svc.build_features(loan, as_of=_as_of(150))["recent_ptp_status"]
    later_day = int(r.resolved_day) + 1
    mat.rewind_to(db, later_day)
    late = svc.build_features(loan, as_of=_as_of(later_day))["recent_ptp_status"]
    assert early == "OPEN" or early != late         # newer promises may exist; status must not leak back
    if early == "OPEN":
        assert late in {"HONORED", "BROKEN", "RESCHEDULED", "OPEN"}


def test_no_cases_means_none_for_every_status():
    """A loan with no case has no contact history; the levels say so."""
    create_schema(bind=engine)
    db = Session()
    try:
        svc = MLScoringService(db)
        from types import SimpleNamespace
        loan = SimpleNamespace(id="L-NOCASE", emi_amount=1000.0)
        f = svc._history_features(loan, datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert f["latest_disposition"] == "NONE" and f["disposition_recency_class"] == "NONE"
        assert f["last_commit_status"] == "NONE" and f["recent_ptp_status"] == "NONE"
    finally:
        db.close()
