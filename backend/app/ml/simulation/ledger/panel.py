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
#
# 2026-09-15 — Seventeen behavioural features added for recovery_risk 2.0.0,
#   every one derived from an event table the PRODUCT ALREADY STORES and the
#   adapter (`ml_scoring_service._history_features`) now computes with the same
#   arithmetic: visit outcomes (RTP / DISPUTE), call attempts and answers, broken
#   and rescheduled promises and their size, the shape and regularity of the
#   payment ledger, and the two customer flags. The definitions are written ONCE
#   here in words and once in each implementation, and
#   tests/test_ledger_phase3_adapter_equality.py holds the two implementations
#   to each other feature by feature. A feature that exists in only one of them
#   is not a feature.
#
#   ABSTAIN, DON'T GUESS — applied to the new ratios. `contact_rate_6m` and
#   `ptp_kept_ratio` carry a numeric prior (0.45 / 0.5) where there is no
#   evidence, which tests/test_batch_scoring_parity.py records as a default
#   wearing an observation's clothes. The new ratios are NaN where their
#   denominator is empty, so the WOE binner routes them to the Missing bin it
#   was fitted with. Counts are 0 where nothing happened, because "no refusals
#   in six months" is an observation.
#
# 2026-09-15 (later) — Six more for recovery_risk 2.1.0, the fresh development:
#   paid_ratio_1m, pay_amount_cv_6m, call_answer_rate_3m, intent_rate_6m,
#   days_since_last_call, last_visit_outcome. Chosen by a discovery pass over
#   ~100 candidates derived from these same tables and carried here only
#   because the forward selection reached them; see config.RECOVERY_RISK_V21
#   for the definitions in words. Same boundary as everything above: events
#   with day < t, window [t - W, t).
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
from app.ml.simulation.ledger.simulator import HARDSHIP_REASONS, Ledger
from app.models.loan import dpd_bucket_for

MATERIAL_RATIO = 0.8          # must equal outcomes.MATERIAL_PAYMENT_RATIO
HORIZON_DAYS = 30


def _status_at(payments: pd.DataFrame, day: int, *,
               inclusive: bool = False) -> pd.Series:
    """A payment's status AS KNOWN at the START of `day`.

    Not its final status. A payment verified on day 10 and reversed on day 25
    was VERIFIED on day 20, and a panel that used the final status would be
    telling the model on day 20 something only discoverable on day 25.

    2026-09-15 — STRICT (`< day`) for features, and that is a correction. This
    used `<= day`, so a payment made on day 89 whose verification took effect
    on day 90 counted in the day-90 FEATURES, while the materialiser
    (`rewind_to`: `status_effective_day < day`) and the simulator itself —
    which derives dpd BEFORE it applies the day's status transitions — both
    said it did not. The same defect as the 2026-09-09 `payment_day` boundary,
    one column over: two boundaries for one as_of. Found by the Phase 3
    equality test when a reshuffled book put such a transition on a snapshot
    day: `dpd` 10 against 40 on one loan, and every payment-derived ratio with
    it. `inclusive=True` is for the LABEL only — `_window_paid` asks what
    finally stuck by the END of the horizon, so a verification effective on
    its last day counts there.
    """
    known = (payments.status_effective_day <= day if inclusive
             else payments.status_effective_day < day)
    return np.where(known, payments.final_status, payments.initial_status)


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
    calls = getattr(ledger, "calls", pd.DataFrame())
    flags = getattr(ledger, "flags", pd.DataFrame())

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
        f = f.join(_ptp_history(ptps, live, t, emi))
        f = f.join(_call_history(calls, live, t))
        f = f.join(_commitment_history(calls, pays, live, t, emi, cfg))
        f = f.join(_disposition_history(calls, visits, live, t))
        # Cross-channel recency — 2026-09-15 (later). The freshest successful
        # contact of either kind, and how the last month of attempts went.
        f["days_since_last_successful_contact"] = np.fmin(
            f.days_since_last_contact.to_numpy(dtype=float),
            f.days_since_last_answered_call.to_numpy(dtype=float))
        # Momentum: is the borrower paying MORE lately than over the year?
        # A pure derivation of two columns already on the row, so it can never
        # disagree with them; the adapter computes it the same way.
        f["paid_momentum"] = np.round(f.paid_ratio_3m - f.paid_ratio_12m, 3)
        # 30-day against 90-day: the same derivation one step closer to now.
        f["payment_momentum_30_vs_90"] = np.round(f.paid_ratio_1m - f.paid_ratio_3m, 3)

        # ── flags, as they stood at the START of day t ─────────────────────
        # `is_hostile` is an EVENT with a day, raised on some refusals and
        # never lowered — exactly how `Customer.is_hostile` behaves in the
        # product. `fraud_flag` is the bank's flag, static from origination.
        f["is_hostile"] = _hostile_at(flags, live, t)
        f["fraud_flag"] = (B["fraud_flag"].to_numpy(dtype=int)
                           if "fraud_flag" in B.columns else 0)

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
        out["payments_6m"] = 0.0
        out["partial_payment_share_6m"] = np.nan
        out["payment_gap_cv_12m"] = np.nan
        out["last_payment_to_emi"] = np.nan
        out["paid_ratio_1m"] = 0.0
        out["pay_amount_cv_6m"] = np.nan
        out["payment_count_30d"] = 0.0
        out["partial_rate_90d"] = np.nan
        out["payment_amount_trend"] = np.nan
        out["payment_gap_mean_12m"] = np.nan
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

    # ── the SHAPE of the ledger, 2026-09-15 ────────────────────────────────
    emi_s = pd.Series(emi, index=idx)
    w6 = ok[ok.payment_day >= t - 180]
    n6 = w6.groupby("loan_id").size().reindex(idx).fillna(0.0)
    out["payments_6m"] = n6.to_numpy(float)
    # Share of verified payments under 90% of an instalment. NaN, not 0, where
    # there were no payments: "all of nothing was partial" is not an
    # observation.
    part = (w6.amount.to_numpy() < 0.9 * emi_s.reindex(w6.loan_id).to_numpy())
    n_part = pd.Series(part, index=w6.loan_id).groupby(level=0).sum().reindex(idx)
    out["partial_payment_share_6m"] = np.round(
        np.where(n6 > 0, n_part.fillna(0).to_numpy() / np.maximum(n6, 1), np.nan), 3)
    # Regularity: coefficient of variation of the gaps between consecutive
    # verified payments over the last year. Needs three payments (two gaps).
    # A borrower who pays every due date has a CV near 0; one who pays when
    # cornered has a large one.
    w12 = ok[ok.payment_day >= t - 365]
    out["payment_gap_cv_12m"] = _gap_cv(w12, idx).to_numpy()
    # Size of the most recent verified payment, relative to the instalment.
    last_row = ok.sort_values("payment_day").groupby("loan_id").tail(1).set_index("loan_id")
    last_amt = last_row.amount.reindex(idx)
    out["last_payment_to_emi"] = np.round(
        (last_amt / np.maximum(emi_s, 1.0)).to_numpy(dtype=float), 3)

    # ── 2.1.0: the freshest read, and the regularity of SIZE ───────────────
    # One cycle back, against ONE instalment, same clip as the longer ratios.
    # Zero where nothing was paid: a month without a payment is an
    # observation, not a gap in the record.
    w1 = ok[ok.payment_day >= t - 30]
    paid1 = w1.groupby("loan_id").amount.sum().reindex(idx).fillna(0.0).to_numpy()
    out["paid_ratio_1m"] = np.round(np.clip(paid1 / np.maximum(emi, 1.0), 0, 1.5), 3)
    # Population CV of the AMOUNTS over six months, two payments minimum —
    # beside `payment_gap_cv_12m`, which is the CV of the TIMING. A borrower
    # who pays the same sum each time and one who pays whatever is in hand
    # look identical to every ratio above; they do not look identical here.
    out["pay_amount_cv_6m"] = _amount_cv(w6, idx).to_numpy()

    # ── the last month and the last payment, 2026-09-15 (later) ────────────
    out["payment_count_30d"] = w1.groupby("loan_id").size().reindex(idx).fillna(0.0).to_numpy(float)
    w3 = ok[ok.payment_day >= t - 90]
    n3 = w3.groupby("loan_id").size().reindex(idx).fillna(0.0)
    part3 = (w3.amount.to_numpy() < 0.9 * emi_s.reindex(w3.loan_id).to_numpy())
    npart3 = pd.Series(part3, index=w3.loan_id).groupby(level=0).sum().reindex(idx).fillna(0)
    out["partial_rate_90d"] = np.round(
        np.where(n3 > 0, npart3.to_numpy(float) / np.maximum(n3.to_numpy(float), 1), np.nan), 3)
    # Last verified payment against the six-month mean: rising or falling.
    mean6 = w6.groupby("loan_id").amount.mean().reindex(idx)
    n6s = w6.groupby("loan_id").size().reindex(idx).fillna(0)
    trend = last_amt / mean6
    out["payment_amount_trend"] = np.round(
        np.where(n6s >= 2, trend.to_numpy(dtype=float), np.nan), 3)
    out["payment_gap_mean_12m"] = _gap_mean(w12, idx).to_numpy()
    return out


def _gap_mean(pays: pd.DataFrame, idx: pd.Index) -> pd.Series:
    """Mean gap in days between consecutive verified payments; two minimum."""
    out = pd.Series(np.nan, index=idx, dtype=float)
    if not len(pays):
        return out
    for lid, g in pays[pays.loan_id.isin(idx)].groupby("loan_id"):
        days = np.sort(g.payment_day.to_numpy(dtype=float))
        if len(days) >= 2:
            out[lid] = round(float(np.diff(days).mean()), 2)
    return out


def _amount_cv(pays: pd.DataFrame, idx: pd.Index) -> pd.Series:
    """std(amounts) / mean(amounts) per loan, ddof=0, NaN below two payments.

    The adapter repeats this arithmetic on `Payment.amount` over the same
    window and the same VERIFIED-at-as_of population.
    """
    out = pd.Series(np.nan, index=idx, dtype=float)
    if not len(pays):
        return out
    g = pays[pays.loan_id.isin(idx)].groupby("loan_id").amount
    n, mean, std = g.size(), g.mean(), g.std(ddof=0)
    ok = (n >= 2) & (mean > 0)
    cv = (std[ok] / mean[ok]).round(3)
    out.loc[cv.index] = cv.to_numpy()
    return out


def _gap_cv(pays: pd.DataFrame, idx: pd.Index) -> pd.Series:
    """std(gaps) / mean(gaps) over consecutive payment days per loan.

    Population std (ddof=0), three payments minimum, NaN otherwise. Same-day
    payments give a zero gap and are kept — two receipts on one day IS a
    pattern. The adapter repeats this arithmetic on `Payment.payment_date`.
    """
    out = pd.Series(np.nan, index=idx, dtype=float)
    if not len(pays):
        return out
    for lid, g in pays[pays.loan_id.isin(idx)].groupby("loan_id"):
        days = np.sort(g.payment_day.to_numpy(dtype=float))
        if len(days) < 3:
            continue
        gaps = np.diff(days)
        m = gaps.mean()
        if m > 0:
            out[lid] = round(float(gaps.std(ddof=0) / m), 3)
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
        out["rtp_visits_6m"] = 0.0
        out["dispute_visits_6m"] = 0.0
        out["adverse_visit_ratio_6m"] = np.nan
        out["hardship_visits_6m"] = 0.0
        out["last_visit_outcome"] = "NONE"
        out["recent_visit_outcome_30d"] = "NONE"
        out["visits_30d"] = 0.0
        out["met_visits_30d"] = 0.0
        out["hardship_flag_90d"] = 0.0
        out["days_since_hardship"] = np.nan
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

    # ── what happened when the door opened, 2026-09-15 ─────────────────────
    # Refusals and disputes over the same six-month window as `visits_6m`.
    # The ratio is over ALL visits (met or not) so that it is comparable
    # across borrowers who are hard to find, and NaN where nobody went.
    if "outcome" in v6.columns:
        rtp = v6[v6.outcome == "RTP"].groupby("loan_id").size().reindex(idx).fillna(0)
        dsp = v6[v6.outcome == "DISPUTE"].groupby("loan_id").size().reindex(idx).fillna(0)
    else:
        rtp = dsp = pd.Series(0.0, index=idx)
    out["rtp_visits_6m"] = rtp.to_numpy(float)
    out["dispute_visits_6m"] = dsp.to_numpy(float)
    out["adverse_visit_ratio_6m"] = np.round(
        np.where(n6 > 0, (rtp + dsp).to_numpy(float) / np.maximum(n6, 1), np.nan), 3)
    # What the borrower SAID about why: a hardship reason recorded at the
    # door. `Visit.default_reason` in the product.
    if "default_reason" in v6.columns:
        hard = v6[v6.default_reason.isin(HARDSHIP_REASONS)]
        out["hardship_visits_6m"] = (hard.groupby("loan_id").size()
                                     .reindex(idx).fillna(0).to_numpy(float))
    else:
        out["hardship_visits_6m"] = 0.0

    # ── 2.1.0: what the LAST visit said, met or not ─────────────────────────
    # The most recent visit strictly before t, whatever its age; "NONE" where
    # nobody has ever gone. Any age, because the newest observation of the
    # door is informative whether it was last week or last quarter, and the
    # binner sees "NONE" as its own category rather than a missing value.
    if "outcome" in h.columns and len(h):
        last_row = h.sort_values("day").groupby("loan_id").tail(1).set_index("loan_id")
        out["last_visit_outcome"] = (last_row.outcome.astype(str).reindex(idx)
                                     .fillna("NONE").to_numpy())
    else:
        out["last_visit_outcome"] = "NONE"

    # ── the last month at the door, 2026-09-15 (later) ─────────────────────
    v1 = h[h.day >= t - 30]
    out["visits_30d"] = v1.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    out["met_visits_30d"] = v1[v1.met].groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    if "outcome" in h.columns and len(v1):
        lr = v1.sort_values("day").groupby("loan_id").tail(1).set_index("loan_id")
        out["recent_visit_outcome_30d"] = (lr.outcome.astype(str).reindex(idx)
                                           .fillna("NONE").to_numpy())
    else:
        out["recent_visit_outcome_30d"] = "NONE"
    if "default_reason" in h.columns:
        hard = h[h.default_reason.isin(HARDSHIP_REASONS)]
        out["hardship_flag_90d"] = (hard[hard.day >= t - 90].groupby("loan_id").size()
                                    .reindex(idx).fillna(0) > 0).astype(float).to_numpy()
        last_h = hard.groupby("loan_id").day.max().reindex(idx)
        out["days_since_hardship"] = (t - last_h).to_numpy(dtype=float)
    else:
        out["hardship_flag_90d"] = 0.0
        out["days_since_hardship"] = np.nan
    return out


def _call_history(calls, live, t) -> pd.DataFrame:
    """Telephony, from the call ledger. 2026-09-15.

    `no_answer_streak` counts back from the most recent attempt before t until
    an ANSWERED one: three unanswered calls in a row is a different fact from
    three unanswered calls spread over a year. Zero where there are no calls
    at all — a streak of nothing is nothing, and recording it as NaN would put
    never-called borrowers in the Missing bin beside the ones the agent gave
    up on. `days_since_last_answered_call` is NaN where no call was ever
    answered, matching `days_since_last_contact`.
    """
    idx = pd.Index(live, name="loan_id")
    out = pd.DataFrame(index=idx)
    if not len(calls):
        out["calls_3m"] = 0.0
        out["call_answer_rate_6m"] = np.nan
        out["no_answer_streak"] = 0.0
        out["days_since_last_answered_call"] = np.nan
        out["intent_calls_3m"] = 0.0
        out["last_call_intent"] = np.nan
        out["call_answer_rate_3m"] = np.nan
        out["intent_rate_6m"] = np.nan
        out["days_since_last_call"] = np.nan
        for c_ in _CALL_OBS_NAN:
            out[c_] = np.nan
        for c_ in _CALL_OBS_ZERO:
            out[c_] = 0.0
        return out

    # Restricted to live loans first: a closed loan's calls would otherwise
    # widen the streak index beyond the panel's rows.
    h = _before(calls, "day", t)
    h = h[h.loan_id.isin(idx)]
    c3 = h[h.day >= t - 90]
    c6 = h[h.day >= t - 180]
    out["calls_3m"] = c3.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    n6 = c6.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    a6 = c6[c6.answered].groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    out["call_answer_rate_6m"] = np.round(
        np.where(n6 > 0, a6 / np.maximum(n6, 1), np.nan), 3)

    streak = pd.Series(0.0, index=idx)
    for lid, g in h.groupby("loan_id"):
        # Newest first. One attempt per loan per day in this ledger, so the
        # day is a total order; the adapter sorts on `called_at`.
        ans = g.sort_values("day", ascending=False).answered.to_numpy()
        k = 0
        for a in ans:
            if a:
                break
            k += 1
        streak[lid] = float(k)
    out["no_answer_streak"] = streak.to_numpy()
    answered = h[h.answered]
    last = answered.groupby("loan_id").day.max().reindex(idx)
    out["days_since_last_answered_call"] = (t - last).to_numpy(dtype=float)

    # What the borrower SAID on the phone. `CallLog.payment_intent_signalled`.
    # A count over three months, and the flag on the most recent answered
    # call — the freshest statement of intent there is. NaN where no call was
    # ever answered: nobody has said anything.
    if "payment_intent" in h.columns:
        intent = answered[answered.payment_intent.eq(True)]
        i3 = intent[intent.day >= t - 90]
        out["intent_calls_3m"] = (i3.groupby("loan_id").size()
                                  .reindex(idx).fillna(0).to_numpy(float))
        last_row = (answered.sort_values("day").groupby("loan_id").tail(1)
                    .set_index("loan_id"))
        flag = last_row.payment_intent.eq(True).astype(float)
        out["last_call_intent"] = flag.reindex(idx).to_numpy(dtype=float)
    else:
        out["intent_calls_3m"] = 0.0
        out["last_call_intent"] = np.nan

    # ── 2.1.0 ───────────────────────────────────────────────────────────────
    # Recent answer rate, three months; NaN where nobody dialled.
    n3 = c3.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    a3 = c3[c3.answered].groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    out["call_answer_rate_3m"] = np.round(
        np.where(n3 > 0, a3 / np.maximum(n3, 1), np.nan), 3)
    # Share of ANSWERED calls in six months on which intent was signalled;
    # NaN where no call was answered — nobody has said anything either way.
    if "payment_intent" in h.columns:
        ans6 = c6[c6.answered]
        na6 = ans6.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
        ni6 = (ans6[ans6.payment_intent.eq(True)].groupby("loan_id").size()
               .reindex(idx).fillna(0).to_numpy(float))
        out["intent_rate_6m"] = np.round(
            np.where(na6 > 0, ni6 / np.maximum(na6, 1), np.nan), 3)
    else:
        out["intent_rate_6m"] = np.nan
    # Days since the most recent ATTEMPT, answered or not; NaN if never called.
    last_any = h.groupby("loan_id").day.max().reindex(idx)
    out["days_since_last_call"] = (t - last_any).to_numpy(dtype=float)

    # ── the willingness channels and their recency, 2026-09-15 (later) ────
    # DECLINED: picked up and cut short. Rate over REACHED calls (answered or
    # declined), NaN where nobody picked up. `CallOutcome.DECLINED`.
    has_outcome = "outcome" in h.columns
    declined_all = (h.outcome == "DECLINED") if has_outcome else pd.Series(False, index=h.index)
    for label, days in (("6m", 180), ("90d", 90), ("30d", 30)):
        w = h[h.day >= t - days]
        dec = declined_all.loc[w.index]
        reached = w.answered | dec
        nr = reached.groupby(w.loan_id).sum().reindex(idx).fillna(0).to_numpy(float)
        nd = dec.groupby(w.loan_id).sum().reindex(idx).fillna(0).to_numpy(float)
        out[f"declined_rate_{label}"] = np.round(np.where(nr > 0, nd / np.maximum(nr, 1), np.nan), 3)
    # Recency of the intent flag: 30d / 90d rates, and days since the last
    # positive and the last negative statement. A negative statement is an
    # answered call without intent or a call cut short.
    if "payment_intent" in h.columns:
        for label, days in (("30d", 30), ("90d", 90)):
            w = h[(h.day >= t - days) & h.answered]
            na = w.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
            ni = (w[w.payment_intent.eq(True)].groupby("loan_id").size()
                  .reindex(idx).fillna(0).to_numpy(float))
            out[f"intent_rate_{label}"] = np.round(np.where(na > 0, ni / np.maximum(na, 1), np.nan), 3)
        pos = answered[answered.payment_intent.eq(True)]
        neg = pd.concat([answered[~answered.payment_intent.eq(True)], h[declined_all]])
        out["days_since_positive_intent"] = (t - pos.groupby("loan_id").day.max().reindex(idx)).to_numpy(float)
        out["days_since_negative_intent"] = (t - neg.groupby("loan_id").day.max().reindex(idx)).to_numpy(float)
    else:
        out["intent_rate_30d"] = out["intent_rate_90d"] = np.nan
        out["days_since_positive_intent"] = out["days_since_negative_intent"] = np.nan
    w30 = h[h.day >= t - 30]
    n30 = w30.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    a30 = w30[w30.answered].groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    out["answered_rate_30d"] = np.round(np.where(n30 > 0, a30 / np.maximum(n30, 1), np.nan), 3)
    out["calls_30d"] = n30
    out["answered_calls_30d"] = a30
    # Duration: how long the borrower talked. `CallLog.duration_seconds`.
    if "duration_seconds" in h.columns:
        d = answered.assign(dur=pd.to_numeric(answered.duration_seconds, errors="coerce"))
        d = d[d.dur.notna()]
        for label, days in (("6m", 180), ("30d", 30)):
            w = d[d.day >= t - days]
            out[f"mean_call_duration_{label}"] = np.round(
                w.groupby("loan_id").dur.mean().reindex(idx).to_numpy(dtype=float), 1)
        last_d = d.sort_values("day").groupby("loan_id").dur.last().reindex(idx)
        out["last_call_duration"] = last_d.to_numpy(dtype=float)
    else:
        out["mean_call_duration_6m"] = out["mean_call_duration_30d"] = np.nan
        out["last_call_duration"] = np.nan

    # ── the last three days: a reading taken at scoring time ───────────────
    # Where a pre-scoring sweep runs, most of the pool has one call in
    # [t-3, t); where it does not, these are simply sparse.
    w3d = h[h.day >= t - 3]
    out["calls_3d"] = w3d.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    a3d = w3d[w3d.answered]
    out["answered_3d"] = (a3d.groupby("loan_id").size().reindex(idx).fillna(0) > 0).astype(float).to_numpy()
    dec3 = declined_all.loc[w3d.index] if has_outcome else pd.Series(False, index=w3d.index)
    out["declined_3d"] = (dec3.groupby(w3d.loan_id).sum().reindex(idx).fillna(0) > 0).astype(float).to_numpy()
    if "payment_intent" in h.columns and len(a3d):
        last3 = a3d.sort_values("day").groupby("loan_id").tail(1).set_index("loan_id")
        out["intent_3d"] = last3.payment_intent.eq(True).astype(float).reindex(idx).to_numpy(dtype=float)
    else:
        out["intent_3d"] = np.nan
    if "duration_seconds" in h.columns and len(a3d):
        dd = a3d.assign(dur=pd.to_numeric(a3d.duration_seconds, errors="coerce"))
        out["duration_3d"] = dd.sort_values("day").groupby("loan_id").dur.last().reindex(idx).to_numpy(dtype=float)
    else:
        out["duration_3d"] = np.nan
    # Reached (answered or declined) in the last three days: 1/0; NaN if no
    # attempt was made at all, because "not reached" and "not tried" differ.
    reached3 = (a3d.groupby("loan_id").size().reindex(idx).fillna(0)
                + dec3.groupby(w3d.loan_id).sum().reindex(idx).fillna(0)) > 0
    out["reached_3d"] = np.where(out["calls_3d"] > 0, reached3.astype(float).to_numpy(), np.nan)
    return out


#: Call-channel features that abstain (NaN) / are zero when there are no calls.
_CALL_OBS_NAN = ("intent_3d", "duration_3d", "reached_3d",
                 "declined_rate_6m", "declined_rate_90d", "declined_rate_30d",
                 "intent_rate_30d", "intent_rate_90d", "days_since_positive_intent",
                 "days_since_negative_intent", "answered_rate_30d",
                 "mean_call_duration_6m", "mean_call_duration_30d", "last_call_duration")
_CALL_OBS_ZERO = ("calls_30d", "answered_calls_30d", "calls_3d", "answered_3d", "declined_3d")


def _commitment_history(calls, pays, live, t, emi, cfg) -> pd.DataFrame:
    """Verbal payment commitments, 2026-09-15 (later). `CallLog.verbal_payment_date`.

    A commitment is a call before t on which a date was named. Its status AS
    KNOWN AT t is derived from the payment ledger, never stored:

        KEPT    VERIFIED money (status as known at t) with
                call_day <= payment_day <= due + grace and payment_day < t
                sums to >= commitment_kept_ratio x EMI
        BROKEN  not kept and due + grace < t
        OPEN    otherwise

    The adapter repeats this on CallLog + Payment. Money that arrives early
    keeps the commitment early; a commitment whose grace has not run out is
    neither kept nor broken yet.
    """
    idx = pd.Index(live, name="loan_id")
    out = pd.DataFrame(index=idx)
    cols_nan = ("commit_kept_ratio_6m", "commit_kept_ratio_90d",
                "days_since_commit_kept", "days_since_commit_broken")
    cols_zero = ("commitments_6m", "commitments_90d", "commit_broken_6m",
                 "commit_broken_90d", "commit_kept_90d", "commit_live_at_asof")
    if not len(calls) or "verbal_due_day" not in calls.columns:
        for c_ in cols_nan:
            out[c_] = np.nan
        for c_ in cols_zero:
            out[c_] = 0.0
        out["last_commit_status"] = "NONE"
        return out
    grace = int(getattr(cfg, "commitment_grace_days", 2))
    ratio = float(getattr(cfg, "commitment_kept_ratio", 0.5))
    emi_s = pd.Series(np.asarray(emi, dtype=float), index=idx)

    h = _before(calls, "day", t)
    cm = h[(h.verbal_due_day >= 0) & h.loan_id.isin(idx)][["loan_id", "day", "verbal_due_day"]].copy()
    cm = cm.reset_index(drop=True)
    cm["cid"] = np.arange(len(cm))
    if len(cm) and len(pays):
        ph = _before(pays, "payment_day", t)
        ph = ph[(_status_at(ph, t) == "VERIFIED") & ph.loan_id.isin(idx)][["loan_id", "payment_day", "amount"]]
        m = cm.merge(ph, on="loan_id", how="left")
        ok = (m.payment_day >= m.day) & (m.payment_day <= m.verbal_due_day + grace)
        paid = m[ok].groupby("cid").amount.sum().reindex(cm.cid).fillna(0.0).to_numpy()
    else:
        paid = np.zeros(len(cm))
    cm["paid"] = paid
    cm["kept"] = cm.paid >= ratio * emi_s.reindex(cm.loan_id).to_numpy()
    cm["expired"] = cm.verbal_due_day + grace < t
    cm["status"] = np.where(cm.kept, "KEPT", np.where(cm.expired, "BROKEN", "OPEN"))
    # Resolution day: the day the money completed it (approximated by the
    # last qualifying payment day) or the day grace ran out.
    if len(cm) and len(pays):
        last_pay = m[ok].groupby("cid").payment_day.max().reindex(cm.cid).to_numpy()
    else:
        last_pay = np.full(len(cm), np.nan)
    cm["resolved_day"] = np.where(cm.kept, last_pay,
                                  np.where(cm.expired, cm.verbal_due_day + grace, np.nan))

    for label, days in (("6m", 180), ("90d", 90)):
        w = cm[cm.day >= t - days]
        n = w.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
        res = w[w.status != "OPEN"]
        nres = res.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
        nk = res[res.status == "KEPT"].groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
        nb = res[res.status == "BROKEN"].groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
        out[f"commitments_{label}"] = n
        out[f"commit_broken_{label}"] = nb
        out[f"commit_kept_ratio_{label}"] = np.round(np.where(nres > 0, nk / np.maximum(nres, 1), np.nan), 3)
        if label == "90d":
            out["commit_kept_90d"] = nk
    out["commit_live_at_asof"] = (cm[cm.status == "OPEN"].groupby("loan_id").size()
                                  .reindex(idx).fillna(0) > 0).astype(float).to_numpy()
    kept = cm[cm.status == "KEPT"]
    broken = cm[cm.status == "BROKEN"]
    out["days_since_commit_kept"] = (t - kept.groupby("loan_id").resolved_day.max().reindex(idx)).to_numpy(float)
    out["days_since_commit_broken"] = (t - broken.groupby("loan_id").resolved_day.max().reindex(idx)).to_numpy(float)
    last = cm.sort_values("day").groupby("loan_id").tail(1).set_index("loan_id")
    out["last_commit_status"] = last.status.reindex(idx).fillna("NONE").to_numpy()
    return out


#: Ordinal score of a disposition, for the numeric reading features. HARDSHIP
#: sits with NO_COMMITMENT (a capacity statement, not a stance); DISPUTE with
#: REFUSES (no money is coming while the debt is contested).
DISPOSITION_SCORE = {"WILL_PAY": 2.0, "MAY_PAY": 1.0, "NO_COMMITMENT": 0.0,
                     "HARDSHIP": 0.0, "DISPUTE": -1.0, "REFUSES": -2.0}
_DISP_POSITIVE = ("WILL_PAY", "MAY_PAY")
_DISP_NEGATIVE = ("REFUSES", "DISPUTE")


def _disposition_history(calls, visits, live, t) -> pd.DataFrame:
    """The structured disposition readings, 2026-09-15 (later still).

    Readings from answered calls and met visits STRICTLY BEFORE t, pooled and
    ordered by day (a visit and a call on the same day: the call is taken as
    the later, arbitrarily but consistently). Six features, chosen for
    RECENCY rather than volume:

        latest_disposition         the newest reading, "NONE" if never read
        latest_disposition_score   its ordinal score, NaN if never read
        days_since_disposition     t - day of the newest reading, NaN if never
        disposition_3d             the newest reading in [t-3, t), "NONE"
        positive_disposition_rate_30d / negative_disposition_rate_30d
                                   share of readings in [t-30, t), NaN if none
        disposition_count_30d      readings in [t-30, t), 0 if none
        disposition_trend_90d      latest score minus the mean of the earlier
                                   readings in [t-90, t); NaN below 2 readings
    """
    idx = pd.Index(live, name="loan_id")
    out = pd.DataFrame(index=idx)
    parts = []
    if len(calls) and "disposition" in calls.columns:
        parts.append(calls[calls.disposition.notna()][["loan_id", "day", "disposition"]].assign(src=1))
    if len(visits) and "disposition" in visits.columns:
        parts.append(visits[visits.disposition.notna()][["loan_id", "day", "disposition"]].assign(src=0))
    if not parts:
        out["latest_disposition"] = "NONE"; out["disposition_3d"] = "NONE"
        out["disposition_7d"] = "NONE"; out["disposition_30d"] = "NONE"
        out["fresh_positive_disposition"] = 0.0; out["fresh_negative_disposition"] = 0.0
        out["disposition_recency_class"] = "NONE"; out["disposition_score_decayed"] = np.nan
        for c_ in ("latest_disposition_score", "days_since_disposition",
                   "positive_disposition_rate_30d", "negative_disposition_rate_30d",
                   "disposition_trend_90d"):
            out[c_] = np.nan
        out["disposition_count_30d"] = 0.0
        return out
    r = pd.concat(parts, ignore_index=True)
    r = r[(r.day < t) & r.loan_id.isin(idx)].sort_values(["day", "src"])
    r["score"] = r.disposition.map(DISPOSITION_SCORE).astype(float)
    last = r.groupby("loan_id").tail(1).set_index("loan_id")
    out["latest_disposition"] = last.disposition.reindex(idx).fillna("NONE").to_numpy()
    out["latest_disposition_score"] = last.score.reindex(idx).to_numpy(dtype=float)
    out["days_since_disposition"] = (t - last.day.reindex(idx)).to_numpy(dtype=float)
    for label, days in (("3d", 3), ("7d", 7), ("30d", 30)):
        rw = r[r.day >= t - days]
        lw = rw.groupby("loan_id").tail(1).set_index("loan_id")
        out[f"disposition_{label}"] = lw.disposition.reindex(idx).fillna("NONE").to_numpy()
    # A positive / negative stance recorded in the last 7 days: 1/0, and 0
    # where nothing was recorded — "no fresh positive signal" is an
    # observation, not a gap.
    r7 = r[r.day >= t - 7]
    out["fresh_positive_disposition"] = (r7[r7.disposition.isin(_DISP_POSITIVE)].groupby("loan_id").size()
                                         .reindex(idx).fillna(0) > 0).astype(float).to_numpy()
    out["fresh_negative_disposition"] = (r7[r7.disposition.isin(_DISP_NEGATIVE)].groupby("loan_id").size()
                                         .reindex(idx).fillna(0) > 0).astype(float).to_numpy()
    r30 = r[r.day >= t - 30]
    n30 = r30.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    pos = r30[r30.disposition.isin(_DISP_POSITIVE)].groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    neg = r30[r30.disposition.isin(_DISP_NEGATIVE)].groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    out["positive_disposition_rate_30d"] = np.round(np.where(n30 > 0, pos / np.maximum(n30, 1), np.nan), 3)
    out["negative_disposition_rate_30d"] = np.round(np.where(n30 > 0, neg / np.maximum(n30, 1), np.nan), 3)
    out["disposition_count_30d"] = n30
    r90 = r[r.day >= t - 90]
    g = r90.groupby("loan_id")
    n90 = g.size()
    prior_mean = (g.score.sum() - g.score.last()) / (n90 - 1).replace(0, np.nan)
    trend = (g.score.last() - prior_mean).where(n90 >= 2)
    out["disposition_trend_90d"] = np.round(trend.reindex(idx).to_numpy(dtype=float), 3)
    # Freshness-aware forms of the same reading, for a scorecard that cannot
    # interact a class with its age: the class split FRESH (<= 7 days) /
    # STALE, and the ordinal score halved every 14 days. Both are business
    # constants, not properties of the world.
    out["disposition_recency_class"] = _recency_class(out["latest_disposition"].to_numpy(),
                                                      out["days_since_disposition"].to_numpy())
    out["disposition_score_decayed"] = np.round(
        out["latest_disposition_score"].to_numpy() * np.power(0.5, out["days_since_disposition"].to_numpy() / 14.0), 3)
    return out


def _recency_class(cls: np.ndarray, days: np.ndarray, fresh_days: int = 7) -> np.ndarray:
    out = np.array(cls, dtype=object).copy()
    has = out != "NONE"
    out[has] = np.where(days[has] <= fresh_days, out[has].astype(str) + "_FRESH", out[has].astype(str) + "_STALE")
    return out


def _hostile_at(flags, live, t) -> np.ndarray:
    """1 where a HOSTILE flag event was raised STRICTLY BEFORE day t."""
    if not len(flags):
        return np.zeros(len(live), dtype=int)
    h = flags[(flags.flag == "HOSTILE") & (flags.day < t)]
    raised = set(h.loan_id)
    return np.array([1 if lid in raised else 0 for lid in live], dtype=int)


def _ptp_history(ptps, live, t, emi=None) -> pd.DataFrame:
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
        out["ptp_broken_6m"] = 0.0
        out["ptp_rescheduled_6m"] = 0.0
        out["ptp_amount_to_emi"] = np.nan
        out["recent_ptp_status"] = "NONE"
        out["ptp_conversion_90d"] = np.nan
        return out

    h = _before(ptps, "created_day", t, lookback=180)
    n_set = h.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    resolved = h[(h.resolved_day >= 0) & (h.resolved_day < t)]
    kept = resolved[resolved.resolved_status == "HONORED"]
    n_kept = kept.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    out["ptp_set_6m"] = n_set
    out["ptp_kept_6m"] = n_kept
    out["ptp_kept_ratio"] = np.round(
        np.where(n_set > 0, n_kept / np.maximum(n_set, 1), 0.5), 3)

    # ── how promises END, and how big they were, 2026-09-15 ────────────────
    # Same window, same "resolved before t" rule as kept. A promise still open
    # at t is neither kept nor broken yet.
    for label, status in (("ptp_broken_6m", "BROKEN"),
                          ("ptp_rescheduled_6m", "RESCHEDULED")):
        n = (resolved[resolved.resolved_status == status]
             .groupby("loan_id").size().reindex(idx).fillna(0))
        out[label] = n.to_numpy(float)
    if emi is not None:
        mean_amt = h.groupby("loan_id").committed_amount.mean().reindex(idx)
        out["ptp_amount_to_emi"] = np.round(
            (mean_amt / np.maximum(pd.Series(emi, index=idx), 1.0)).to_numpy(float), 3)
    else:
        out["ptp_amount_to_emi"] = np.nan

    # ── recency, 2026-09-15 (later) ────────────────────────────────────────
    # Status of the most recent promise AS KNOWN at t, and the 90-day
    # kept-over-set conversion.
    allp = _before(ptps, "created_day", t)
    allp = allp[allp.loan_id.isin(idx)]
    if len(allp):
        lastp = allp.sort_values("created_day").groupby("loan_id").tail(1).set_index("loan_id")
        known = (lastp.resolved_day >= 0) & (lastp.resolved_day < t)
        st = np.where(known, lastp.resolved_status.astype(str), "OPEN")
        out["recent_ptp_status"] = pd.Series(st, index=lastp.index).reindex(idx).fillna("NONE").to_numpy()
    else:
        out["recent_ptp_status"] = "NONE"
    h90 = h[h.created_day >= t - 90]
    n90 = h90.groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float)
    k90 = (h90[(h90.resolved_day >= 0) & (h90.resolved_day < t) & (h90.resolved_status == "HONORED")]
           .groupby("loan_id").size().reindex(idx).fillna(0).to_numpy(float))
    out["ptp_conversion_90d"] = np.round(np.where(n90 > 0, k90 / np.maximum(n90, 1), np.nan), 3)
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
    w = w[_status_at(w, hi, inclusive=True) == "VERIFIED"]
    return w.groupby("loan_id").amount.sum().reindex(idx).fillna(0.0).to_numpy()
