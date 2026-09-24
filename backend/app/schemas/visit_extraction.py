# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New file. H14: the request and response of
#   POST /agent/cases/{case_id}/visit-extraction. Its own module rather than
#   schemas/agent.py so the v2 rewrite of that file and this feature cannot
#   collide.
# ───────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.visit_report_extraction import MAX_TRANSCRIPT_CHARS


class VisitExtractionRequest(BaseModel):
    # The agent's visit report and the customer's statement, as transcribed —
    # the frontend joins the two boxes. Capped at what the extractor reads.
    transcript: str = Field(min_length=1, max_length=MAX_TRANSCRIPT_CHARS)


class ExtractedField(BaseModel):
    field: str
    value: Any
    evidence: str


class RejectedField(BaseModel):
    field: str
    value: Any
    reason: str


class VisitExtractionResponse(BaseModel):
    source: Literal["llm", "rules", "none"]
    ai_generated: bool
    suggestions: list[ExtractedField]
    rejected: list[RejectedField]
    llm_status: str | None
    failure_reason: str | None
    version: str
