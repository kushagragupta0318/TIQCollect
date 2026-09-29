"""The placement engine (P3 D09, ADR 0010)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_placement_exploration_is_off_by_default_and_capped_at_twenty_percent():
    assert Settings().PLACEMENT_EXPLORATION_RATE == 0.0
    assert Settings(PLACEMENT_EXPLORATION_RATE=0.2).PLACEMENT_EXPLORATION_RATE == 0.2
    for bad in (0.21, -0.01):
        with pytest.raises(ValidationError):
            Settings(PLACEMENT_EXPLORATION_RATE=bad)
