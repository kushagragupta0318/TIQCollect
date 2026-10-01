"""One definition of a portfolio breakdown, for every dimension and both surfaces.

Known issue 8: the agency analytics page broke the book down by agent, DPD
bucket and month only, although the columns for branch, city and product have
always been there. The metric rules -- what counts as collected, what the rate
divides by, what a missing value is called -- live here once, so the agency
view and the bank view cannot drift apart, and so a new dimension is a row in
`DIMENSIONS` rather than another hand-written query.

The bucket dimension is the query that used to be inline in
`manager.get_team_dpd_breakdown`, moved here unchanged: same joins, same two
modes, same lakh rounding, same bucket ordering. Its output is byte-identical,
because the agency page already reads it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.case import Case
from app.models.customer import Customer
from app.models.loan import Loan
from app.models.payment import Payment

Dimension = Literal["bucket", "product", "branch", "city"]

#: DPD buckets are ordered by severity, not alphabetically. Every other
#: dimension is ordered by size, largest first: nobody reads a 522-branch list
#: alphabetically, and the question a breakdown answers is "where is the money".
BUCKET_ORDER = ("BUCKET_1", "BUCKET_2", "BUCKET_3", "NPA")

#: dimension -> the column it groups by. A dimension on `customers` brings its
#: own join; everything else is already on the loan.
_COLUMN = {
    "bucket": Loan.dpd_bucket,
    "product": Loan.loan_type,
    "branch": Loan.branch_code,
    "city": Customer.city,
}
_NEEDS_CUSTOMER = ("city",)

#: What an empty dimension value is called on screen. A loan with no branch
#: recorded is a real row with real money; dropping it would make the
#: breakdown's total disagree with the page's own header.
UNKNOWN = "Not recorded"

LAKH = 100_000


@dataclass(frozen=True)
class BreakdownRow:
    key: str
    case_count: int
    target_lakhs: float
    collected_lakhs: float
    collection_rate_pct: float


def dimensions() -> tuple[str, ...]:
    return tuple(_COLUMN)


def _label(value) -> str:
    if value is None or value == "":
        return UNKNOWN
    return value.value if hasattr(value, "value") else str(value)


def breakdown(db: Session, *, dimension: Dimension, agent_ids: Sequence[str] | None = None,
              bank_id: str | None = None, month: str | None = None) -> list[BreakdownRow]:
    """The book grouped by one dimension, for one manager's agents or one bank.

    `agent_ids` scopes it to an agency manager's team, `bank_id` to a bank's
    whole book; exactly one is required, because an unscoped aggregate over
    every tenant is never a thing a caller wants and would be a leak if it
    were. `month` (YYYY-MM) sums the payments VERIFIED in that calendar month;
    without it the figures are the all-time totals carried on the case.
    """
    if dimension not in _COLUMN:
        raise ValueError(f"unknown dimension {dimension!r}; one of {', '.join(_COLUMN)}")
    if (agent_ids is None) == (bank_id is None):
        raise ValueError("pass exactly one of agent_ids or bank_id")

    col = _COLUMN[dimension]
    if month:
        q = db.query(
            col.label("key"),
            func.count(func.distinct(Case.id)).label("case_count"),
            func.sum(Case.target_amount).label("target_amount"),
            func.sum(Payment.amount).label("collected_amount"),
            # select_from(Loan) explicitly: for a dimension whose column lives on
            # another table (city) the first entity would otherwise be that table,
            # and the joins below would have no Loan to hang off.
        ).select_from(Loan).join(Case, Case.loan_id == Loan.id).join(Payment, Payment.case_id == Case.id)
        lo, hi = _month_bounds(month)
        q = q.filter(Payment.payment_date >= lo, Payment.payment_date < hi,
                     Payment.status != "REJECTED")
        if agent_ids is not None:
            q = q.filter(Payment.agent_id.in_(agent_ids))
    else:
        q = db.query(
            col.label("key"),
            func.count(Case.id).label("case_count"),
            func.sum(Case.target_amount).label("target_amount"),
            func.sum(Case.collected_amount).label("collected_amount"),
        ).select_from(Loan).join(Case, Case.loan_id == Loan.id)

    if dimension in _NEEDS_CUSTOMER:
        q = q.join(Customer, Customer.id == Case.customer_id)
    if agent_ids is not None:
        q = q.filter(Case.agent_id.in_(agent_ids))
    else:
        q = q.filter(Loan.bank_id == bank_id)

    rows = [_row(r) for r in q.group_by(col).all()]
    if dimension == "bucket":
        return sorted(rows, key=lambda r: BUCKET_ORDER.index(r.key) if r.key in BUCKET_ORDER else 99)
    return sorted(rows, key=lambda r: (-r.collected_lakhs, r.key))


def _row(r) -> BreakdownRow:
    target = float(r.target_amount or 0)
    collected = float(r.collected_amount or 0)
    return BreakdownRow(
        key=_label(r.key),
        case_count=r.case_count,
        target_lakhs=round(target / LAKH, 2),
        collected_lakhs=round(collected / LAKH, 2),
        # max(target, 1) rather than a zero guard: the inline version this
        # replaces did the same, and a target of zero with money collected is a
        # data fault, not a 0% row.
        collection_rate_pct=round(collected / max(target, 1) * 100, 1),
    )


def _month_bounds(month: str) -> tuple[datetime, datetime]:
    try:
        year, mon = (int(part) for part in month.split("-"))
        lo = datetime(year, mon, 1)
    except (ValueError, TypeError) as exc:
        raise ValueError("month must be YYYY-MM") from exc
    hi = datetime(year + 1, 1, 1) if mon == 12 else datetime(year, mon + 1, 1)
    return lo, hi
