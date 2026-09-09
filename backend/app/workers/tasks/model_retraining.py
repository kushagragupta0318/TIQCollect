"""Train a challenger, validate it, compare it to the incumbent, and stop.

WHAT THIS TASK CANNOT DO, by construction rather than by convention: promote.
`lifecycle.run_candidate` ends at PENDING_APPROVAL and `champion.txt` is written
only by `registry.promote`, which is reachable from an authenticated manager
route and refuses without an APPROVED candidate whose recorded gate and
comparison results are both passes.

IT IS NOT ON THE BEAT SCHEDULE, and that is deliberate. Retraining is not a
thing to do nightly; it is a thing to do when monitoring asks. The 19:15
labelling task enqueues it, once per monitoring EVENT, and the unique constraint
on `model_candidates` makes a duplicate enqueue a no-op rather than a second
training run.

Training on the production cohort takes minutes, not the milliseconds the
labelling task takes, which is the other reason it is a separate task: a long
fit must not sit inside the job that attaches outcomes.
"""
from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()


@celery_app.task(name="app.workers.tasks.model_retraining.run_candidate_training",
                 bind=True, max_retries=0)
def run_candidate_training(self, candidate_id: str):
    """Drive one candidate from TRAINING to its terminal state.

    NO RETRIES, deliberately. `run_candidate` is idempotent by state — a second
    delivery for a candidate that has left TRAINING returns immediately — but a
    retry after a partial failure would be re-fitting on the same data hoping
    for a different answer. Every failure is already recorded on the row with
    its exception type, which is the thing worth having.

    The exception is re-raised after being persisted so the Celery result shows
    a failure rather than a success that quietly did nothing.
    """
    from app.core.database import SessionLocal
    from app.ml.pipeline.lifecycle import run_candidate
    from app.models.model_candidate import CandidateState, ModelCandidate

    db = SessionLocal()
    try:
        cand = run_candidate(db, candidate_id)
        logger.warning("model_retraining.finished", candidate=candidate_id,
                       state=cand.state.value,
                       candidate_version=cand.candidate_version,
                       incumbent=cand.incumbent_version,
                       gates_passed=cand.gates_passed,
                       comparison_passed=cand.comparison_passed,
                       gini_uplift=cand.gini_uplift,
                       rejection_reason=cand.rejection_reason,
                       error=cand.error)
        return cand.to_dict()
    except Exception as exc:
        # A crash OUTSIDE lifecycle's own error handling — an unreachable
        # database, a killed worker resumed mid-transaction. The candidate must
        # not be left in TRAINING forever, because that state blocks every
        # future trigger for this model.
        db.rollback()
        try:
            cand = (db.query(ModelCandidate)
                    .filter(ModelCandidate.id == candidate_id).one_or_none())
            if cand is not None and cand.state == CandidateState.TRAINING:
                cand.error = f"{type(exc).__name__}: {exc}"
                cand.transition(CandidateState.FAILED,
                                f"worker raised {type(exc).__name__}")
                db.commit()
        except Exception:                                   # pragma: no cover
            db.rollback()
        logger.error("model_retraining.failed", candidate=candidate_id,
                     error=str(exc), error_type=type(exc).__name__, exc_info=True)
        raise
    finally:
        db.close()
