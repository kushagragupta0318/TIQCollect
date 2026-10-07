"""The bank's LLM usage and cost (GET /bank/usage): what this bank's own
tenancy spent calling a model, by feature and by day.

ONE definition of the scope, mirroring services/bank/audit_read.py for the
same reason that file states: the list and any later export must not disagree
about who may read what.

WHAT THIS CANNOT SEE, stated rather than discovered: a row whose `bank_id` is
NULL. core/llm.py's `complete()`/`chat()` default `bank_id` to None, and none
of today's six call sites (agent.py, manager.py, case_service.py,
visit_report_extraction.py, ai_report_service.py) pass one yet — a known,
counted gap, the exact shape of AuditLog's own unattributed rows. Those calls
still cost real money; they are just not yet provably this bank's, so
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


def scoped_query(db: Session, bank_id: str, f: Filters):
    """This bank's own calls in the window, newest first. An unattributed
    (bank_id NULL) row can never match — see the module docstring."""
    q = db.query(LLMCall).filter(LLMCall.bank_id == bank_id)
    return _windowed(q, f).order_by(LLMCall.created_at.desc())


def totals(db: Session, bank_id: str, f: Filters) -> dict:
    q = _windowed(db.query(
        func.count(LLMCall.id),
        func.coalesce(func.sum(LLMCall.input_tokens), 0),
        func.coalesce(func.sum(LLMCall.output_tokens), 0),
        func.coalesce(func.sum(LLMCall.cache_tokens), 0),
        func.coalesce(func.sum(LLMCall.cost), 0.0),
        func.coalesce(func.sum(case((LLMCall.cost.is_(None), 1), else_=0)), 0),
    ).filter(LLMCall.bank_id == bank_id), f)
    calls, input_tokens, output_tokens, cache_tokens, cost_usd, unpriced = q.one()
    return {
        "calls": int(calls), "input_tokens": int(input_tokens), "output_tokens": int(output_tokens),
        "cache_tokens": int(cache_tokens), "cost_usd": float(cost_usd), "unpriced_calls": int(unpriced or 0),
    }


def by_feature(db: Session, bank_id: str, f: Filters) -> list[dict]:
    q = _windowed(db.query(
        LLMCall.feature, func.count(LLMCall.id),
        func.coalesce(func.sum(LLMCall.input_tokens), 0), func.coalesce(func.sum(LLMCall.output_tokens), 0),
        func.coalesce(func.sum(LLMCall.cache_tokens), 0), func.coalesce(func.sum(LLMCall.cost), 0.0),
    ).filter(LLMCall.bank_id == bank_id), f).group_by(LLMCall.feature)
    return [
        {"feature": feature, "calls": int(calls), "input_tokens": int(it), "output_tokens": int(ot),
         "cache_tokens": int(ct), "cost_usd": float(cost)}
        for feature, calls, it, ot, ct, cost in q.order_by(func.sum(LLMCall.cost).desc().nullslast())
    ]


def by_day(db: Session, bank_id: str, f: Filters) -> list[dict]:
    day = func.date(LLMCall.created_at)
    q = _windowed(db.query(
        day, func.count(LLMCall.id), func.coalesce(func.sum(LLMCall.cost), 0.0),
    ).filter(LLMCall.bank_id == bank_id), f).group_by(day)
    return [{"day": str(d), "calls": int(calls), "cost_usd": float(cost)}
            for d, calls, cost in q.order_by(day.asc())]


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
            "cost real money — this is a metering gap (known: today's callers do not pass "
            "bank_id yet), not evidence those calls were free."
        ),
    }
