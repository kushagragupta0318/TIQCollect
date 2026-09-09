# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-09 — NEW. Derives the modelling panel FROM THE EVENT LEDGER.
#
#   THIS MODULE NEVER SEES THE SIMULATOR'S STATE. It is handed event tables and
#   nothing else, and it recomputes `paid_known` — and therefore dpd and
#   overdue_amount — from the payment ledger by itself. That redundancy is the
#   point: `test_panel_dpd_matches_the_simulators_own_running_balance` compares
#   the two derivations, so a bug in either is a failing test rather than a
#   plausible number.
#
#   WHY THE OUTCOME CANNOT LEAK. Features for as_of T read events with
#   `day < T`; the label reads events in `(T, T+30]`. The two windows are
#   disjoint by construction and the boundary is written once, here, rather than
#   restated at each feature. `_before` and `_window` are the only two gates.
# ───────────────────────────────────────────────────────────────────────────
"""Point-in-time feature panel, derived from the event ledger.

    panel = build_panel(ledger)

Column contract matches `book_simulator`'s panel so `ml/pipeline/train.py` and
`ModelSpec.RECOVERY_RISK` consume it unchanged — that comparability is what
makes any result here readable against the committed 1.1.0 baseline.

THE FOUR CHAMPION FEATURES, and where each comes from:

    dpd             installments + payments, FIFO      (billing.dpd_at)
    overdue_amount  installments + payments            (billing.overdue_at)
    cibil_score     last bureau pull with pull_day < T  — STALE ON PURPOSE
    ptp_kept_ratio  PTPs created in [T-180, T), kept iff resolved BEFORE T

That last one is the definition the PRODUCTION ADAPTER uses
(`ml_scoring_service._history_features`): denominator is every promise SET in
the window, not just the resolved ones, and the no-evidence prior is 0.5. The
repayment snapshot labeller uses a different denominator. Two definitions exist
in this repo; this file implements the one that serves.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from app.ml.simulation.ledger import billing
from app.ml.simulation.ledger.config import LedgerConfig
from app.ml.simulation.ledger.simulator import Ledger
from app.models.loan import dpd_bucket_for

MATERIAL_RATIO = 0.8          # must equal outcomes.MATERIAL_PAYMENT_RATIO
HORIZON_DAYS = 30


def _status_at(payments: pd.DataFrame, day: int) -> pd.Series:
    """A payment's status AS KNOWN on `day`.

    Not its final status. A payment verified on day 10 and reversed on day 25
    was VERIFIED on day 20, and a panel that used the final status would be
    telling the model on day 20 something only discoverable on day 25.
    """
    return np.where(payments.status_effective_day <= day,
                    payments.final_status, payments.initial_status)


def _paid_known(payments: pd.DataFrame, day: int) -> pd.Series:
    """Cumulative VERIFIED money received STRICTLY BEFORE `day`, per loan.

    2026-09-09 — this used `<= day` while `_before` used `< day`, so within this
    one module a payment on day t moved the BALANCE but not the payment
    HISTORY. Two boundaries for one as_of is a defect on its own terms, and it
    surfaced as the materialised `Loan.overdue_amount` disagreeing with the
    panel's on every account that paid on a snapshot day. One boundary now:
    everything the panel reports is what was known at the START of day t, which
    is also what `build_features` means by `as_of` (it floors the timestamp to
    midnight). The outcome window `(t, t+30]` still starts after day t, so
    features and label remain disjoint with a day to spare.
    """
    if not len(payments):
        return pd.Series(dtype=float)
    m = (payments.payment_day < day) & (_status_at(payments, day) == "VERIFIED")
    return payments[m].groupby("loan_id").amount.sum()


def build_panel(ledger: Ledger, cfg: LedgerConfig | None = None) -> pd.DataFrame:
    cfg = cfg or LedgerConfig(**{k: v for k, v in ledger.config.items()
                                 if k in LedgerConfig.__dataclass_fields__})
    cyc = cfg.cycle_days
    loans = ledger.loans.set_index("loan_id")
    borrowers = ledger.borrowers.set_index("borrower_id")
    pays, visits, ptps, pulls = (ledger.payments, ledger.visits,
                                 ledger.ptps, ledger.bureau_pulls)

    # Terminal events end an account's life; a loan contributes rows only while
    # it is live. `OPENED` is not terminal.
    term = ledger.lifecycle[ledger.lifecycle.event != "OPENED"]
    closed_on = term.groupby("loan_id").day.min().to_dict()
    opened_on = loans["opened_day"].to_dict()

    frames = []
    for m in range(cfg.months):
        t = m * cyc
        as_of = cfg.start_date + timedelta(days=t)

        live = [lid for lid in loans.index
                if opened_on[lid] <= t < closed_on.get(lid, 10 ** 9)]
        if not live:
            continue
        L = loans.loc[live]
        B = borrowers.loc[L.borrower_id.to_numpy()]

        emi = L.emi_amount.to_numpy(dtype=float)
        sched = billing.Schedule(L.first_due_day.to_numpy(), emi,
                                 L.tenure_months.to_numpy(), cyc)

        # Opening balance + everything the ledger has seen since. See
        # `loans.opening_paid` for why the first term is not an event.
        paid_map = _paid_known(pays, t)
        paid = (L.opening_paid.to_numpy(dtype=float)
                + paid_map.reindex(live).fillna(0.0).to_numpy())

        dpd = billing.dpd_at(t, sched, paid, cfg.grace_days)
        overdue = billing.overdue_at(t, sched, paid)
        penal = billing.penal_at(overdue, dpd, cfg.penal_rate_monthly, cyc)
        settled = billing.settled_count(paid, emi, sched.billed_count(t))
        out_principal = np.maximum(
            L.sanction_amount.to_numpy() *
            (1 - settled / np.maximum(L.tenure_months.to_numpy(), 1)), 0.0)
        total_out = out_principal + overdue + penal

        f = pd.DataFrame(index=pd.Index(live, name="loan_id"))
        f["borrower_id"] = L.borrower_id.to_numpy()
        f["as_of_date"] = as_of
        f["month_index"] = m

        # ── bureau, as of the last pull STRICTLY BEFORE t ───────────────────
        f["cibil_score"] = _asof_bureau(pulls, loans, live, t)
        for col in ("credit_vintage_months", "num_open_loans", "num_enquiries_6m",
                    "other_lender_delinq", "utilization_pct",
                    "mail_returned_count", "address_vintage_months",
                    "phone_verified"):
            f[col] = B[col].to_numpy()
        f["thin_file"] = (B.credit_vintage_months.to_numpy() < 24).astype(int)

        # ── loan ────────────────────────────────────────────────────────────
        f["loan_type"] = L.loan_type.to_numpy()
        f["is_secured"] = L.is_secured.to_numpy()
        f["sanction_amount"] = L.sanction_amount.to_numpy()
        f["emi_amount"] = emi
        f["tenure_months"] = L.tenure_months.to_numpy()
        f["interest_rate"] = L.interest_rate.to_numpy()
        # From the loan's ONE origination fact, so the adapter — which computes
        # this from `disbursement_date` — and the panel cannot drift. The
        # earlier version rebuilt seasoning from `opened_day` plus a first-due
        # offset, which is a second definition of the same quantity.
        f["months_on_book"] = np.maximum(
            (t - L.origination_day.to_numpy()) // cyc, 0)
        f["sourcing_channel"] = L.sourcing_channel.to_numpy()
        f["branch_code"] = L.branch_code.to_numpy()

        # ── delinquency ─────────────────────────────────────────────────────
        f["dpd"] = dpd
        f["dpd_bucket"] = [dpd_bucket_for(d).value for d in dpd]
        f["outstanding_principal"] = np.round(out_principal, 2)
        f["overdue_amount"] = np.round(overdue, 2)
        f["penal_charges"] = np.round(penal, 2)
        f["total_outstanding"] = np.round(total_out, 2)
        f["arrears_ratio"] = np.round(overdue / np.maximum(emi, 1), 3)
        f["penal_ratio"] = np.round(penal / np.maximum(total_out, 1), 4)
        f["outstanding_to_sanction"] = np.round(
            out_principal / np.maximum(L.sanction_amount.to_numpy(), 1), 3)

        # ── behaviour, all from events STRICTLY BEFORE t ────────────────────
        f = f.join(_payment_history(pays, live, t, emi, cyc))
        f = f.join(_visit_history(visits, live, t))
        f = f.join(_ptp_history(ptps, live, t))

        # ── demographic ─────────────────────────────────────────────────────
        # Age at as_of, not at origination. The adapter derives it from
        # `date_of_birth`, so it moves; a static column would disagree with it
        # by a whole year over a two-year book.
        f["age"] = (t - B.dob_day.to_numpy()) // 365
        f["city"] = B.city.to_numpy()
        f["employment_type"] = B.employment_type.to_numpy()
        f["residence_type"] = B.residence_type.to_numpy()
        f["monthly_income"] = B.monthly_income.to_numpy()
        f["dti_ratio"] = np.round(emi / np.maximum(B.monthly_income.to_numpy(), 1), 3)

        # ── FROZEN BASELINE, then the outcome window ────────────────────────
        # Frozen BEFORE the label is computed, and stored, so the panel carries
        # exactly what `ModelPrediction.outcome_baseline` carries in production
        # and `outcomes.evaluate` can be replayed against it verbatim.
        f["baseline_overdue_amount"] = np.round(overdue, 2)
        f["baseline_emi_amount"] = np.round(emi, 2)
        # Threshold and label are computed from the ROUNDED, STORED baseline
        # rather than from full-precision intermediates, so the panel is
        # self-verifiable: y can be recomputed from the columns beside it. With
        # unrounded intermediates a row whose payment sits within a paisa of the
        # bar disagrees with its own stored numbers, which is indefensible in
        # exactly the rows a reviewer would look at first.
        threshold = MATERIAL_RATIO * np.minimum(f.baseline_overdue_amount.to_numpy(),
                                                f.baseline_emi_amount.to_numpy())
        f["outcome_threshold"] = np.round(threshold, 2)

        paid_fwd = np.round(_window_paid(pays, live, t, t + HORIZON_DAYS), 2)
        f["recovered_amount"] = paid_fwd
        # y = 1 is the RISK event (no material payment), matching config.py.
        # An account with nothing owed cannot fail to pay it, so it is not a
        # risk row at all and is dropped below rather than scored as a success.
        f["y"] = (paid_fwd < f.outcome_threshold.to_numpy()).astype(int)
        f["_has_demand"] = overdue > 0

        frames.append(f.reset_index())

    panel = pd.concat(frames, ignore_index=True)
    panel = panel[panel._has_demand].drop(columns=["_has_demand"])
    return panel.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Window helpers — the ONLY two places a time boundary is written
# ---------------------------------------------------------------------------

def _before(df: pd.DataFrame, day_col: str, t: int, lookback: int | None = None):
    """Events known STRICTLY BEFORE day `t`.

    MATCHES THE ADAPTER EXACTLY, and the adapter is stricter than it looks:
    `build_features` does `datetime.combine(as_of, datetime.min.time())`, so it
    DISCARDS THE TIME OF DAY and its `as_of_dt` is always MIDNIGHT at the start
    of day t. Its `check_in_time < as_of_dt` therefore means "before day t
    began", not "by the end of day t".

    *(This was briefly changed to `<= t` on 2026-09-09 on the belief that the
    adapter's as_of was end-of-day. It is not, and the change broke five
    features that had been agreeing. The day-out errors it was chasing came from
    the materialiser writing events at MIDDAY, which made
    `(midnight(t) - midday(t-21)).days` floor to 20 against the panel's 21.
    Corrected there instead — see `materialise._dt`.)*
    """
    m = df[day_col] < t
    if lookback is not None:
        # Inclusive lower edge, because the adapter's is: it tests
        # `created_at >= as_of_dt - 180d` and both sides are midnights.
        m &= df[day_col] >= t - lookback
    return df[m]


def _window(df: pd.DataFrame, day_col: str, lo: int, hi: int):
    """Half-open at the start: (lo, hi]. Money arriving ON the observation day
    happened before the prediction and says nothing about what came next —
    the same convention `outcomes.payment_window` uses."""
    return df[(df[day_col] > lo) & (df[day_col] <= hi)]


def _asof_bureau(pulls, loans, live, t) -> np.ndarray:
    """The last score pulled before t; the origination value if never repulled.

    Staleness is deliberate and realistic: a refreshed score carries recent
    behaviour, and one pulled ninety days ago does not. A simulator that hands
    the model a live score is giving it information the product does not have.
    """
    base = loans.loc[live, "opening_cibil"].to_numpy(dtype=float) \
        if "opening_cibil" in loans.columns else np.full(len(live), np.nan)
    if len(pulls):
        p = _before(pulls, "day", t)
        if len(p):
            last = p.sort_values("day").groupby("loan_id").cibil_score.last()
            got = last.reindex(live).to_numpy(dtype=float)
            base = np.where(np.isnan(got), base, got)
    return base


def _payment_history(pays, live, t, emi, cyc) -> pd.DataFrame:
    idx = pd.Index(live, name="loan_id")
    out = pd.DataFrame(index=idx)
    if not len(pays):
        for c in ("paid_ratio_3m", "paid_ratio_6m", "paid_ratio_12m"):
            out[c] = 0.5
        out["days_since_last_payment"] = np.nan
        out["bounce_count_6m"] = 0.0
        return out

    hist = _before(pays, "payment_day", t)
    ok = hist[_status_at(hist, t) == "VERIFIED"]
    for label, days, months in (("3m", 90, 3), ("6m", 180, 6), ("12m", 365, 12)):
        w = ok[ok.payment_day >= t - days]
        paid = w.groupby("loan_id").amount.sum().reindex(idx).fillna(0.0).to_numpy()
        # WHOLE INSTALMENTS, not days/cycle. The model was trained on a
        # denominator summed over the window's MONTHS (`book_simulator`:
        # `hist_due[:, lo12:m].sum(1)`), and the adapter now does the same. A
        # 365-day window over 30-day cycles is 12.17 cycles, so dividing by that
        # put `paid_ratio_12m` 1.4% below the served value on every loan — small,
        # but a skew, and the panel's job is to reproduce the definition rather
        # than improve on it.
        due = emi * months
        # Prior 0.5 where nothing was due — the abstain-don't-guess rule the
        # scorecards follow and the adapter repeats.
        out[f"paid_ratio_{label}"] = np.round(
            np.where(due > 0, np.clip(paid / np.maximum(due, 1), 0, 1.5), 0.5), 3)

    last = ok.groupby("loan_id").payment_day.max().reindex(idx)
    out["days_since_last_payment"] = (t - last).to_numpy(dtype=float)
    # A "bounce" is money that did not stick: rejected outright, or reversed
    # by t. This is the observable consequence of the status lifecycle.
    bad = hist[np.isin(_status_at(hist, t), ["REJECTED", "REVERSED"])]
    bad = bad[bad.payment_day >= t - 180]
    out["bounce_count_6m"] = bad.groupby("loan_id").size().reindex(idx).fillna(0.0).to_numpy()
    return out


def _visit_history(visits, live, t) -> pd.DataFrame:
    idx = pd.Index(live, name="loan_id")
    out = pd.DataFrame(index=idx)
    if not len(visits):
        out["visits_3m"] = 0.0
        out["visits_6m"] = 0.0
        out["contact_rate_6m"] = 0.45
        out["days_since_last_contact"] = np.nan
        out["distinct_agents_6m"] = 1.0
        return out

    h = _before(visits, "day", t)
    v3 = h[h.day >= t - 90]
    v6 = h[h.day >= t - 180]
    out["visits_3m"] = v3.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    n6 = v6.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    out["visits_6m"] = n6
    met6 = v6[v6.met].groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    # Prior 0.45, the same value the adapter uses when a case has no visits.
    out["contact_rate_6m"] = np.round(
        np.where(n6 > 0, met6 / np.maximum(n6, 1), 0.45), 3)
    out["distinct_agents_6m"] = np.maximum(
        v6.groupby("loan_id").agent_id.nunique().reindex(idx).fillna(0).to_numpy(float), 1.0)
    met = h[h.met]
    last = met.groupby("loan_id").day.max().reindex(idx)
    out["days_since_last_contact"] = (t - last).to_numpy(dtype=float)
    return out


def _ptp_history(ptps, live, t) -> pd.DataFrame:
    """The adapter's definition exactly: set-in-window as denominator.

    KEPT MEANS KEPT BY `t`. A promise made before t and honoured after it is
    NOT kept as far as this row is concerned, because on day t nobody knew. The
    live database cannot express that — `PTP.status` holds only the current
    value, with no resolution timestamp — which is precisely why reconstructing
    this feature historically there is impossible.
    """
    idx = pd.Index(live, name="loan_id")
    out = pd.DataFrame(index=idx)
    if not len(ptps):
        out["ptp_set_6m"] = 0.0
        out["ptp_kept_6m"] = 0.0
        out["ptp_kept_ratio"] = 0.5
        return out

    h = _before(ptps, "created_day", t, lookback=180)
    n_set = h.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    kept = h[(h.resolved_day >= 0) & (h.resolved_day < t) &
             (h.resolved_status == "HONORED")]
    n_kept = kept.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    out["ptp_set_6m"] = n_set
    out["ptp_kept_6m"] = n_kept
    out["ptp_kept_ratio"] = np.round(
        np.where(n_set > 0, n_kept / np.maximum(n_set, 1), 0.5), 3)
    return out


def _window_paid(pays, live, lo, hi) -> np.ndarray:
    """VERIFIED money in (lo, hi], judged by status as at `hi`.

    Status as at the END of the window, not as at the prediction: the label is
    asked after the horizon closes, and what is wanted is what finally stuck.
    `outcomes.evaluate` reads current status for the same reason.
    """
    idx = pd.Index(live, name="loan_id")
    if not len(pays):
        return np.zeros(len(live))
    w = _window(pays, "payment_day", lo, hi)
    w = w[_status_at(w, hi) == "VERIFIED"]
    return w.groupby("loan_id").amount.sum().reindex(idx).fillna(0.0).to_numpy()
