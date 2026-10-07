"""Response shapes for the bank's usage & cost page (endpoints/bank_usage.py,
services/bank/usage_read.py)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class UsageTotalsOut(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cache_tokens: int
    cost_usd: float
    #: Calls priced at NULL (the model was not in core/llm.py's price table),
    #: already excluded from `cost_usd` — never silently folded into it as 0.
    unpriced_calls: int


class UsageByFeatureOut(BaseModel):
    feature: str
    calls: int
    input_tokens: int
    output_tokens: int
    cache_tokens: int
    cost_usd: float
    unpriced_calls: int


class UsageByDayOut(BaseModel):
    day: str
    calls: int
    cost_usd: float
    unpriced_calls: int


class UsageCoverageOut(BaseModel):
    pending_attribution: int
    note: str


class UsagePageOut(BaseModel):
    since: str
    until: Optional[str] = None
    totals: UsageTotalsOut
    by_feature: list[UsageByFeatureOut]
    by_day: list[UsageByDayOut]
    coverage: UsageCoverageOut
