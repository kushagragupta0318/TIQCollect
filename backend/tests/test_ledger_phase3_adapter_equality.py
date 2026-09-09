"""Phase 3: the production adapter, run against a rewound database, must agree
with the independently derived panel.

THIS IS THE TEST THE WHOLE REWRITE EXISTS FOR. `panel.py` aggregates events with
pandas. `services/ml_scoring_service.py` issues ORM queries against mutable
current-state columns. Two implementations of one definition, and until now
nothing could compare them — which is how `ptp_kept_ratio` went missing from
every served vector for a full cycle while coverage stayed above its floor.

NEITHER SIDE MAY BE BENT TO FIT THE OTHER. Where they disagree the fix belongs
in whichever one is wrong, and the disagreement is the finding.

Tolerances: exact for counts and identities, 0.01 absolute on money (both sides
round to paise), 0.002 on ratios the two sides round at different points.
Nothing wider, and every one of them is a rounding artefact rather than a
definitional gap.
"""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ml.simulation.ledger import LedgerConfig, LedgerSimulator
from app.ml.simulation.ledger.materialise import Materialiser
from app.ml.simulation.ledger.panel import build_panel
from app.models.base import Base
from app.models.loan import Loan
from app.services.ml_scoring_service import MLScoringService

CFG = LedgerConfig(n_borrowers=300, months=12, seed=17)
#: Snapshot days to compare. Enough (loan, as_of) pairs to clear the 1,000
#: required, and spread across the book so seasoning, drift and lifecycle
#: turnover are all represented.
DAYS = [90, 150, 210, 270, 300]

#: Exact equality. Counts, identities and anything integral.
EXACT = ["dpd", "dpd_bucket", "loan_type", "branch_code", "city",
         "employment_type", "is_secured", "tenure_months", "months_on_book",
         "age", "visits_3m", "visits_6m", "distinct_agents_6m",
         "ptp_set_6m", "ptp_kept_6m", "days_since_last_contact",
         "days_since_last_payment", "cibil_score"]
#: Money. Both sides round to paise; 0.01 is the rounding, not a fudge.
MONEY = ["emi_amount", "sanction_amount", "overdue_amount", "penal_charges",
         "outstanding_principal", "total_outstanding", "interest_rate"]
#: Ratios the two sides round at different points in the arithmetic.
RATIO = ["arrears_ratio", "penal_ratio", "outstanding_to_sanction",
         "contact_rate_6m", "ptp_kept_ratio",
         "paid_ratio_3m", "paid_ratio_6m", "paid_ratio_12m"]

MONEY_TOL = 0.01
RATIO_TOL = 0.002

engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="module")
def world():
    Base.metadata.create_all(bind=engine)
    ledger = LedgerSimulator(CFG).run(intercept=-4.1562)
    panel = build_panel(ledger, CFG)
    db = Session()
    mat = Materialiser(ledger, CFG)
    mat.load(db)
    try:
        yield ledger, panel, db, mat
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def _adapter_rows(db, mat, panel, day):
    """Rewind, then score every loan the panel has a row for on this day."""
    mat.rewind_to(db, day)
    as_of = datetime.combine(CFG.start_date + timedelta(days=day), time(23, 59),
                             tzinfo=timezone.utc)
    want = panel[panel.month_index == day // CFG.cycle_days]
    svc = MLScoringService(db)
    out = {}
    for lid in want.loan_id:
        loan = db.query(Loan).filter(Loan.id == lid).first()
        if loan is not None:
            out[lid] = svc.build_features(loan, as_of=as_of)
    return want.set_index("loan_id"), out


@pytest.fixture(scope="module")
def matched(world):
    """Every (loan, as_of) pair, panel row beside adapter vector."""
    _, panel, db, mat = world
    pairs = []
    for day in DAYS:
        want, got = _adapter_rows(db, mat, panel, day)
        for lid, feats in got.items():
            pairs.append((day, lid, want.loc[lid], feats))
    return pairs


# ---------------------------------------------------------------------------
# Scale
# ---------------------------------------------------------------------------

def test_at_least_a_thousand_matched_pairs(matched):
    assert len(matched) >= 1000, len(matched)


def test_the_adapter_answers_for_every_panel_row(world):
    """Coverage, not just agreement. A silent drop would look like a pass."""
    _, panel, db, mat = world
    for day in DAYS:
        want, got = _adapter_rows(db, mat, panel, day)
        assert set(got) == set(want.index), (
            f"day {day}: adapter answered {len(got)} of {len(want)}")


# ---------------------------------------------------------------------------
# Feature-by-feature equality
# ---------------------------------------------------------------------------

def _mismatches(matched, names, tol):
    bad = []
    for day, lid, row, feats in matched:
        for f in names:
            a, b = row.get(f), feats.get(f)
            if a is None and b is None:
                continue
            a_nan = a is None or (isinstance(a, float) and math.isnan(a))
            b_nan = b is None or (isinstance(b, float) and math.isnan(b))
            if a_nan and b_nan:
                continue
            if a_nan != b_nan:
                bad.append((day, lid, f, a, b))
                continue
            if isinstance(a, str) or isinstance(b, str):
                if str(a) != str(b):
                    bad.append((day, lid, f, a, b))
            elif abs(float(a) - float(b)) > tol:
                bad.append((day, lid, f, a, b))
    return bad


@pytest.mark.parametrize("feature", EXACT)
def test_exact_features_agree(matched, feature):
    bad = _mismatches(matched, [feature], 0.0)
    assert not bad, f"{len(bad)} mismatches, first 3: {bad[:3]}"


@pytest.mark.parametrize("feature", MONEY)
def test_money_features_agree_to_the_paisa(matched, feature):
    bad = _mismatches(matched, [feature], MONEY_TOL)
    assert not bad, f"{len(bad)} mismatches, first 3: {bad[:3]}"


@pytest.mark.parametrize("feature", RATIO)
def test_ratio_features_agree(matched, feature):
    bad = _mismatches(matched, [feature], RATIO_TOL)
    assert not bad, f"{len(bad)} mismatches, first 3: {bad[:3]}"


def test_every_champion_feature_is_covered_by_one_of_the_groups():
    """A feature the model actually uses must not slip out of the comparison."""
    from app.ml.pipeline.config import RECOVERY_RISK

    covered = set(EXACT) | set(MONEY) | set(RATIO)
    missing = [f for f in RECOVERY_RISK.selected_or_all()
               if f not in covered] if hasattr(RECOVERY_RISK, "selected_or_all") \
        else [f for f in ("dpd", "cibil_score", "ptp_kept_ratio",
                          "overdue_amount", "arrears_ratio", "paid_ratio_3m",
                          "contact_rate_6m", "visits_3m") if f not in covered]
    assert missing == []


# ---------------------------------------------------------------------------
# Point-in-time: future events cannot reach a feature at as_of
# ---------------------------------------------------------------------------

def test_rewinding_backwards_undoes_every_future_event(world):
    """The decisive PIT test. Score a loan at day 90, advance the database to
    day 270, rewind to 90 again — the vector must be identical. If any future
    event had leaked into current state, it would not be."""
    _, panel, db, mat = world
    want, first = _adapter_rows(db, mat, panel, 90)
    _adapter_rows(db, mat, panel, 270)
    _, again = _adapter_rows(db, mat, panel, 90)

    assert set(first) == set(again)
    for lid, a in first.items():
        b = again[lid]
        assert set(a) == set(b), lid
        for k, v in a.items():
            if isinstance(v, float) and math.isnan(v):
                assert math.isnan(b[k]), (lid, k)
            else:
                assert v == b[k], (lid, k, v, b[k])


def test_a_payment_after_as_of_does_not_move_the_features(world):
    """Directly: find a loan whose next payment lands after day 150, confirm the
    adapter at 150 knows nothing about it, and that it DOES show up later."""
    ledger, panel, db, mat = world
    pays = ledger.payments
    later = pays[(pays.payment_day > 150) & (pays.payment_day <= 210) &
                 (pays.final_status == "VERIFIED")]
    assert len(later), "fixture produced no post-150 payments to test with"
    lid = later.iloc[0].loan_id

    from datetime import date as _date

    pay_day = int(later.iloc[0].payment_day)
    pay_date = CFG.start_date + timedelta(days=pay_day)

    mat.rewind_to(db, 150)
    at150 = db.query(Loan).filter(Loan.id == lid).first()
    # The sharpest statement of the property: at as_of the ledger cannot name a
    # payment that has not happened yet.
    if at150.last_payment_date:
        assert _date.fromisoformat(at150.last_payment_date) < pay_date

    mat.rewind_to(db, 210)
    at210 = db.query(Loan).filter(Loan.id == lid).first()
    assert at210.last_payment_date is not None
    assert _date.fromisoformat(at210.last_payment_date) >= pay_date


def test_a_promise_resolved_after_as_of_still_reads_as_active(world):
    """`PTP.status` is the single column the live schema cannot rewind, and the
    reason `ptp_kept_ratio` is not historically reconstructable there."""
    from app.models.ptp import PTP, PTPStatus

    ledger, _, db, mat = world
    ptps = ledger.ptps
    late = ptps[(ptps.created_day < 150) & (ptps.resolved_day > 150)]
    if not len(late):
        pytest.skip("no promise straddles day 150 in this book")
    pid = late.iloc[0].ptp_id

    mat.rewind_to(db, 150)
    assert db.query(PTP).filter(PTP.id == pid).first().status is PTPStatus.ACTIVE
    mat.rewind_to(db, 270)
    assert db.query(PTP).filter(PTP.id == pid).first().status is not PTPStatus.ACTIVE


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_a_reversal_is_invisible_before_it_takes_effect(world):
    """A payment verified on day 10 and reversed on day 25 counted on day 20.
    The live schema stores only the current status, so it cannot say that."""
    from app.models.payment import Payment, PaymentStatus

    ledger, _, db, mat = world
    pays = ledger.payments
    rev = pays[(pays.final_status == "REVERSED") &
               (pays.status_effective_day > pays.payment_day)]
    if not len(rev):
        pytest.skip("no reversal in this book")
    r = rev.iloc[0]

    mat.rewind_to(db, int(r.payment_day) + 1)
    assert db.query(Payment).filter(
        Payment.id == r.payment_id).first().status is PaymentStatus.VERIFIED
    mat.rewind_to(db, int(r.status_effective_day) + 1)
    assert db.query(Payment).filter(
        Payment.id == r.payment_id).first().status is PaymentStatus.REVERSED


def test_opening_balances_reach_the_materialised_state(world):
    """Seasoned accounts opened mid-life. Without `opening_paid` every one reads
    as never having paid — measured once at 77.5% of account-months in NPA."""
    ledger, panel, db, mat = world
    mat.rewind_to(db, 90)
    rows = panel[panel.month_index == 3].set_index("loan_id")
    seasoned = [lid for lid in rows.index
                if ledger.loans.set_index("loan_id").loc[lid, "opening_paid"] > 0]
    assert seasoned, "no seasoned accounts in this book"
    for lid in seasoned[:20]:
        loan = db.query(Loan).filter(Loan.id == lid).first()
        assert abs(loan.overdue_amount - rows.loc[lid, "overdue_amount"]) <= MONEY_TOL


@pytest.mark.parametrize("day", DAYS)
def test_dpd_transitions_agree_at_every_snapshot(world, day):
    ledger, panel, db, mat = world
    mat.rewind_to(db, day)
    rows = panel[panel.month_index == day // CFG.cycle_days].set_index("loan_id")
    for lid in rows.index:
        loan = db.query(Loan).filter(Loan.id == lid).first()
        assert loan.dpd == rows.loc[lid, "dpd"], (day, lid)


def test_missing_values_are_missing_on_both_sides(matched):
    """A borrower who has never paid has no `days_since_last_payment`. NaN on
    one side and 0.0 on the other is a silent disagreement that would read as a
    perfectly ordinary feature value."""
    seen = 0
    for _, lid, row, feats in matched:
        a = row.get("days_since_last_payment")
        if a is None or (isinstance(a, float) and math.isnan(a)):
            seen += 1
            assert "days_since_last_payment" not in feats or \
                feats["days_since_last_payment"] is None, (lid, feats.get(
                    "days_since_last_payment"))
    assert seen > 0, "no never-paid accounts in the sample to check"
