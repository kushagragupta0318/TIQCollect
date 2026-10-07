"""E06: a 13-week cash forecast (plan §7, STANDALONE-TASKS E06).

Two legs, reconciled, exactly as the task names them:

  - TOP-DOWN: Holt's linear-trend ETS fit on the bank's own weekly VERIFIED
    collections (`read_weekly_payments`), hand-rolled (no statsmodels — the
    recursion is eight lines; see `_holt_fit`). Smoothing constants are chosen
    by a small grid search minimising one-step-ahead SSE, so "ETS" is not a
    label pasted on a guess.
  - BOTTOM-UP: the book's own known commitments — ACTIVE PTPs due in the next
    13 weeks, de-rated by the bank's own historical PTP honor rate
    (`read_ptp_schedule` / `read_ptp_honor_rate`) — plus, for loans with NO
    active PTP, what the recovery_risk model already says about the next
    cycle (`read_recovery_risk_next_cycle`): (1 - P(no material payment)) x
    overdue_amount, spread over the model's own ~monthly horizon and NOT
    claimed for the remaining weeks. Excluding PTP'd loans from this term is
    what keeps the two commitments from being counted twice.

  `reconcile()` blends the two legs; when the book has no PTPs and no
  recovery_risk coverage at all, the blend degrades to the ETS leg alone
  rather than silently blending in zeros as if they were evidence.

Bands (p10/p50/p90) come from the EMPIRICAL quantiles of the ETS's own
one-step-ahead residuals, widened by sqrt(h) for an h-week-ahead point — the
standard random-walk growth rate for forecast uncertainty, not a tuned
parameter. `rolling_origin_backtest` scores this honestly: it re-forecasts
from several past origins the book itself lived through and reports the MAPE
against what actually happened, which is the "forecast-vs-actual tracker" the
task asks for and the source of the `calibrated` flag in the honesty stamp
(ADR 0014 — this is a forecast on a SYNTHETIC book, never claimed as
real-world accuracy; strategy/honesty.py.stamp_for_cash_forecast prints the
caveat beside every figure).

ADR 0005 (abstain rather than impute): a book under MIN_WEEKS_HISTORY weeks of
VERIFIED payments — including a book with none at all — gets
AppException(INSUFFICIENT_HISTORY), never a forecast built on invented history.
A thin PTP or recovery_risk signal is not the same failure: both are real
(possibly zero) facts about the book today, so they degrade their own term to
zero and are reported as zero, not abstained.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import AppException, ErrorCode

# cf-1.0.0 = 128a46e. cf-1.1.0 (2026-10-07): residual_bands no longer shifts
# p50 by a growing, possibly-negative bias (see its docstring); a trailing
# reporting-lag gap is trimmed before fitting (trim_trailing_reporting_lag).
ENGINE_VERSION = "cf-1.1.0"

HORIZON_WEEKS = 13
MIN_WEEKS_HISTORY = 8          # shortest span Holt's trend is fit on at all
HISTORY_WEEKS_BACK = 52        # how far back read_weekly_payments looks
PTP_HONOR_LOOKBACK_WEEKS = 26
RECOVERY_RISK_RECENCY_DAYS = 45
# The model's own stated horizon is "next cycle" (~1 month); its contribution
# is spread over this many weeks of the 13 and is exactly 0 after that — the
# model is never stretched to cover weeks it was not scored for.
RECOVERY_RISK_CYCLE_WEEKS = 4
# Below this, even a successful fold's MAPE does not earn "calibrated": a
# bank whose book barely supports one fold should not get a confident stamp
# from it. Chosen, not fitted — the same spirit as transitions.py's MIN_MONTHS.
MIN_BACKTEST_FOLDS = 2
# How many trailing all-zero weeks trim_trailing_reporting_lag will treat as
# "not yet reported" rather than "collections stopped". Bounded: a gap this
# short is a plausible ingest lag (CLAUDE.md — scripts/ingest_daily.py is run
# by hand, not scheduled); a longer run of zeros is left in the fit rather
# than trimmed without limit, since nothing in the data distinguishes a long
# lag from a book that genuinely went quiet.
MAX_REPORTING_LAG_WEEKS = 6
# A nominal bar for "the point forecast tracks what happened", not a
# statistical guarantee. Stated so a reviewer can see exactly what calibrated
# means here rather than reverse-engineering it from the code.
CALIBRATION_MAPE_CEILING = 0.50


# ── Pure engine: Holt's linear trend, by hand ────────────────────────────────

def _holt_fit(y: np.ndarray, alpha: float, beta: float) -> tuple[float, float, np.ndarray]:
    """One-step-ahead fitted values under Holt's linear trend, and the final
    (level, trend) to forecast forward from. fitted[0] is undefined (no prior
    state) and left as y[0] so residuals[0] == 0 rather than NaN; every other
    fitted[t] is level/trend as they stood BEFORE seeing y[t], so this is a
    true one-step forecast, not a smoothed fit of the whole series at once."""
    n = y.shape[0]
    level = float(y[0])
    trend = float(y[1] - y[0]) if n > 1 else 0.0
    fitted = np.empty(n, dtype=np.float64)
    fitted[0] = level
    for t in range(1, n):
        forecast = level + trend
        fitted[t] = forecast
        obs = float(y[t])
        new_level = alpha * obs + (1 - alpha) * (level + trend)
        new_trend = beta * (new_level - level) + (1 - beta) * trend
        level, trend = new_level, new_trend
    return level, trend, fitted


def fit_holt(y: np.ndarray) -> tuple[float, float, np.ndarray, float, float]:
    """Grid-search (alpha, beta) minimising one-step SSE over y[1:] (y[0] has
    no prior state to score). Returns (level, trend, residuals, alpha, beta)
    so a caller can report which constants were actually used — nothing here
    is a fixed, undocumented guess."""
    grid = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
    best: tuple[float, float, np.ndarray, float, float] | None = None
    best_sse = np.inf
    for a in grid:
        for b in grid:
            level, trend, fitted = _holt_fit(y, a, b)
            resid = y[1:] - fitted[1:]
            sse = float(np.sum(resid * resid))
            if sse < best_sse:
                best_sse = sse
                best = (level, trend, fitted, a, b)
    assert best is not None  # grid is non-empty
    level, trend, fitted, a, b = best
    residuals = y[1:] - fitted[1:]
    return level, trend, residuals, a, b


def trim_trailing_reporting_lag(amounts: np.ndarray) -> tuple[np.ndarray, int]:
    """Drop a trailing run of EXACT zero weeks before fitting, up to
    MAX_REPORTING_LAG_WEEKS and never below MIN_WEEKS_HISTORY remaining.

    2026-10-07 (demo-visible): `read_weekly_payments` buckets every week up
    to `as_of` on a fixed 7-day grid, so the one or two most recent weeks
    are routinely a real reporting lag (the bank-file ingest is run by
    hand, not scheduled) rather than a real stop in collections — but the
    bucketing cannot tell the difference on its own, and a naive fit reads
    "no rows yet" as "collections went to zero", dragging Holt's level and
    trend down to match. Trimming only EXACT zeros (never a merely low
    week) and only at the very end (never a zero week buried in the
    middle, which IS a real zero) is the one distinction the data actually
    supports; it is still a stated assumption, not a certainty, which is
    why the trimmed count travels with the run (`reporting_lag_weeks`) and
    into the basis string rather than being silently absorbed."""
    n = amounts.shape[0]
    cap = min(MAX_REPORTING_LAG_WEEKS, n - MIN_WEEKS_HISTORY)
    lag = 0
    while lag < cap and amounts[n - 1 - lag] == 0.0:
        lag += 1
    return (amounts[: n - lag], lag) if lag else (amounts, 0)


def forecast_holt(level: float, trend: float, horizon: int) -> np.ndarray:
    """h-step-ahead point forecasts, h = 1..horizon. Collections cannot be
    negative, so the point forecast is floored at 0 (a strong downward trend
    extrapolated 13 weeks out can otherwise cross zero)."""
    h = np.arange(1, horizon + 1, dtype=np.float64)
    return np.maximum(level + h * trend, 0.0)


def residual_bands(residuals: np.ndarray, point: np.ndarray, *, start_h: int = 1) -> dict[str, np.ndarray]:
    """p10/p90 spread OUT from `point` by sqrt(h) on each side; p50 IS `point`,
    always — never shifted.

    2026-10-07 (demo-visible, found on the live book): the first version set
    `p50 = point + median(residuals) * sqrt(h)`, i.e. it shifted the CENTRE
    line by the model's own one-step bias, growing with the horizon. On a
    book whose recent weeks under-ran the model's fit (residuals skew
    negative — exactly what a few weeks of reporting lag produces, see
    read_weekly_payments), that shift compounded with h until it crossed
    zero and every band — p10, p50, and eventually even p90, since the
    SAME growing negative shift was applied to all three — clipped flat to
    0 for the rest of the horizon. A one-step bias is a fact about the
    FITTING window; stretching it forward as if it were the forecast's own
    growing error is a different claim the data never supported, and it
    reads as "the forecast says zero" when the engine never computed zero.

    The spread is now built from the two one-sided distances off the
    residual MEDIAN (lower = median - p10, upper = p90 - median), each
    non-negative by construction, so p10 <= point <= p90 always holds
    without a clip, and `point` itself — already floored at 0 by
    forecast_holt/reconcile — is never pulled down by a biased tail.

    `start_h`: the first point is `start_h` steps past the fitting window,
    not necessarily 1 — trim_trailing_reporting_lag moves the fit's own
    origin back by the lag it trims, and the band width must keep growing
    from THAT origin, not reset to h=1 as if the forecast started fresh at
    today (a reporting gap narrows the uncertainty of nothing)."""
    if residuals.size == 0:
        return {"p10": point.copy(), "p50": point.copy(), "p90": point.copy()}
    r10, r50, r90 = np.percentile(residuals, [10, 50, 90])
    lower_spread = max(r50 - r10, 0.0)
    upper_spread = max(r90 - r50, 0.0)
    h = np.arange(start_h, start_h + point.shape[0], dtype=np.float64)
    scale = np.sqrt(h)
    return {
        "p10": np.maximum(point - lower_spread * scale, 0.0),
        "p50": point,
        "p90": point + upper_spread * scale,
    }


def bootstrap_total_bands(residuals: np.ndarray, point: np.ndarray, *, start_h: int = 1,
                          n_boot: int = 2000, seed: int = 0) -> dict[str, float]:
    """The p10/p50/p90 of the 13-week SUM — NOT the sum of each week's own
    p10/p50/p90 (coordinator/audit finding, 2026-10-07).

    Summing per-week percentiles overstates the total's tails: p10 of the
    sum is not sum(p10_i), because that would require every week to land at
    its own p10 simultaneously, which is far less likely than 10% once there
    is more than one week (assuming the weeks' errors are independent —
    Var(sum) = sum(Var_i), so std(sum) = sqrt(sum(std_i^2)) < sum(std_i) by
    the triangle inequality whenever more than one term is nonzero). The
    honest total band is therefore NARROWER than the naive per-week sum, not
    wider — this corrects an overstated range, not an understated one.

    Implementation: resample the one-step residual pool independently for
    each week (same pool `residual_bands` reads its quantiles from), scale
    by that week's own sqrt(h), add to the point forecast, floor at 0, sum
    across the horizon, repeat `n_boot` times, and read percentiles off
    those sums. Independence across weeks is a stated simplifying
    assumption — a real book's week-to-week errors likely correlate
    somewhat, which would widen the true band back out, so this is an
    optimistic (narrower) bound, not a guaranteed one. Deterministic by
    default (fixed seed) so a run is reproducible."""
    total_point = float(point.sum())
    if residuals.size == 0:
        return {"p10": total_point, "p50": total_point, "p90": total_point}
    rng = np.random.default_rng(seed)
    h = np.arange(start_h, start_h + point.shape[0], dtype=np.float64)
    scale = np.sqrt(h)
    draws = rng.choice(residuals, size=(n_boot, point.shape[0]), replace=True)
    paths = np.maximum(point[None, :] + draws * scale[None, :], 0.0)
    totals = paths.sum(axis=1)
    # The bootstrap median need not equal `total_point` exactly (the 0-floor
    # skews the distribution whenever some weeks are near zero) -- reported
    # as its own honest figure, not silently replaced by the point sum, same
    # as residual_bands lets the upper/lower spreads differ from each other.
    p10, p50, p90 = (float(x) for x in np.percentile(totals, [10, 50, 90]))
    return {"p10": p10, "p50": p50, "p90": p90}


def reconcile(bottom_up: np.ndarray, top_down: np.ndarray) -> np.ndarray:
    """Equal-weight blend when the book has a bottom-up signal AT ALL, over
    the WHOLE horizon; otherwise the ETS leg alone.

    The 50/50 weight is applied per week, uniformly, once any week has a
    bottom-up figure — including every week with its own `bottom_up[h] == 0`
    (past the PTP horizon, or past RECOVERY_RISK_CYCLE_WEEKS). Those weeks
    are NOT switched to the ETS leg alone; they become exactly
    `0.5 * top_down[h]`, i.e. the point forecast is halved precisely because
    the bottom-up method had nothing to say about that particular week, not
    because it said zero was expected. This is a stated, fixed weighting
    choice (not fitted, not week-varying), and it is why `bottom_up` and
    `top_down` ride alongside the bands in the API response and the UI
    table — a reader comparing a week's p50 against `top_down` for that
    same week can see the halving directly rather than inferring it.

    A silent 50/50 blend against an ALL-WEEKS-zero bottom-up (no PTPs, no
    recovery_risk coverage anywhere in the horizon) would read as 'the
    bottom-up method says half of the top-down number', which is not a
    method, it is an unlabelled halving of someone else's forecast — that
    all-zero case is the one `reconcile` refuses, falling back to the ETS
    leg alone."""
    if float(bottom_up.sum()) <= 0.0:
        return top_down.copy()
    return 0.5 * bottom_up + 0.5 * top_down


@dataclass(frozen=True)
class BacktestResult:
    mape: float | None
    n_folds: int
    fold_origins: tuple[int, ...]
    calibrated: bool
    reason: str = ""

    def as_dict(self) -> dict:
        return {"mape": self.mape, "n_folds": self.n_folds, "calibrated": self.calibrated,
                "reason": self.reason, "calibration_ceiling": CALIBRATION_MAPE_CEILING,
                "min_folds": MIN_BACKTEST_FOLDS}


def rolling_origin_backtest(y: np.ndarray, horizon: int = HORIZON_WEEKS,
                            fold_step: int = 4) -> BacktestResult:
    """Walk-forward MAPE: at each origin with >= MIN_WEEKS_HISTORY weeks
    behind it and >= horizon weeks of real outcome ahead of it, fit Holt on
    the weeks before the origin only (no look-ahead), forecast forward, and
    score against what the book actually collected. This is the
    forecast-vs-actual tracker E06 asks for, run against history the book
    already has rather than waiting 13 weeks for the first live comparison."""
    n = y.shape[0]
    origins = list(range(MIN_WEEKS_HISTORY, n - horizon + 1, fold_step))
    if not origins:
        return BacktestResult(mape=None, n_folds=0, fold_origins=(), calibrated=False,
                              reason=f"book has {n} week(s) of history; a backtest fold needs "
                                     f"{MIN_WEEKS_HISTORY} to fit on plus {horizon} to score against")
    errors: list[float] = []
    for origin in origins:
        level, trend, _resid, _a, _b = fit_holt(y[:origin])
        point = forecast_holt(level, trend, horizon)
        actual = y[origin:origin + horizon]
        # Weeks the book genuinely collected nothing make a percentage error
        # undefined, not large; excluded rather than inflating the score with
        # a divide-by-near-zero.
        nonzero = actual > 1e-6
        if nonzero.any():
            errors.extend((np.abs(point[nonzero] - actual[nonzero]) / actual[nonzero]).tolist())
    if not errors:
        return BacktestResult(mape=None, n_folds=len(origins), fold_origins=tuple(origins),
                              calibrated=False,
                              reason="every scored week was zero collections; MAPE is undefined")
    mape = float(np.mean(errors))
    calibrated = len(origins) >= MIN_BACKTEST_FOLDS and mape <= CALIBRATION_MAPE_CEILING
    # Named even on success: a consumer reading only `reason` should see the
    # bar that was cleared, not just the bar that was missed.
    reason = (
        f"only {len(origins)} fold(s), need {MIN_BACKTEST_FOLDS}" if len(origins) < MIN_BACKTEST_FOLDS
        else f"MAPE {mape:.0%} exceeds the {CALIBRATION_MAPE_CEILING:.0%} ceiling" if not calibrated
        else f"MAPE {mape:.0%} within the {CALIBRATION_MAPE_CEILING:.0%} ceiling")
    return BacktestResult(mape=mape, n_folds=len(origins), fold_origins=tuple(origins),
                          calibrated=calibrated, reason=reason)


# ── DB reads: tenant-scoped, pure-logic-testable (fake session + rows) ──────

@dataclass(frozen=True)
class WeeklyHistory:
    week_starts: tuple[date, ...]     # chronological, oldest first, each 7 days before the next
    amounts: np.ndarray               # same length; VERIFIED payments total in that week


_PAYMENTS_SQL = text("""
    SELECT payment_date, amount
      FROM collections.payments
     WHERE bank_id = :bank
       AND status = 'VERIFIED'
       AND payment_date >= :start
       AND payment_date < :as_of
     ORDER BY payment_date
""")


def read_weekly_payments(adb: Session, bank_id: str, *, as_of: date | None = None,
                         weeks_back: int = HISTORY_WEEKS_BACK) -> WeeklyHistory:
    """The bank's weekly VERIFIED collections, bucketed as `weeks_back` fixed
    7-day windows counting backward from `as_of` (NOT calendar weeks) so the
    horizon's week 1 — [as_of, as_of + 7) — sits on exactly the same grid the
    history was fit on; a calendar-week boundary falling mid-window would
    silently misalign the two.

    `adb` must be the tenant-bound analytics session; `bank_id` is also named
    in the query so an unbound session yields nothing rather than another
    bank's rows. Raises INSUFFICIENT_HISTORY when there are no VERIFIED
    payments at all, or fewer than MIN_WEEKS_HISTORY weeks of them — a
    forecast fit on less would be a trend line through noise.
    """
    as_of = as_of or date.today()
    start = as_of - timedelta(weeks=weeks_back)
    rows = adb.execute(_PAYMENTS_SQL, {"bank": bank_id, "start": start, "as_of": as_of}).all()
    if not rows:
        raise AppException(422, ErrorCode.INSUFFICIENT_HISTORY,
                           "This bank has no VERIFIED payment history; there is nothing to "
                           "project a cash forecast from.")

    max_bucket = 0
    totals: dict[int, float] = {}
    for payment_date, amount in rows:
        pd = payment_date.date() if hasattr(payment_date, "date") else payment_date
        bucket = (as_of - pd).days // 7
        if bucket < 0 or bucket >= weeks_back:
            continue
        # bucket 0 = the most recent complete week; flip to chronological below.
        totals[bucket] = totals.get(bucket, 0.0) + float(amount)
        max_bucket = max(max_bucket, bucket)

    n_weeks = max_bucket + 1
    if n_weeks < MIN_WEEKS_HISTORY:
        raise AppException(
            422, ErrorCode.INSUFFICIENT_HISTORY,
            f"This bank has {n_weeks} week(s) of VERIFIED payment history; "
            f"{MIN_WEEKS_HISTORY} are needed to fit a trend. No forecast is produced: "
            "a projection on less would be arithmetic on an assumption.")

    amounts = np.array([totals.get(b, 0.0) for b in range(max_bucket, -1, -1)], dtype=np.float64)
    week_starts = tuple(as_of - timedelta(weeks=b + 1) for b in range(max_bucket, -1, -1))
    return WeeklyHistory(week_starts=week_starts, amounts=amounts)


_PTP_SCHEDULE_SQL = text("""
    SELECT committed_date, committed_amount
      FROM collections.ptps
     WHERE bank_id = :bank
       AND status = 'ACTIVE'
       AND committed_date >= :as_of
       AND committed_date < :end
""")


def read_ptp_schedule(adb: Session, bank_id: str, *, as_of: date | None = None,
                      horizon_weeks: int = HORIZON_WEEKS) -> np.ndarray:
    """ACTIVE PTPs due in the next `horizon_weeks`, bucketed on the same
    as_of-anchored 7-day grid as the horizon forecast. Raw committed amounts —
    the honor-rate de-rating is applied by the caller, not here, so this
    function answers one question only: what has the borrower actually
    promised."""
    as_of = as_of or date.today()
    end = as_of + timedelta(weeks=horizon_weeks)
    rows = adb.execute(_PTP_SCHEDULE_SQL, {"bank": bank_id, "as_of": as_of, "end": end}).all()
    scheduled = np.zeros(horizon_weeks, dtype=np.float64)
    for committed_date, committed_amount in rows:
        cd = committed_date.date() if hasattr(committed_date, "date") else committed_date
        week = min((cd - as_of).days // 7, horizon_weeks - 1)
        scheduled[max(week, 0)] += float(committed_amount)
    return scheduled


_PTP_HONOR_SQL = text("""
    SELECT status, committed_amount, actual_paid_amount
      FROM collections.ptps
     WHERE bank_id = :bank
       AND status IN ('HONORED', 'PARTIALLY_HONORED', 'BROKEN', 'EXPIRED')
       AND committed_date >= :start
       AND committed_date < :as_of
""")


def read_ptp_honor_rate(adb: Session, bank_id: str, *, as_of: date | None = None,
                        lookback_weeks: int = PTP_HONOR_LOOKBACK_WEEKS) -> tuple[float | None, int]:
    """The bank's own rupee-weighted honor rate over resolved PTPs in the
    lookback window: HONORED counts its full committed_amount, PARTIALLY_HONORED
    counts what was actually paid, BROKEN/EXPIRED count 0. (None, 0) when no
    PTP has resolved yet in the window — there is nothing to de-rate the
    schedule by, and the caller treats that as 'no bottom-up PTP signal', not
    as a rate of 1.0 or 0.0, either of which would be invented."""
    as_of = as_of or date.today()
    start = as_of - timedelta(weeks=lookback_weeks)
    rows = adb.execute(_PTP_HONOR_SQL, {"bank": bank_id, "start": start, "as_of": as_of}).all()
    committed_total = paid_total = 0.0
    for status, committed_amount, actual_paid_amount in rows:
        committed_total += float(committed_amount)
        if status == "HONORED":
            paid_total += float(committed_amount)
        elif status == "PARTIALLY_HONORED":
            paid_total += float(actual_paid_amount or 0.0)
    if committed_total <= 0.0:
        return None, 0
    return paid_total / committed_total, len(rows)


_RECOVERY_RISK_SQL = text("""
    SELECT mp.loan_id, mp.probability, l.overdue_amount
      FROM ml.model_predictions mp
      JOIN lending.loans l ON l.id = mp.loan_id
     WHERE mp.bank_id = :bank
       AND mp.model_name = 'recovery_risk'
       AND mp.is_modelled = TRUE
       AND mp.probability IS NOT NULL
       AND mp.as_of_date >= :recency_start
       AND l.status = 'ACTIVE'
       AND l.overdue_amount > 0
       AND NOT EXISTS (
             -- ptps has no loan_id of its own: it hangs off the case, so the
             -- loan a PTP belongs to is reached through collections.cases.
             SELECT 1 FROM collections.ptps p
              JOIN collections.cases c ON c.id = p.case_id
              WHERE c.loan_id = mp.loan_id
                AND p.status = 'ACTIVE'
                AND p.committed_date >= :as_of
                AND p.committed_date < :horizon_end
           )
     ORDER BY mp.loan_id, mp.as_of_date DESC, mp.scored_at DESC
""")


def read_recovery_risk_next_cycle(adb: Session, bank_id: str, *, as_of: date | None = None,
                                  recency_days: int = RECOVERY_RISK_RECENCY_DAYS,
                                  horizon_weeks: int = HORIZON_WEEKS) -> tuple[float, int]:
    """What the recovery_risk model already says is likely next cycle, for
    loans with NO active PTP in the forecast window (the SQL excludes them,
    so this term and the PTP term can never both claim the same loan).

    `mp.probability` is P(no material payment) (ml/pipeline/config.py's y=1
    convention), so P(pay) = 1 - probability; monetised against the loan's
    own overdue_amount (what is actually delinquent), not total_outstanding
    (which includes money not yet due). The latest prediction per loan is
    kept by reading rows ordered newest-first and taking the first one seen
    per loan_id in Python — simpler than a window function and portable to
    the SQLite test harness, which has no `DISTINCT ON`.

    Returns (total expected INR, loans counted). (0.0, 0) is a normal,
    honest answer — a book with no live recovery_risk coverage for
    PTP-free loans contributes nothing from this leg, which is not the
    same failure as having no payment history at all.
    """
    as_of = as_of or date.today()
    recency_start = as_of - timedelta(days=recency_days)
    horizon_end = as_of + timedelta(weeks=horizon_weeks)
    rows = adb.execute(_RECOVERY_RISK_SQL, {
        "bank": bank_id, "recency_start": recency_start,
        "as_of": as_of, "horizon_end": horizon_end,
    }).all()
    seen: set[str] = set()
    total = 0.0
    for loan_id, probability, overdue_amount in rows:
        if loan_id in seen:
            continue
        seen.add(loan_id)
        total += (1.0 - float(probability)) * float(overdue_amount)
    return total, len(seen)


# ── Orchestration ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CashForecastRun:
    week_starts: tuple[date, ...]        # horizon_weeks future weeks, chronological
    p10: tuple[float, ...]
    p50: tuple[float, ...]
    p90: tuple[float, ...]
    ptp_scheduled: tuple[float, ...]      # raw, informational
    bottom_up: tuple[float, ...]          # de-rated PTP + recovery_risk, informational
    top_down: tuple[float, ...]           # the ETS leg alone, informational
    ptp_honor_rate: float | None
    ptp_resolved_count: int
    recovery_informed_total: float
    recovery_informed_loans: int
    history_weeks: int
    reporting_lag_weeks: int
    alpha: float
    beta: float
    backtest: BacktestResult
    residuals: tuple[float, ...]          # the ETS fit's one-step residuals; totals() bootstraps from these
    engine_version: str = ENGINE_VERSION

    def totals(self) -> dict[str, float]:
        """The 13-week total's OWN p10/p50/p90 (bootstrap_total_bands), not
        the sum of each week's own band — see that function for why summing
        percentiles overstates the total's tails."""
        return bootstrap_total_bands(np.array(self.residuals), np.array(self.p50),
                                     start_h=self.reporting_lag_weeks + 1)


def build_cash_forecast(adb: Session, bank_id: str, *, as_of: date | None = None,
                        horizon_weeks: int = HORIZON_WEEKS) -> CashForecastRun:
    """Read the book, fit the ETS leg, build the bottom-up leg, reconcile,
    band, and backtest. Raises AppException(INSUFFICIENT_HISTORY) — via
    read_weekly_payments — on a book too thin to fit at all; that is the only
    abstain condition (PTP and recovery_risk coverage degrade to zero instead,
    see their own docstrings)."""
    as_of = as_of or date.today()
    history = read_weekly_payments(adb, bank_id, as_of=as_of)
    fit_amounts, lag_weeks = trim_trailing_reporting_lag(history.amounts)
    level, trend, residuals, alpha, beta = fit_holt(fit_amounts)
    # The fit's own origin is `lag_weeks` before as_of: ask for that many
    # extra steps and drop them, so the kept horizon is real calendar weeks
    # from as_of, not from wherever the trimmed series happened to end.
    top_down = forecast_holt(level, trend, horizon_weeks + lag_weeks)[lag_weeks:]

    ptp_scheduled = read_ptp_schedule(adb, bank_id, as_of=as_of, horizon_weeks=horizon_weeks)
    honor_rate, resolved_n = read_ptp_honor_rate(adb, bank_id, as_of=as_of)
    ptp_expected = ptp_scheduled * (honor_rate if honor_rate is not None else 0.0)

    recovery_total, recovery_loans = read_recovery_risk_next_cycle(
        adb, bank_id, as_of=as_of, horizon_weeks=horizon_weeks)
    cycle_weeks = min(RECOVERY_RISK_CYCLE_WEEKS, horizon_weeks)
    recovery_per_week = np.zeros(horizon_weeks, dtype=np.float64)
    if cycle_weeks > 0:
        recovery_per_week[:cycle_weeks] = recovery_total / cycle_weeks

    bottom_up = ptp_expected + recovery_per_week
    reconciled = reconcile(bottom_up, top_down)
    bands = residual_bands(residuals, reconciled, start_h=lag_weeks + 1)

    backtest = rolling_origin_backtest(history.amounts, horizon=horizon_weeks)
    week_starts = tuple(as_of + timedelta(weeks=i) for i in range(horizon_weeks))

    return CashForecastRun(
        week_starts=week_starts,
        p10=tuple(bands["p10"].tolist()), p50=tuple(bands["p50"].tolist()),
        p90=tuple(bands["p90"].tolist()),
        ptp_scheduled=tuple(ptp_scheduled.tolist()), bottom_up=tuple(bottom_up.tolist()),
        top_down=tuple(top_down.tolist()),
        ptp_honor_rate=honor_rate, ptp_resolved_count=resolved_n,
        recovery_informed_total=recovery_total, recovery_informed_loans=recovery_loans,
        history_weeks=int(history.amounts.shape[0]), reporting_lag_weeks=lag_weeks,
        alpha=alpha, beta=beta, backtest=backtest, residuals=tuple(residuals.tolist()),
    )
