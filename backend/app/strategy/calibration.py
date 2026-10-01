"""E04: calibrate the engine on a bank's own month-end history and stamp it.

This is the piece that makes UNCALIBRATED a measured statement rather than an
apology (ADR 0014). It loads the bank's panel (history.py), backtests the engine's
p10-p90 bands against what actually happened (backtest.py), and returns the
coverage figure WITH the one honesty stamp (honesty.py) — so the number never
travels without naming the synthetic book it was measured on.

It composes the three E04 parts; it defines no new rule. The window is chosen to
fit the panel: a backtest needs a fit window before the origin and a scoring
window after it, so a book too short to hold both ABSTAINS — the same honest
refusal transitions.py and history.py give, for the same reason.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.core.errors import AppException, ErrorCode
from app.strategy.backtest import BacktestReport, backtest
from app.strategy.history import read_panel
from app.strategy.honesty import HonestyStamp, stamp_for_backtest

# A backtest needs at least a 2-month fit window and a 2-month scoring horizon, so
# a panel under this many month-ends cannot be scored at all. Named, not magic.
MIN_CALIBRATION_MONTHS = 4
DEFAULT_HORIZON = 6


@dataclass(frozen=True)
class CalibrationResult:
    report: BacktestReport
    stamp: HonestyStamp
    loans: int
    month_ends: tuple[date, ...]

    @property
    def calibrated(self) -> bool:
        return self.report.passes()

    def as_dict(self) -> dict:
        """Coverage figure plus the stamp — the number and its basis together."""
        return {
            "coverage": self.report.coverage,
            "nominal": self.report.nominal,
            "band": list(self.report.band),
            "coverage_by_month": list(self.report.coverage_by_month),
            "horizon_months": self.report.horizon_months,
            "cohort_accounts": self.report.cohort_accounts,
            "passes": self.report.passes(),
            "loans": self.loans,
            "months": len(self.month_ends),
            **self.stamp.as_fields(),
        }


def _window(n_months: int, requested: int) -> int:
    """Largest horizon that leaves a >=2-month fit window, capped at `requested`.

    backtest needs origin = M-1-horizon >= 2 (a fit window of at least 2
    transitions), so horizon <= M-3.
    """
    return max(2, min(requested, n_months - 3))


def calibrate(adb: Session, bank_id: str, *, as_of: date | None = None,
              horizon_months: int = DEFAULT_HORIZON, n_paths: int = 500, seed: int = 0,
              synthetic: bool = True, data_version: str | None = None) -> CalibrationResult:
    """Backtest the engine on the bank's own history and return coverage + stamp.

    `synthetic` defaults True: every book this runs on today is the generated demo
    book, and the stamp must say so. A caller with a genuinely real, consented book
    passes synthetic=False — and even then the coverage is a real claim only
    because it was measured, which is the whole point of E04.

    Abstains (INSUFFICIENT_HISTORY) when the panel is too short to hold a fit and a
    scoring window; read_panel abstains earlier for an even shorter book.
    """
    reading = read_panel(adb, bank_id, as_of=as_of, synthetic=synthetic)
    if reading.months < MIN_CALIBRATION_MONTHS:
        raise AppException(
            422, ErrorCode.INSUFFICIENT_HISTORY,
            f"This bank has {reading.months} month-end(s); a backtest needs at least "
            f"{MIN_CALIBRATION_MONTHS} to fit on one window and score on another.")

    horizon = _window(reading.months, horizon_months)
    try:
        report = backtest(reading.panel, horizon_months=horizon, n_paths=n_paths, seed=seed)
    except ValueError as e:
        # A panel too sparse to estimate shock volatility (no segment has enough
        # deterioration AND improvement moves) cannot support a calibration claim.
        # Abstain with a reason — the same honest refusal E01 gives — rather than
        # letting a thin book 500 the page (ADR 0005's abstain-rather-than-impute).
        raise AppException(422, ErrorCode.INSUFFICIENT_HISTORY,
                           f"This bank's history is too sparse to calibrate: {e}") from e

    dv = data_version or (reading.month_ends[-1].isoformat() if reading.month_ends else "unknown")
    basis = (f"{reading.loans} {'synthetic ' if synthetic else ''}loans, "
             f"{reading.months} month-ends of loan_dpd_history "
             f"({reading.month_ends[0].isoformat()}..{reading.month_ends[-1].isoformat()}), "
             f"p{report.band[0]:.0f}-p{report.band[1]:.0f} coverage over a {horizon}-month horizon")
    stamp = stamp_for_backtest(report, data_version=dv, basis=basis)
    return CalibrationResult(report=report, stamp=stamp, loans=reading.loans,
                             month_ends=reading.month_ends)
