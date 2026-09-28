# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-09 — NEW. The training frame built from what the product actually
#   served and what actually happened, replacing the synthetic panel for the
#   RETRAINING path only.
#
#   `scripts/build_modelling_dataset.py` drives `book_simulator` and stays
#   exactly as it is: it is how a developer gets a frame in two seconds, and it
#   is what champion 1.1.0 was trained on. It is NOT reachable from this module,
#   and `build_training_frame` raises rather than degrading to it — a retraining
#   run that quietly trained on simulated borrowers and then reported a Gini
#   would be the single worst failure this system could have.
# ───────────────────────────────────────────────────────────────────────────
"""Production model predictions + their matured outcomes, as a training frame.

    frame, cohort = build_training_frame(db, "recovery_risk")

POINT-IN-TIME CORRECTNESS IS STRUCTURAL HERE, not a discipline. Every feature
value is read from `ModelPrediction.features` — the vector that was FROZEN at
scoring time and handed to the model that made the decision. Nothing is
recomputed from `Loan` or `Case`, which are overwritten in place and would leak
the future if read now. The label comes from `ModelPrediction.actual_outcome`,
written by `ml/pipeline/outcomes.py` and by nothing else.

So the frame cannot contain a post-prediction feature, because the only source
of features is a row written before the outcome window opened.

WHAT THIS USED TO COST, AND SINCE WHEN IT DOES NOT. Until 2026-09-15
`score_cases_and_log` stored only the CHAMPION'S SELECTED features (four, for
recovery_risk 1.1.0), so a challenger trained here could only re-select among
the same four. It now stores the full candidate vector (`config.LOGGED_FEATURES`,
50 keys). The limitation is still real for every row logged BEFORE that date —
those carry four keys and put the other 46 in the Missing bin — and it is still
recorded on every cohort as `feature_source`, which now reports the number of
distinct feature keys the frame actually holds, so a reader can tell a
four-feature cohort from a fifty-feature one without opening the rows.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.orm import Session

from app.models.model_prediction import ModelPrediction

logger = logging.getLogger(__name__)

# ── how much real data is enough ────────────────────────────────────────────
# DERIVED, NOT CHOSEN, and derived the same way MIN_MATURED_FOR_MONITORING was.
# The out-of-time slice is the only read that counts, and a Gini measured on it
# has to be precise enough to compare against the incumbent: at the development
# bad rate (0.718) and AUC (0.757), Hanley-McNeil gives SE(AUC) ~= 0.022 at
# n=500, so a 95% interval on Gini spans about +/- 0.085. Below that the
# comparison in `comparison.py` cannot distinguish a better model from a luckier
# one, and promoting on it would be noise wearing a decision.
MIN_OOT_ROWS = 500
#: The spec splits 60/15/25 by period, so an OOT slice of 500 needs 2,000 rows.
#: Stated as its own constant because the split fractions can change and this
#: floor is about the OOT read, not about the total.
MIN_TRAINING_ROWS = 2_000
#: Both classes must be present, and the minority class must be able to fill
#: the WOE binner's minimum bin (`Gates.min_bin_events`). Fewer than this and
#: binning degenerates into one bin per feature, which trains a model that is
#: an intercept.
MIN_MINORITY_ROWS = 50
#: A chronological split needs periods to split ON. With fewer distinct as-of
#: dates than this, the 60/15/25 cut cannot produce three non-empty slices.
MIN_DISTINCT_PERIODS = 8


class InsufficientProductionData(RuntimeError):
    """Raised instead of returning a frame that would train a misleading model.

    Carries `.detail` so the orchestrator can persist exactly which floor was
    not met rather than a message a human has to parse.
    """

    def __init__(self, message: str, detail: dict):
        super().__init__(message)
        self.detail = detail


@dataclass
class TrainingCohort:
    """Everything needed to rebuild this exact frame, or to prove two runs did
    not use the same one."""

    model_name: str
    outcome_definition_version: str
    horizon_days: int | None
    n_rows: int
    n_recovered: int
    n_not_recovered: int
    bad_rate: float
    first_as_of: str | None
    last_as_of: str | None
    n_periods: int
    model_versions: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    feature_source: str = (
        "ModelPrediction.features — the frozen vector served at prediction "
        "time. Rows logged before 2026-09-15 carry only the serving model's "
        "selected features; later rows carry the full candidate set."
    )
    #: Sorted prediction ids, hashed. Two cohorts with the same digest are the
    #: same rows; a different digest is a different experiment.
    cohort_digest: str = ""
    excluded: dict = field(default_factory=dict)
    built_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _digest(prediction_ids: list[str]) -> str:
    h = hashlib.sha256()
    for pid in sorted(prediction_ids):
        h.update(pid.encode())
    return h.hexdigest()


def build_training_frame(
    db: Session,
    model_name: str = "recovery_risk",
    *,
    outcome_definition_version: str | None = None,
    min_rows: int | None = None,
) -> tuple[pd.DataFrame, TrainingCohort]:
    """The frame, and the audit record of what went into it.

    Raises `InsufficientProductionData` rather than returning something
    trainable-but-meaningless. The caller records the detail and stops; it must
    never reach for the synthetic panel.
    """
    from app.ml.pipeline.outcomes import OUTCOME_DEFINITION_VERSION

    odv = outcome_definition_version or OUTCOME_DEFINITION_VERSION
    floor = MIN_TRAINING_ROWS if min_rows is None else min_rows

    rows = (
        db.query(ModelPrediction)
        .filter(ModelPrediction.model_name == model_name,
                # A declined score has no probability and no usable feature
                # vector; it is evidence about coverage, not about a borrower.
                ModelPrediction.is_modelled.is_(True),
                # CENSORED AND UNLABELLED ROWS ARE EXCLUDED BY THIS ONE CLAUSE.
                # `actual_outcome` is NULL for every non-terminal status the
                # labeller can assign — NOT_MATURED, NO_BASELINE, and all four
                # CENSORED_* — so "the bank withdrew the account" never becomes
                # a training example of borrower behaviour.
                ModelPrediction.actual_outcome.isnot(None),
                # One labelling rule per frame. Rows labelled under a different
                # definition answer a different question.
                ModelPrediction.outcome_definition_version == odv)
        .order_by(ModelPrediction.scored_at.asc())
        .all()
    )

    excluded = {"duplicate_rescores": 0, "missing_features": 0}

    # ONE ROW PER (entity, as_of), LAST SCORED WINS — the same rule the monitor
    # applies, and for the same reason: a re-plan re-scores the whole pool, so
    # nine near-identical rows for one account-day would be nine copies of one
    # observation. Training on them inflates n, correlates the errors and makes
    # the OOT interval look tighter than the evidence supports.
    seen: dict[tuple, int] = {}
    deduped: list[ModelPrediction] = []
    for r in rows:
        key = (r.entity_id, r.as_of_date)
        if key in seen:
            deduped[seen[key]] = r
            excluded["duplicate_rescores"] += 1
        else:
            seen[key] = len(deduped)
            deduped.append(r)

    records, ids, versions = [], [], set()
    feature_keys: set[str] = set()
    for r in deduped:
        feats = r.features or {}
        if not feats:
            excluded["missing_features"] += 1
            continue
        feature_keys.update(feats.keys())
        versions.add(r.model_version)
        ids.append(r.id)
        records.append({
            # Traceability, row by row: prediction -> case/loan -> as-of ->
            # outcome. Kept in the frame so a training row can be walked back
            # to the decision it came from.
            "prediction_id": r.id,
            "loan_id": r.loan_id,
            "case_id": r.case_id,
            "borrower_id": r.entity_id,
            "as_of_date": r.as_of_date,
            "served_model_version": r.model_version,
            "y": int(r.actual_outcome),
            **feats,
        })

    if not records:
        raise InsufficientProductionData(
            f"no matured, modelled predictions for '{model_name}' under "
            f"outcome definition {odv}",
            {"reason": "no_rows", "n_rows": 0, "required_rows": floor,
             "outcome_definition_version": odv, "excluded": excluded})

    frame = pd.DataFrame(records)
    # The chronological axis. The spec splits by MONTH on the synthetic panel,
    # which spans two years; a production cohort spans weeks, so months would
    # give one period and no split at all. The day is the finest honest period
    # available and keeps the split strictly chronological either way.
    # A segment label, NOT a feature. Derived from the FROZEN `dpd` in the
    # prediction's own vector, so it is point-in-time by the same construction
    # as everything else here — and it is the one segment dimension the
    # prediction log can supply. `loan_type` and `city` are not stored on a
    # prediction and are deliberately not joined from Loan/Customer: they would
    # be read at today's value, and a segment label that moved after the
    # prediction would quietly regroup the rows.
    if "dpd" in frame.columns:
        from app.models.loan import dpd_bucket_for
        frame["dpd_bucket"] = [
            dpd_bucket_for(int(v)).value if pd.notna(v) else None
            for v in frame["dpd"]
        ]
    # The chronological axis, as an INTEGER ordinal day.
    #
    # It has to be an integer, and finding that out took running the real
    # trainer rather than a stand-in: `train.py` records the split bounds with
    # `int(oot[split_col].max())`, which raises on a date. The synthetic panel
    # uses `month_index`, so nothing had ever handed it anything else.
    #
    # The day rather than the month, because a production cohort spans weeks: by
    # month it is one period and the 60/15/25 split degenerates to a single
    # empty slice. Ordinal days sort exactly as the dates do, so the split stays
    # strictly chronological and `as_of_date` is kept beside it for reading.
    frame["as_of_period"] = pd.to_datetime(frame["as_of_date"]).map(
        lambda d: d.date().toordinal()).astype(int)
    frame = frame.sort_values("as_of_period").reset_index(drop=True)

    n = len(frame)
    n_bad = int(frame.y.sum())
    n_good = n - n_bad
    periods = frame["as_of_period"].nunique()
    cohort = TrainingCohort(
        model_name=model_name,
        outcome_definition_version=odv,
        horizon_days=next((r.outcome_horizon_days for r in deduped
                           if r.outcome_horizon_days), None),
        n_rows=n, n_recovered=n_good, n_not_recovered=n_bad,
        bad_rate=round(n_bad / n, 4),
        first_as_of=str(frame["as_of_period"].min()),
        last_as_of=str(frame["as_of_period"].max()),
        n_periods=int(periods),
        model_versions=sorted(v for v in versions if v),
        features=sorted(feature_keys),
        feature_source=(
            f"ModelPrediction.features — the frozen vector served at prediction "
            f"time; {len(feature_keys)} distinct feature keys in this cohort "
            f"(rows logged before 2026-09-15 carry only the serving model's "
            f"selected features, later rows the full candidate set)"),
        cohort_digest=_digest(ids),
        excluded=excluded,
        built_at=datetime.now(timezone.utc).isoformat(),
    )

    # ── the floors, each with its own message ───────────────────────────────
    minority = min(n_bad, n_good)
    problems = []
    if n < floor:
        problems.append(f"{n} rows, need {floor}")
    if periods < MIN_DISTINCT_PERIODS:
        problems.append(f"{periods} distinct as-of dates, need "
                        f"{MIN_DISTINCT_PERIODS} to split chronologically")
    if minority < MIN_MINORITY_ROWS:
        which = "not-recovered" if n_bad < n_good else "recovered"
        problems.append(f"only {minority} {which} outcomes, need "
                        f"{MIN_MINORITY_ROWS}")
    if problems:
        raise InsufficientProductionData(
            f"production training data is not sufficient for '{model_name}': "
            + "; ".join(problems),
            {"reason": "below_floor", "problems": problems,
             "required_rows": floor, "required_oot_rows": MIN_OOT_ROWS,
             "required_minority": MIN_MINORITY_ROWS,
             "required_periods": MIN_DISTINCT_PERIODS,
             **cohort.to_dict()})

    logger.info("ml.production_dataset.built model=%s rows=%d bad_rate=%.4f "
                "periods=%d digest=%s", model_name, n, cohort.bad_rate,
                periods, cohort.cohort_digest[:12])
    return frame, cohort

