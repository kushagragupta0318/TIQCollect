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

ENGINE_VERSION = "cf-1.0.0"

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


def forecast_holt(level: float, trend: float, horizon: int) -> np.ndarray:
    """h-step-ahead point forecasts, h = 1..horizon. Collections cannot be
    negative, so the point forecast is floored at 0 (a strong downward trend
    extrapolated 13 weeks out can otherwise cross zero)."""
    h = np.arange(1, horizon + 1, dtype=np.float64)
    return np.maximum(level + h * trend, 0.0)


def residual_bands(residuals: np.ndarray, point: np.ndarray) -> dict[str, np.ndarray]:
    """p10/p50/p90 around `point`, widened by sqrt(h) — the random-walk growth
    rate for forecast-error variance, not a tuned width. Degenerate when there
    is only one residual (p10 == p50 == p90 == point): MIN_WEEKS_HISTORY
    guarantees several, so this is a defensive floor, not the normal path."""
    if residuals.size == 0:
        p10 = p50 = p90 = point
    else:
        r10, r50, r90 = np.percentile(residuals, [10, 50, 90])
        h = np.arange(1, point.shape[0] + 1, dtype=np.float64)
        scale = np.sqrt(h)
        p10 = np.maximum(point + r10 * scale, 0.0)
        p50 = np.maximum(point + r50 * scale, 0.0)
        p90 = np.maximum(point + r90 * scale, 0.0)
    # Clipping each band at 0 independently cannot invert their order: all
    # three are shifted by the same monotone r10 <= r50 <= r90 before the
    # clip, and max(x, 0) is monotone.
    return {"p10": p10, "p50": np.maximum(p50, p10), "p90": np.maximum(p90, p50)}


def reconcile(bottom_up: np.ndarray, top_down: np.ndarray) -> np.ndarray:
    """Equal-weight blend when the book has a bottom-up signal at all;
    otherwise the ETS leg alone. A silent 50/50 blend against an
    all-zero bottom-up (no PTPs, no recovery_risk coverage) would read as
    'the bottom-up method says half of the top-down number', which is not a
    method, it is an unlabelled halving of someone else's forecast."""
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
                "reason": self.reason}


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
    reason = "" if calibrated else (
        f"only {len(origins)} fold(s), need {MIN_BACKTEST_FOLDS}" if len(origins) < MIN_BACKTEST_FOLDS
        else f"MAPE {mape:.0%} exceeds the {CALIBRATION_MAPE_CEILING:.0%} ceiling")
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
             SELECT 1 FROM collections.ptps p
              WHERE p.loan_id = mp.loan_id
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
    alpha: float
    beta: float
    backtest: BacktestResult
    engine_version: str = ENGINE_VERSION

    def totals(self) -> dict[str, float]:
        return {"p10": float(sum(self.p10)), "p50": float(sum(self.p50)), "p90": float(sum(self.p90))}


def build_cash_forecast(adb: Session, bank_id: str, *, as_of: date | None = None,
                        horizon_weeks: int = HORIZON_WEEKS) -> CashForecastRun:
    """Read the book, fit the ETS leg, build the bottom-up leg, reconcile,
    band, and backtest. Raises AppException(INSUFFICIENT_HISTORY) — via
    read_weekly_payments — on a book too thin to fit at all; that is the only
    abstain condition (PTP and recovery_risk coverage degrade to zero instead,
    see their own docstrings)."""
    as_of = as_of or date.today()
    history = read_weekly_payments(adb, bank_id, as_of=as_of)
    level, trend, residuals, alpha, beta = fit_holt(history.amounts)
    top_down = forecast_holt(level, trend, horizon_weeks)

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
    bands = residual_bands(residuals, reconciled)

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
        history_weeks=int(history.amounts.shape[0]), alpha=alpha, beta=beta,
        backtest=backtest,
    )
