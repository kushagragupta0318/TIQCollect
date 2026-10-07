"""Guardrails on LLM-generated text (owner-directed RAG + guardrails build,
2026-10-07).

Two independent, pattern-based checks, each fail-closed and never
raising — the same discipline core/llm.py and core/redaction.py already
hold:

  - `check_figures` turns the "use only the figures given" instruction
    every narrative prompt already states (see report_templates.py,
    manager.py) into an ENFORCED check: a generated number that does not
    appear in the call site's own input figures fails the call, which
    routes to the existing `ai_generated=False` template-fallback path —
    no new failure mode, just an enforced one.
  - `check_tone` is the RBI Fair-Practices conduct guard
    (core/rag/corpus/rbi_fair_practices.md) for AGENT-FACING text.

Prompt-injection on user-authored free text (visit notes, borrower
comments) is NOT a third function here: `core/prompting.py`'s `fence()` +
`DATA_RULE` already is the one definition of that defense (ADR 0001) —
every free-text field reaching a narrative prompt already goes through it
(checked: agent.py's visit_strategy fences `agent_recording_transcript`,
`borrower_recording_transcript`, `ai_visit_note`, `customer_response_notes`
and `bank_agent_remarks`; the other five narrative purposes embed only
structured figures, no free text at all). A second, pattern-based
"sanitizer" alongside it would be an unlabelled restatement, not a second
layer — and a weaker one, since fence() can't be talked past by phrasing a
pattern-matcher has not seen, where "declare it as data and strip anything
that could forge a fence" does not depend on recognising the attempt.

None of this sits behind `settings.LLM_RAG_ENABLED`. These are safety
guards, not a RAG feature — turning RAG off must not turn them off too.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── figure-fabrication guard ─────────────────────────────────────────────────

_NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")
# Below this, a bare number is almost always a count in ordinary prose ("3
# months", "7 days", "2 agencies") rather than a figure that needed
# grounding — guarding it would flag fluent writing, not fabrication.
MIN_GUARDED_VALUE = 10.0
_PLAUSIBLE_YEAR = range(1900, 2100)


def _extract_numbers(text: str) -> list[float]:
    out: list[float] = []
    for raw in _NUMBER_RE.findall(text or ""):
        cleaned = raw.replace(",", "")
        if cleaned in ("", "-", "."):
            continue
        try:
            out.append(float(cleaned))
        except ValueError:
            continue
    return out


def _is_benign(n: float) -> bool:
    if abs(n) < MIN_GUARDED_VALUE:
        return True
    return n == int(n) and int(n) in _PLAUSIBLE_YEAR          # a calendar year, not a figure


def _close(a: float, b: float, rel_tol: float) -> bool:
    return abs(a - b) <= max(abs(a), abs(b)) * rel_tol + 0.5   # +0.5: integer rounding in prose


@dataclass(frozen=True)
class FigureCheckResult:
    ok: bool
    offending: tuple[float, ...] = ()


def check_figures(generated_text: str, allowed_source_text: str, *, rel_tol: float = 0.01) -> FigureCheckResult:
    """A generated narrative may cite only numbers that already appear
    (allowing for formatting and rounding) in `allowed_source_text` — the
    template fallback / KPI values the call site's own prompt was built
    from. A number the model invented fails the call; the caller is
    expected to fall back to the template, exactly the existing
    `ai_generated=False` path. Dates and small counts are exempt
    (MIN_GUARDED_VALUE, `_is_benign`) — this guards fabricated FIGURES, not
    every integer in fluent prose."""
    allowed = [n for n in _extract_numbers(allowed_source_text) if not _is_benign(n)]
    offending = tuple(
        n for n in _extract_numbers(generated_text)
        if not _is_benign(n) and not any(_close(n, a, rel_tol) for a in allowed)
    )
    return FigureCheckResult(ok=not offending, offending=offending)


# ── tone / compliance guard ──────────────────────────────────────────────────

# Coercive, threatening or shaming phrasing — the conduct
# core/rag/corpus/rbi_fair_practices.md names as what fair-practice recovery
# must not do. A firm, factual statement ("this case is now in the legal
# escalation stage", "please pay by Friday") must NOT match any of these;
# only phrasing that implies harm, humiliation, or an ultimatum does.
_COERCIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bpay\b.{0,25}\bor\s+(else|face|suffer)\b", re.I | re.S),
    re.compile(r"\byou\s+will\s+regret\b", re.I),
    re.compile(r"\bwe\s+will\s+(ruin|destroy|expose|humiliate|shame)\b", re.I),
    re.compile(r"\b(threat|threaten|intimidat\w*)\b", re.I),
    re.compile(r"\b(arrest|jail|police)\b.{0,25}\b(if|unless)\b", re.I | re.S),
    re.compile(r"\bconsequences\s+will\s+be\s+severe\b", re.I),
    re.compile(r"\bshame\s+(you|them|the\s+family)\b", re.I),
    re.compile(r"\b(everyone|your\s+neighbou?rs?|your\s+employer)\s+will\s+(know|find\s+out)\b", re.I),
)


@dataclass(frozen=True)
class ToneCheckResult:
    ok: bool
    reason: str = ""


def check_tone(text: str) -> ToneCheckResult:
    """RBI Fair-Practices-style conduct guard for AGENT-FACING text (visit
    strategy, agent insight). Always on, regardless of LLM_RAG_ENABLED — a
    safety guard is not a RAG feature."""
    if not isinstance(text, str) or not text:
        return ToneCheckResult(ok=True)
    for pattern in _COERCIVE_PATTERNS:
        if pattern.search(text):
            return ToneCheckResult(ok=False, reason=f"matched coercive pattern: {pattern.pattern}")
    return ToneCheckResult(ok=True)
