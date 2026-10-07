"""The one honesty stamp every surface prints beside a strategy figure.

ADR 0014: we never claim real-world performance; we show performance on the
synthetic data we generated and NAME what it was measured on. The demo's value
rests on a bank believing the numbers are honest, so the caveat and the basis
must travel with every figure — the headline, a record row, the API, the UI, the
board PDF.

The failure this prevents: four consumers each formatting their own caption gives
four spellings and eventually one omission, and the omission lands on the surface
someone reformats for space. So there is ONE renderer here and consumers call it;
no consumer writes caption text. The wording itself has one home too — the
sentences are monte_carlo's SYNTHETIC_LINE / UNCALIBRATED_WARNING, composed here
per case, never restated.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.strategy.monte_carlo import SYNTHETIC_LINE, UNCALIBRATED_WARNING


@dataclass(frozen=True)
class HonestyStamp:
    """What a figure rests on, as structured fields. `text()` renders the one
    caption; `as_fields()` is for an API, a record row or a UI that builds its
    own layout — but neither the caption nor its absence is ever a consumer's to
    decide.

    - synthetic: inputs are generated, not observed about a real borrower.
    - calibrated: a backtest reached nominal coverage FOR THIS engine version.
      On synthetic data that means the bands behave as advertised on a book whose
      truth we generated — real-book calibration is a different, later claim.
    - engine_version / data_version: stamped so an old figure stays readable.
    - basis: the population and method in words, e.g.
      "27,412 synthetic borrowers, generator sim-2.1, 12 months of transitions".
    """
    synthetic: bool
    calibrated: bool
    engine_version: str
    data_version: str
    basis: str

    def text(self) -> str:
        """The caption. Always names the basis; carries the synthetic and/or
        uncalibrated sentence when they apply. Never empty — even a calibrated,
        real-book figure states what it rests on."""
        parts: list[str] = []
        if self.synthetic:
            parts.append(SYNTHETIC_LINE)
        if not self.calibrated:
            parts.append(UNCALIBRATED_WARNING)
        parts.append(f"Basis: {self.basis} (engine {self.engine_version}, data {self.data_version}).")
        return " ".join(parts)

    def as_fields(self) -> dict:
        """Structured form for a consumer that lays out its own surface. It still
        gets `text` so it cannot quietly drop the caption; a tripwire checks that
        every figure-bearing payload carries these keys."""
        return {
            "synthetic": self.synthetic,
            "calibrated": self.calibrated,
            "engine_version": self.engine_version,
            "data_version": self.data_version,
            "basis": self.basis,
            "text": self.text(),
        }


# Keys a figure-bearing payload must carry so the stamp cannot be dropped. The
# tripwire test and any consumer check against this one list.
STAMP_FIELDS: frozenset[str] = frozenset(HonestyStamp(False, False, "", "", "").as_fields())


def stamp_for_simulation(result, *, data_version: str, basis: str) -> HonestyStamp:
    """Read the stamp off a SimulationResult. `synthetic` and `calibrated` are the
    run's own `synthetic_inputs` / `calibrated_by_backtest`, so the stamp cannot
    disagree with the run that produced the figure."""
    return HonestyStamp(
        synthetic=bool(result.synthetic_inputs),
        calibrated=bool(result.calibrated_by_backtest),
        engine_version=result.engine_version,
        data_version=data_version,
        basis=basis,
    )


def stamp_for_cash_forecast(run, *, data_version: str, basis: str) -> HonestyStamp:
    """Read the stamp off a cash_forecast.CashForecastRun. `synthetic` is True
    (every book this runs on today is the generated demo book, same as
    stamp_for_simulation); `calibrated` is the run's own rolling-origin
    backtest result, so the stamp cannot claim a coverage the backtest did
    not measure. Unlike stamp_for_simulation/stamp_for_backtest, `synthetic`
    is hardcoded rather than read off the run, because CashForecastRun does
    not carry its own synthetic flag — read_weekly_payments has no caller
    today that is not the demo book. Flip this to read a real flag once a
    real book can reach this function; hardcoding fails safe in the
    meantime (it can only over-claim synthetic, never under-claim it)."""
    return HonestyStamp(
        synthetic=True,
        calibrated=bool(run.backtest.calibrated),
        engine_version=run.engine_version,
        data_version=data_version,
        basis=basis,
    )


def stamp_for_backtest(report, *, data_version: str, basis: str) -> HonestyStamp:
    """Read the stamp off a backtest.BacktestReport. A backtest run on synthetic
    data is `synthetic=True`; `calibrated` is whether it passed, which is exactly
    the claim 'the bands behave as advertised on this (generated) book'."""
    return HonestyStamp(
        synthetic=bool(report.synthetic),
        calibrated=bool(report.passes()),
        engine_version=report.engine_version,
        data_version=data_version,
        basis=basis,
    )
