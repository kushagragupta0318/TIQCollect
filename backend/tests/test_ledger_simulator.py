"""Phase 1 of the event-sourced simulator: the properties the design rests on.

Four things are being protected here, and only one of them is "the numbers look
plausible":

  1. DPD and overdue are DERIVED, not stored — so they are reconstructable for
     any past date. This is the property the live database lacks and the reason
     a retrospective backtest against it is impossible.
  2. Features read only events STRICTLY BEFORE the as_of; the label reads only
     the window after it. The two are disjoint by generation order.
  3. The latents never reach the panel.
  4. A fixed seed reproduces the book exactly.

None of these tests inspects source text.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.ml.pipeline.config import RECOVERY_RISK
from app.ml.simulation.ledger import LedgerConfig, LedgerSimulator
from app.ml.simulation.ledger import billing
from app.ml.simulation.ledger.panel import build_panel
from app.ml.simulation.ledger.realism import realism_report
from app.models.loan import dpd_bucket_for

SMALL = LedgerConfig(n_borrowers=250, months=8, seed=11)


@pytest.fixture(scope="module")
def world():
    sim = LedgerSimulator(SMALL)
    ledger = sim.run(intercept=-4.1562)
    return ledger, build_panel(ledger, SMALL)


# ---------------------------------------------------------------------------
# 1. The billing spine
# ---------------------------------------------------------------------------

def test_dpd_is_a_pure_function_of_schedule_ledger_and_date():
    """The same inputs must give the same DPD however often it is asked.

    This is the property `Loan.dpd` does not have: it is overwritten in place,
    so asking it about a past date is impossible. Here DPD is arithmetic.
    """
    sched = billing.Schedule(first_due_day=np.array([-90, -90, -90]),
                             emi=np.array([1000.0, 1000.0, 1000.0]),
                             n_installments=np.array([12, 12, 12]))
    paid = np.array([0.0, 2000.0, 4000.0])
    first = billing.dpd_at(0, sched, paid)
    assert list(first) == list(billing.dpd_at(0, sched, paid))
    # Instalments fell due on days -90, -60, -30, 0. Nothing paid -> the oldest
    # unpaid is the one from day -90.
    assert first[0] == 90
    # Two cleared -> oldest unpaid is day -30.
    assert first[1] == 30
    # Four cleared -> everything billed is covered.
    assert first[2] == 0


def test_paying_more_moves_dpd_down_and_never_up():
    sched = billing.Schedule(np.full(5, -150), np.full(5, 1000.0), np.full(5, 24))
    paid = np.array([0.0, 1000.0, 2000.0, 3000.0, 4000.0])
    dpd = billing.dpd_at(0, sched, paid)
    assert list(dpd) == sorted(dpd, reverse=True)


def test_grace_shifts_the_boundary_and_nothing_else():
    sched = billing.Schedule(np.array([-10]), np.array([1000.0]), np.array([12]))
    paid = np.array([0.0])
    assert billing.dpd_at(0, sched, paid, grace_days=0)[0] == 10
    assert billing.dpd_at(0, sched, paid, grace_days=5)[0] == 5
    # Never negative: inside the grace an account is current, not ahead.
    assert billing.dpd_at(0, sched, paid, grace_days=30)[0] == 0


def test_overdue_is_billed_minus_paid(world):
    _, panel = world
    # Reconstructed independently of the panel's own arithmetic.
    assert (panel.overdue_amount >= 0).all()
    assert (panel.overdue_amount <= panel.emi_amount * panel.tenure_months + 1).all()


def test_the_panel_and_the_simulator_agree_on_dpd(world):
    """The panel recomputes `paid_known` from the ledger; the simulator carried
    its own running balance. Two derivations, and they must not disagree."""
    ledger, panel = world
    loans = ledger.loans.set_index("loan_id")
    for m in (0, 3, 6):
        rows = panel[panel.month_index == m]
        if not len(rows):
            continue
        L = loans.loc[rows.loan_id]
        sched = billing.Schedule(L.first_due_day.to_numpy(),
                                 L.emi_amount.to_numpy(dtype=float),
                                 L.tenure_months.to_numpy(), SMALL.cycle_days)
        pays = ledger.payments
        t = m * SMALL.cycle_days
        # STRICTLY BEFORE t, matching `panel._paid_known`. Both mean "what was
        # known at the start of day t" — the same thing `build_features` means
        # by as_of, since it floors the timestamp to midnight.
        seen = pays[(pays.payment_day < t) & (pays.final_status == "VERIFIED") &
                    (pays.status_effective_day < t)]
        got = seen.groupby("loan_id").amount.sum().reindex(rows.loan_id).fillna(0.0)
        paid = L.opening_paid.to_numpy(dtype=float) + got.to_numpy()
        expected = billing.dpd_at(t, sched, paid, SMALL.grace_days)
        assert np.array_equal(expected, rows.dpd.to_numpy())


def test_the_dpd_bucket_comes_from_the_products_own_rule(world):
    """Not a local copy. Seven spellings of this rule existed once."""
    _, panel = world
    expected = [dpd_bucket_for(d).value for d in panel.dpd]
    assert expected == list(panel.dpd_bucket)


# ---------------------------------------------------------------------------
# 2. Point-in-time correctness
# ---------------------------------------------------------------------------

def test_no_feature_can_see_a_payment_from_its_own_outcome_window(world):
    """The strongest available check on the gate: an account whose ONLY payment
    lands inside the outcome window must read as never having paid at as_of."""
    ledger, panel = world
    pays = ledger.payments
    for m in (2, 4):
        t = m * SMALL.cycle_days
        rows = panel[panel.month_index == m]
        before = set(pays[pays.payment_day < t].loan_id)
        never_paid = rows[~rows.loan_id.isin(before)]
        if not len(never_paid):
            continue
        # days_since_last_payment is NaN precisely when nothing was seen.
        assert never_paid.days_since_last_payment.isna().all()
        assert (never_paid.paid_ratio_6m == 0.5).all() or \
               (never_paid.paid_ratio_6m == 0.0).all()


def test_a_promise_resolved_after_as_of_is_not_counted_as_kept(world):
    """`ptp_kept_6m` counts promises resolved BEFORE as_of. A promise honoured
    the day after is future information, and the live schema — which stores only
    a PTP's current status — cannot express the distinction at all."""
    ledger, panel = world
    # 2026-09-28: the panel counts only promises the PRODUCT can hold (a
    # promise is for money: ledger/product_rules.product_promises, the same
    # filter the materialiser applies). The expectation reads through it too,
    # or it would count the simulator's zero-amount promises the panel drops.
    from app.ml.simulation.ledger.product_rules import product_promises
    ptps = product_promises(ledger.ptps)
    if not len(ptps):
        pytest.skip("no promises generated")
    for m in (3, 5):
        t = m * SMALL.cycle_days
        window = ptps[(ptps.created_day < t) & (ptps.created_day >= t - 180)]
        kept = window[(window.resolved_day >= 0) & (window.resolved_day < t) &
                      (window.resolved_status == "HONORED")]
        rows = panel[panel.month_index == m].set_index("loan_id")
        expect = kept.groupby("loan_id").size().reindex(rows.index).fillna(0.0)
        assert np.array_equal(expect.to_numpy(), rows.ptp_kept_6m.to_numpy())


def test_the_outcome_window_is_half_open_after_the_observation_day(world):
    """Money arriving ON as_of happened before the prediction. `outcomes.py`
    uses the same convention and the two must not drift apart."""
    ledger, panel = world
    pays = ledger.payments
    m = 3
    t = m * SMALL.cycle_days
    rows = panel[panel.month_index == m].set_index("loan_id")
    hi = t + 30
    # Status AS AT WINDOW CLOSE, not final status: a payment reversed after the
    # horizon still counted while the horizon was open, and the production
    # labeller reads status at labelling time for the same reason.
    status_at_hi = np.where(pays.status_effective_day <= hi,
                            pays.final_status, pays.initial_status)
    w = pays[(pays.payment_day > t) & (pays.payment_day <= hi) &
             (status_at_hi == "VERIFIED")]
    expect = w.groupby("loan_id").amount.sum().reindex(rows.index).fillna(0.0)
    assert np.allclose(expect.to_numpy(), rows.recovered_amount.to_numpy(), atol=0.02)


def test_the_baseline_is_frozen_at_as_of_not_at_labelling_time(world):
    """`outcome_baseline` in production freezes overdue and EMI at prediction
    time because both are overwritten in place on `Loan`. The panel carries the
    same two values so `outcomes.evaluate` can be replayed against it verbatim."""
    _, panel = world
    assert np.allclose(panel.baseline_overdue_amount, panel.overdue_amount)
    assert np.allclose(panel.baseline_emi_amount, panel.emi_amount)
    assert np.allclose(panel.outcome_threshold,
                       0.8 * np.minimum(panel.baseline_overdue_amount,
                                        panel.baseline_emi_amount), atol=0.02)


def test_the_label_matches_the_products_own_outcome_rule(world):
    """y = 1 iff paid < 0.8 * min(overdue, emi) — the rule in
    ml/pipeline/outcomes.py, which the nightly labeller runs on live rows."""
    from app.ml.pipeline.outcomes import MATERIAL_PAYMENT_RATIO

    _, panel = world
    thr = MATERIAL_PAYMENT_RATIO * np.minimum(panel.baseline_overdue_amount,
                                              panel.baseline_emi_amount)
    assert np.array_equal((panel.recovered_amount < thr).astype(int),
                          panel.y.to_numpy())


# ---------------------------------------------------------------------------
# 3. The latents stay out
# ---------------------------------------------------------------------------

def test_no_latent_reaches_the_panel(world):
    _, panel = world
    banned = {"willingness", "capacity", "reachability", "shock_state",
              "true_pay_logit", "agent_skill", "_w", "_w_anchor"}
    assert banned.isdisjoint(panel.columns)
    assert not [c for c in panel.columns if c.startswith("_")]


def test_the_ground_truth_is_a_separate_table(world):
    """Joining it has to be a deliberate act, not the side effect of a merge."""
    ledger, panel = world
    assert "willingness" in ledger.ground_truth.columns
    assert "willingness" not in panel.columns


def test_every_forbidden_column_the_spec_names_is_absent(world):
    _, panel = world
    leaked = [c for c in RECOVERY_RISK.forbidden
              if c in panel.columns and c not in ("y", "recovered_amount")]
    assert leaked == []


# ---------------------------------------------------------------------------
# 4. Reproducibility, and the pipeline contract
# ---------------------------------------------------------------------------

def test_the_same_seed_reproduces_the_book_exactly():
    a = build_panel(LedgerSimulator(SMALL).run(intercept=-4.1), SMALL)
    b = build_panel(LedgerSimulator(SMALL).run(intercept=-4.1), SMALL)
    pd.testing.assert_frame_equal(a, b)


def test_a_different_seed_gives_a_different_book():
    from dataclasses import replace
    other = replace(SMALL, seed=SMALL.seed + 1)
    a = build_panel(LedgerSimulator(SMALL).run(intercept=-4.1), SMALL)
    b = build_panel(LedgerSimulator(other).run(intercept=-4.1), other)
    assert not a.dpd.equals(b.dpd)


def test_the_panel_carries_every_feature_the_model_spec_selects(world):
    """Phase 2 trains `ModelSpec.RECOVERY_RISK` on this frame unchanged. A
    missing column would silently reduce coverage rather than fail."""
    _, panel = world
    missing = [f for f in RECOVERY_RISK.all_features if f not in panel.columns]
    assert missing == []
    assert RECOVERY_RISK.target in panel.columns
    assert RECOVERY_RISK.split_col in panel.columns


def test_the_panel_carries_every_feature_every_candidate_spec_names(world):
    """2026-09-15. The 2.0.0 spec names features only this panel produces
    (calls, visit outcomes, flags, promise and payment shape). The
    book_simulator contract test cannot cover it, so this one does, for every
    spec that declares the ledger as its development panel."""
    from app.ml.pipeline.config import CANDIDATE_SPECS

    _, panel = world
    checked = 0
    for (name, version), spec in CANDIDATE_SPECS.items():
        if spec.training_panel != "ledger":
            continue
        missing = [f for f in spec.all_features if f not in panel.columns]
        assert missing == [], f"{name} {version}: panel lacks {missing}"
        checked += 1
    assert checked >= 1


def test_the_new_channels_are_events_the_panel_derives_from(world):
    """The calls and flags tables exist, carry the columns the panel reads,
    and the panel's counts are consistent with them on one snapshot."""
    ledger, panel = world
    assert {"loan_id", "day", "answered", "outcome", "payment_intent"} <= set(ledger.calls.columns)
    assert {"loan_id", "day", "flag"} <= set(ledger.flags.columns)
    assert {"outcome", "default_reason"} <= set(ledger.visits.columns)
    t = 6 * SMALL.cycle_days
    rows = panel[panel.month_index == 6].set_index("loan_id")
    c = ledger.calls[(ledger.calls.day < t) & (ledger.calls.day >= t - 90)]
    want = c.groupby("loan_id").size().reindex(rows.index).fillna(0)
    assert np.array_equal(want.to_numpy(float), rows.calls_3m.to_numpy(float))


def test_the_split_column_is_chronological_and_dense(world):
    _, panel = world
    months = sorted(panel.month_index.unique())
    assert months == list(range(months[0], months[-1] + 1))


# ---------------------------------------------------------------------------
# 5. The book is not degenerate
# ---------------------------------------------------------------------------

def test_the_book_spans_the_whole_delinquency_range(world):
    """The demo seed starts every case at DPD 35, which leaves a binner nothing
    to bin at the good end and inflates DPD's apparent power."""
    _, panel = world
    assert panel.dpd.min() == 0
    assert panel.dpd.max() > 150
    assert panel.dpd_bucket.nunique() >= 4


def test_accounts_both_enter_and_leave(world):
    """A book with no turnover ages into a deep-delinquency sink — measured at
    51.7% NPA in the previous simulator before its lifecycle hazards existed."""
    ledger, _ = world
    events = set(ledger.lifecycle.event)
    assert "OPENED" in events
    assert events & {"CLOSED", "WRITTEN_OFF", "SETTLED"}


def test_payments_carry_a_real_status_history(world):
    """The live database stores only a payment's CURRENT status, so a reversal
    is indistinguishable from a payment that was never verified. Here the
    transition has a date, which is what makes status-at-a-past-date answerable."""
    ledger, _ = world
    pays = ledger.payments
    changed = pays[pays.initial_status != pays.final_status]
    assert len(changed) > 0
    assert (changed.status_effective_day > changed.payment_day).all()


def test_the_realism_report_labels_every_band_with_its_provenance(world):
    """A band with no provenance is an assumption that will be quoted as fact."""
    ledger, panel = world
    rep = realism_report(ledger, panel, SMALL)
    assert rep["checks"]
    for c in rep["checks"]:
        assert c["provenance"] in {"REGULATORY", "INTERNAL", "ASSUMPTION",
                                   "CALIBRATION_TARGET"}
        assert c["note"]
        assert c["status"] in {"PASS", "FAIL", "REPORTED"}


def test_no_phase_1_check_measures_model_performance(world):
    """Keeping model metrics out of Phase 1 is what stops the generator being
    tuned against a model result."""
    ledger, panel = world
    rep = realism_report(ledger, panel, SMALL)
    names = " ".join(c["check"] for c in rep["checks"])
    for banned in ("gini", "auc", "ks", "iv", "oracle", "brier"):
        assert banned not in names
