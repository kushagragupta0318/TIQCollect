"""Borrower lookup for the bank's top-bar search (C08's entry point).

What this is for: a bank user who has a name, a customer reference or a loan
account number in front of them and wants that borrower's page. It is NOT a
directory, a report or an export, and the shape says so — a handful of rows, no
total, no pagination, no filters.

The rules it is built to keep:

- **The bank comes from the caller's own row**, never from a parameter, so no
  query can reach another bank's book.
- **A region-limited caller searches inside their region only.** A borrower is
  reachable only through a loan the caller may see, which is the same rule the
  borrower page itself applies — so search can never surface a borrower whose
  page would then 404.
- **No existence is disclosed.** A query that matches nothing and a query whose
  matches are all outside the caller's scope return the identical empty body.
- **PAN and Aadhaar are not searchable and are not returned.** They are held
  masked, and a masked identifier is still an identifier: making it searchable
  turns a partial into an oracle.
- **A short query matches nothing.** Two characters would return an arbitrary
  slice of the book, which is a directory by another name.
"""
from __future__ import annotations

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.errors import AppException, ErrorCode
from app.models.customer import Customer
from app.models.loan import Loan
from app.services.placement_read_service import apply_region_limit

#: Below this, the query is refused (matched as nothing). Three characters is
#: the shortest that identifies rather than enumerates.
MIN_QUERY = 3
#: The lookup returns at most this many borrowers, and says when it had more.
#: There is no page 2: the answer to a wide query is a narrower query.
MAX_RESULTS = 15


def _bank_id(ctx) -> str:
    if getattr(ctx, "scope", None) != "BANK" or not getattr(ctx, "bank_id", None):
        raise AppException(403, ErrorCode.FORBIDDEN, "A bank user is required")
    return ctx.bank_id


def _escaped(term: str) -> str:
    """LIKE wildcards in the user's own text are literal. Without this, a query
    of "%" matches the whole book — the one query the rules above forbid."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_customers(db: Session, ctx, query: str, *, region_limit=None) -> dict:
    """Borrowers of the caller's bank matching `query`, reachable through a loan
    the caller may see.

    Matches a name anywhere, or a customer reference or loan account number from
    its start — an account number is quoted from its beginning, and an infix
    match on it would let a three-digit query walk the book.
    """
    bank_id = _bank_id(ctx)
    term = (query or "").strip()
    if len(term) < MIN_QUERY:
        # Not an error: the search box is live, and most keystrokes are short.
        return {"items": [], "query_too_short": True, "min_query_length": MIN_QUERY,
                "truncated": False}

    like = _escaped(term)
    rows = (
        apply_region_limit(
            db.query(
                Customer.id,
                Customer.full_name,
                Customer.city,
                func.count(func.distinct(Loan.id)).label("loans"),
                func.min(Loan.loan_account_number).label("first_account"),
            )
            .join(Loan, Loan.customer_id == Customer.id)
            .filter(
                Customer.bank_id == bank_id,
                # Loan.bank_id is named too: the join alone would trust the loan
                # row's customer pointer to stay inside the bank.
                Loan.bank_id == bank_id,
                or_(
                    Customer.full_name.ilike(f"%{like}%", escape="\\"),
                    Customer.customer_ref.ilike(f"{like}%", escape="\\"),
                    Loan.loan_account_number.ilike(f"{like}%", escape="\\"),
                ),
            ),
            region_limit,
        )
        .group_by(Customer.id, Customer.full_name, Customer.city)
        .order_by(Customer.full_name)
        # One more than the cap, so "there are more" is known without counting
        # the matches — a count would report the size of the book behind it.
        .limit(MAX_RESULTS + 1)
        .all()
    )

    truncated = len(rows) > MAX_RESULTS
    items = [
        {
            "customer_id": str(r.id),
            "full_name": r.full_name,
            "city": r.city,
            "loans": int(r.loans or 0),
            "first_account": r.first_account,
        }
        for r in rows[:MAX_RESULTS]
    ]
    return {"items": items, "query_too_short": False, "min_query_length": MIN_QUERY,
            "truncated": truncated}
