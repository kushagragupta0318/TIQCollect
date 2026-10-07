"""ai.llm_calls (F11): one row per complete()/chat() call, written by
core/llm.py's own best-effort `_record_usage` — never by a caller, and never
inside the caller's own transaction, so a metering failure cannot roll back
the feature that made the call.

UNATTRIBUTED ROWS ARE EXPECTED, NOT A BUG. Every row written before
2026-10-07 (the nine call sites across agent.py, manager.py,
case_service.py, visit_report_extraction.py, ai_report_service.py and
report_templates.py were wired that day) has bank_id NULL, and any future
caller that omits it lands here too — a known, counted gap, the same shape
as AuditLog's own unattributed rows (services/bank/audit_read.py's
`pending_attribution`). GET /bank/usage reports that count rather than
guessing a bank for them.

`cost` is computed once, at write time, from the provider's own published
price per token (core/llm.py's `_PRICE_PER_MTOK_USD`) — never recomputed on
read, so a later price change does not rewrite history. NULL when the model
was not in that table: abstain, not a silent zero.
"""
from __future__ import annotations

from sqlalchemy import Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKey, uuid_fk

SCHEMA = "ai"


class LLMCall(Base, UUIDPrimaryKey, CreatedAtMixin):
    __tablename__ = "llm_calls"

    # Nullable: see the module docstring. No bank row is ever deleted out from
    # under a historical cost record (base.uuid_fk's NO ACTION default).
    bank_id: Mapped[str | None] = uuid_fk("tenancy.banks.id", nullable=True)

    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    # The calling feature: llm.complete()/chat()'s own `purpose` ("briefing",
    # "visit_strategy", "case_ranking", ...). One vocabulary, not restated.
    feature: Mapped[str] = mapped_column(String(40), nullable=False)

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Read + creation combined (Usage.cache_tokens) — a display count; `cost`
    # is computed from the real split before the two are added together.
    cache_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # USD. NUMERIC, not float: summed across thousands of rows for the cost
    # page, and a binary float sum drifts exactly the way money must not.
    cost: Mapped[float | None] = mapped_column(Numeric(12, 6, asdecimal=False), nullable=True)

    __table_args__ = (
        Index(None, "bank_id", "created_at"),
        Index(None, "feature", "created_at"),
        {"schema": SCHEMA},
    )
