"""Score the generated agencies' open cases with the real recovery_risk
model (lane L6, 2026-10-01). Not generator-only truth: these are genuine
`ml.model_predictions` rows, the same table and the same scoring service the
live product uses — this book shows the AI working, it doesn't fake it.

Why `as_of=anchor` and not each case's own placement date: ml_scoring_service
.build_features()'s own docstring says scoring a past date reads TODAY's
loan state against a history filtered to that past date, and calls that
"not honest" — correct only for `as_of=today`, "the only way the product
calls it. Backfilling requires RepaymentSnapshot, not this." A generated
book's "today" is the anchor date, so every open case is scored once, there,
exactly the way the product's own code is designed to be called — not
"what would the model have said when this case was placed" (which this
service cannot answer honestly), but "what does the model say about this
case right now," which Recovery vs Expected can honestly compare against
collection-to-date.

Aravalli is excluded: its v1-copied predictions are real too, just already
there (fixtures/README.md), and re-scoring it would mix two different
scoring regimes (v1's model version against v2's) under one anchor date.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.demo import roster as R
from app.models.case import RESOLVED_STATUSES, Case
from app.models.model_prediction import ModelPrediction
from app.services.ml_scoring_service import MLScoringService


@dataclass
class ScoringResult:
    cases_considered: int
    cases_modelled: int
    cases_declined: int


def score_generated_books(session: Session, *, bank_key: str = "GIRIVAN") -> ScoringResult:
    """Every open case of every generated (non-Aravalli) agency under
    `bank_key`, scored once at the roster's anchor date. Returns counts for
    the manifest; writes nothing the caller has not committed.

    Refuses rather than duplicate: ModelPrediction carries no unique
    constraint on (case_id, as_of_date), so calling this twice would not
    overwrite, it would double the row count and silently lie about how much
    of the book is modelled — the same failure mode run()'s own "already has
    generated agencies" guard exists to prevent, one layer down."""
    bank_id = R.BANK["id"] if bank_key == "GIRIVAN" else R.KUMAON_BANK["id"]
    generated_agency_ids = {
        a.id for a in R.AGENCIES if a.key != "ARAVALLI" and a.bank_key == bank_key
    }
    if not generated_agency_ids:
        return ScoringResult(0, 0, 0)

    already = session.query(ModelPrediction.id).filter(
        ModelPrediction.model_name == "recovery_risk", ModelPrediction.agency_id.in_(generated_agency_ids),
    ).limit(1).first()
    if already is not None:
        raise RuntimeError(f"recovery_risk predictions already exist for {bank_key}'s generated agencies; "
                           "refusing to score twice")

    cases = (
        session.query(Case)
        .filter(Case.bank_id == bank_id, Case.agency_id.in_(generated_agency_ids),
               Case.status.notin_(RESOLVED_STATUSES))
        .all()
    )
    service = MLScoringService(session)
    probabilities, rows = service.score_cases_and_log(cases, model="recovery_risk", as_of=R.ANCHOR_DATE)
    session.add_all(rows)
    session.flush()        # the caller's engine.begin() owns the commit, not this session
    modelled = sum(1 for r in rows if r.is_modelled)
    return ScoringResult(cases_considered=len(cases), cases_modelled=modelled,
                         cases_declined=len(rows) - modelled)
