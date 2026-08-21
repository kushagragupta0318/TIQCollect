# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-21 — New file. The seam every repayment score passes through, so the
#   tier that produced a number always travels WITH the number.
#
#   Deliberately shaped like core/llm.py: resolved_scorer() mirrors
#   resolved_provider(), RepaymentScore mirrors LLMResult, and is_modelled
#   mirrors ai_generated. That is not decoration. The six AI features spent
#   months serving written-in fallbacks that were indistinguishable from model
#   output because nothing carried the distinction; llm.py exists because of it.
#   A hand-weighted scorecard being read as a trained model is the same failure
#   with the same cause, so it gets the same defence.
#
#   Only SCORECARD is implemented today. COMMAND_CENTER and MODEL are named here
#   so that adding one later is wiring rather than a rewrite — and so that
#   is_modelled has something to be false ABOUT.
#
#   Like llm.py, this never raises. A caller gets a result whose status says
#   what happened, and an unusable configuration degrades to the scorecard
#   rather than to an exception in the nightly task.
# ───────────────────────────────────────────────────────────────────────────
"""Which tier produced this score — and never letting a caller forget."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from app.core.config import settings
from app.ml.repayment_scorecard import SCORECARD_VERSION
from app.models.repayment_snapshot import (
    SOURCE_COMMAND_CENTER, SOURCE_MODEL, SOURCE_SCORECARD,
)

logger = structlog.get_logger()

# ── Outcomes ─────────────────────────────────────────────────────────────────
OK = "OK"
NOT_CONFIGURED = "NOT_CONFIGURED"   # the requested tier has nothing behind it
FELL_BACK = "FELL_BACK"             # requested tier unavailable; scorecard ran
NOT_FOUND = "NOT_FOUND"             # upstream had no record of this loan
TIMEOUT = "TIMEOUT"
UPSTREAM_ERROR = "UPSTREAM_ERROR"

# Every tier that is actually implemented. Anything else resolves to the
# scorecard with a named reason rather than failing at 19:45 in a Celery task.
_IMPLEMENTED = {SOURCE_SCORECARD}

_ALIASES = {
    "scorecard": SOURCE_SCORECARD,
    "command_center": SOURCE_COMMAND_CENTER,
    "command_centre": SOURCE_COMMAND_CENTER,
    "model": SOURCE_MODEL,
}


@dataclass
class ScorerResolution:
    """What will actually run, and why it differs from what was asked for."""
    source: str
    requested: str
    status: str
    model_version: str
    reason: str | None = None

    @property
    def is_modelled(self) -> bool:
        """True only for a trained model.

        The direct analogue of LLMResult.ai_generated. It travels with the score
        so a page cannot render hand-chosen weights behind an "AI" chip by
        forgetting to check — the flag is on the object, not in the caller's
        memory.
        """
        return self.source == SOURCE_MODEL

    @property
    def degraded(self) -> bool:
        return self.status != OK


@dataclass
class RepaymentScore:
    """A score plus its provenance. Never an exception."""
    likelihood: float
    risk_score: float
    band: str
    risk_category: str
    evidence_coverage: float
    source: str
    model_version: str
    status: str = OK
    factors: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None

    @property
    def is_modelled(self) -> bool:
        return self.source == SOURCE_MODEL

    @property
    def is_confident(self) -> bool:
        """Whether enough evidence spoke to show a NUMBER rather than a band."""
        return self.evidence_coverage >= settings.REPAYMENT_MIN_COVERAGE_TO_SHOW


def resolved_scorer() -> ScorerResolution:
    """The tier that will actually run. Degrades to the scorecard, never raises.

    Called once per run and logged, rather than per loan — 525 identical log
    lines would bury the one that matters.
    """
    requested_raw = (settings.REPAYMENT_SCORER or "").strip().lower()
    requested = _ALIASES.get(requested_raw)

    if requested is None:
        return ScorerResolution(
            source=SOURCE_SCORECARD, requested=requested_raw or "(unset)",
            status=FELL_BACK, model_version=SCORECARD_VERSION,
            reason=(f"REPAYMENT_SCORER={requested_raw!r} is not a known tier; "
                    f"using the scorecard"),
        )

    if requested not in _IMPLEMENTED:
        return ScorerResolution(
            source=SOURCE_SCORECARD, requested=requested,
            status=NOT_CONFIGURED, model_version=SCORECARD_VERSION,
            reason=f"{requested} is not implemented yet; using the scorecard",
        )

    return ScorerResolution(
        source=SOURCE_SCORECARD, requested=requested, status=OK,
        model_version=SCORECARD_VERSION,
    )


def log_resolution(resolution: ScorerResolution) -> None:
    """One line per run, at the level the situation deserves."""
    event = "repayment.scorer.resolved"
    if resolution.degraded:
        logger.warning(event, source=resolution.source,
                       requested=resolution.requested, status=resolution.status,
                       reason=resolution.reason)
    else:
        logger.info(event, source=resolution.source,
                    model_version=resolution.model_version)


def health() -> dict[str, Any]:
    """Provider state for an operator. Mirrors llm.health()."""
    resolution = resolved_scorer()
    return {
        "requested_scorer": resolution.requested,
        "active_scorer": resolution.source,
        "status": resolution.status,
        "model_version": resolution.model_version,
        "is_modelled": resolution.is_modelled,
        "reason": resolution.reason,
        "implemented_tiers": sorted(_IMPLEMENTED),
        # The two switches an operator most needs to see, because between them
        # they decide whether this scorer touches the running application at all.
        "writes_risk_score": settings.REPAYMENT_WRITE_RISK_SCORE,
        "reprices_open_cases": settings.REPAYMENT_REPRICE_OPEN_CASES,
    }
