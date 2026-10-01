"""The one honesty stamp (app/strategy/honesty.py), ADR 0014.

No DB. What this holds: the caption is never empty; it carries the synthetic
and/or uncalibrated sentence exactly when they apply; the wording is monte_carlo's
constants, not a second copy; the stamp read off a run cannot disagree with the
run; and as_fields carries every key the tripwire requires.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.strategy import honesty as H
from app.strategy.honesty import HonestyStamp
from app.strategy.monte_carlo import SYNTHETIC_LINE, UNCALIBRATED_WARNING


def _stamp(synthetic: bool, calibrated: bool) -> HonestyStamp:
    return HonestyStamp(synthetic, calibrated, "mc-1.2.0", "demo-2026-09", "100 synthetic borrowers")


def test_the_caption_is_never_empty_and_always_names_the_basis():
    for s in (True, False):
        for c in (True, False):
            text = _stamp(s, c).text()
            assert "Basis: 100 synthetic borrowers" in text
            assert "engine mc-1.2.0" in text and "data demo-2026-09" in text


def test_the_synthetic_and_uncalibrated_sentences_appear_exactly_when_they_apply():
    assert SYNTHETIC_LINE in _stamp(True, False).text()
    assert UNCALIBRATED_WARNING in _stamp(True, False).text()
    # calibrated synthetic: synthetic sentence yes, uncalibrated no
    t = _stamp(True, True).text()
    assert SYNTHETIC_LINE in t and UNCALIBRATED_WARNING not in t
    # real but uncalibrated: uncalibrated yes, synthetic no
    t = _stamp(False, False).text()
    assert UNCALIBRATED_WARNING in t and SYNTHETIC_LINE not in t
    # real and calibrated: neither caveat, but still a basis line
    t = _stamp(False, True).text()
    assert SYNTHETIC_LINE not in t and UNCALIBRATED_WARNING not in t and "Basis:" in t


def test_the_wording_is_the_engines_constants_not_a_second_copy():
    # If monte_carlo's sentence changed, this stamp changes with it — one home.
    assert SYNTHETIC_LINE in _stamp(True, False).text()
    assert _stamp(True, False).text().count("SYNTHETIC:") == 1


def test_as_fields_carries_every_key_the_tripwire_requires():
    f = _stamp(True, False).as_fields()
    assert set(f) >= H.STAMP_FIELDS
    assert f["synthetic"] is True and f["calibrated"] is False
    assert f["text"] == _stamp(True, False).text()
    # STAMP_FIELDS is exactly what as_fields produces, so a consumer and the
    # tripwire check the same list.
    assert H.STAMP_FIELDS == frozenset(f)


@dataclass
class _FakeResult:
    synthetic_inputs: bool
    calibrated_by_backtest: bool
    engine_version: str = "mc-1.2.0"


def test_a_stamp_read_off_a_run_cannot_disagree_with_the_run():
    r = _FakeResult(synthetic_inputs=True, calibrated_by_backtest=False)
    st = H.stamp_for_simulation(r, data_version="demo-2026-09", basis="761k-row demo book")
    assert st.synthetic is True and st.calibrated is False and st.engine_version == "mc-1.2.0"
    assert SYNTHETIC_LINE in st.text() and "761k-row demo book" in st.text()


@dataclass
class _FakeReport:
    synthetic: bool
    engine_version: str = "mc-1.2.0"
    _passes: bool = True
    def passes(self) -> bool:
        return self._passes


def test_a_backtest_stamp_is_calibrated_iff_the_backtest_passes():
    passing = H.stamp_for_backtest(_FakeReport(synthetic=True, _passes=True),
                                   data_version="d", basis="synthetic panel")
    failing = H.stamp_for_backtest(_FakeReport(synthetic=True, _passes=False),
                                   data_version="d", basis="synthetic panel")
    assert passing.calibrated is True and UNCALIBRATED_WARNING not in passing.text()
    assert failing.calibrated is False and UNCALIBRATED_WARNING in failing.text()
    assert passing.synthetic and SYNTHETIC_LINE in passing.text()
