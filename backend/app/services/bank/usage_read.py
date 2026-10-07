"""The bank's LLM usage and cost (GET /bank/usage): what this bank's own
tenancy spent calling a model, by feature and by day.

ONE definition of the scope, mirroring services/bank/audit_read.py for the
same reason that file states: the list and any later export must not disagree
about who may read what.

WHAT THIS CANNOT SEE, stated rather than discovered: a row whose `bank_id` is
NULL. core/llm.py's `complete()`/`chat()` default `bank_id` to None, and every
row written before 2026-10-07 (the six call sites were wired that day) is one
— a known, counted gap, the exact shape of AuditLog's own unattributed rows.
A future seventh call site that forgets to pass `bank_id` lands here too.
Those calls still cost real money; they are just not provably this bank's, so
`coverage.pending_attribution` counts them platform-wide (same value shown to
every bank) instead of guessing which bank to charge.

A second, narrower gap lives in `_cost_usd` (core/llm.py): a model missing
from its price table prices as NULL, not 0 — a real zero must stay
distinguishable from "price unknown". `unpriced_calls` on the totals and on
each breakdown row counts those separately from the summed `cost_usd`, which
SQL's SUM already skips NULLs for.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.llm_call import LLMCall

WINDOW_DAYS = 30


@dataclass(frozen=True)
class Filters:
    feature: str | None = None
    since: datetime | None = None
    until: datetime | None = None

    def window_start(self) -> datetime:
        return self.since or (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS))


def _windowed(q, f: Filters):
    q = q.filter(LLMCall.created_at >= f.window_start())
    if f.until is not None:
        q = q.filter(LLMCall.created_at <= f.until)
    if f.feature:
        q = q.filter(LLMCall.feature == f.feature)
    return q


#: ONE definition of "this bank's own rows in the window" — totals/by_feature/
#: by_day each used to re-filter LLMCall.bank_id == bank_id from scratch
#: (coordinator audit, 2026-10-07); they now build on this and pick their own
#: columns with `.with_entities()`, which keeps the filter without restating
#: it. `pending_attribution` is deliberately NOT built on this: it reads
#: bank_id IS NULL, a different scope, not a second copy of this one.
def _scoped(db: Session, bank_id: str, f: Filters):
    return _windowed(db.query(LLMCall).filter(LLMCall.bank_id == bank_id), f)


_UNPRICED = func.coalesce(func.sum(case((LLMCall.cost.is_(None), 1), else_=0)), 0)


def totals(db: Session, bank_id: str, f: Filters) -> dict:
    calls, input_tokens, output_tokens, cache_tokens, cost_usd, unpriced = _scoped(db, bank_id, f).with_entities(
        func.count(LLMCall.id),
        func.coalesce(func.sum(LLMCall.input_tokens), 0),
        func.coalesce(func.sum(LLMCall.output_tokens), 0),
        func.coalesce(func.sum(LLMCall.cache_tokens), 0),
        func.coalesce(func.sum(LLMCall.cost), 0.0),
        _UNPRICED,
    ).one()
    return {
        "calls": int(calls), "input_tokens": int(input_tokens), "output_tokens": int(output_tokens),
        "cache_tokens": int(cache_tokens), "cost_usd": float(cost_usd), "unpriced_calls": int(unpriced or 0),
    }


def by_feature(db: Session, bank_id: str, f: Filters) -> list[dict]:
    q = _scoped(db, bank_id, f).with_entities(
        LLMCall.feature, func.count(LLMCall.id),
        func.coalesce(func.sum(LLMCall.input_tokens), 0), func.coalesce(func.sum(LLMCall.output_tokens), 0),
        func.coalesce(func.sum(LLMCall.cache_tokens), 0), func.coalesce(func.sum(LLMCall.cost), 0.0),
        _UNPRICED,
    ).group_by(LLMCall.feature)
    return [
        {"feature": feature, "calls": int(calls), "input_tokens": int(it), "output_tokens": int(ot),
         "cache_tokens": int(ct), "cost_usd": float(cost), "unpriced_calls": int(unpriced or 0)}
        for feature, calls, it, ot, ct, cost, unpriced in q.order_by(func.sum(LLMCall.cost).desc().nullslast())
    ]


def by_day(db: Session, bank_id: str, f: Filters) -> list[dict]:
    day = func.date(LLMCall.created_at)
    q = _scoped(db, bank_id, f).with_entities(
        day, func.count(LLMCall.id), func.coalesce(func.sum(LLMCall.cost), 0.0), _UNPRICED,
    ).group_by(day)
    return [{"day": str(d), "calls": int(calls), "cost_usd": float(cost), "unpriced_calls": int(unpriced or 0)}
            for d, calls, cost, unpriced in q.order_by(day.asc())]


def pending_attribution(db: Session, f: Filters) -> int:
    """Calls in the window with no bank at all — see the module docstring."""
    q = _windowed(db.query(func.count(LLMCall.id)).filter(LLMCall.bank_id.is_(None)), f)
    return int(q.scalar() or 0)


def coverage(db: Session, f: Filters) -> dict:
    unattributed = pending_attribution(db, f)
    return {
        "pending_attribution": unattributed,
        "note": (
            "Calls with no bank recorded cannot be charged to one, so they are counted here "
            f"rather than added to any bank's total ({unattributed} in this window). They still "
            "cost real money — this is a metering gap (rows from before 2026-10-07, or any "
            "caller that omits bank_id), not evidence those calls were free."
        ),
    }
