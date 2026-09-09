"""Attach realised outcomes to matured model predictions.

WHY THIS TASK EXISTS. `model_predictions` recorded 916 served scores and sat at
zero labelled rows, because nothing ever compared them against what happened.
A model that scores and is never judged cannot be monitored, cannot be retrained
on its own errors, and cannot be shown to have decayed — CLAUDE.md's feature #20
in one sentence.

IT USES THE MODEL'S OWN DEFINITION, not the repayment labeller's. The rule lives
in app/ml/pipeline/outcomes.py and nowhere else; this task only schedules it.
Reusing RepaymentService.attach_outcomes would have silently relabelled the
model against a 0.9 threshold on a different denominator — changing the target
`recovery_risk` was validated on, and quietly invalidating every metric in
ml/artifacts.

IT ALSO MEASURES THE DISAGREEMENT. After labelling, `label_comparison.compare_all`
records what the repayment labeller WOULD have said about each of the same rows,
over the same as_of_date and horizon. That is stored per row and reported here,
and it changes nothing: the label the model is judged on is still the model's
own. Choosing between the two definitions is a decision to take once the
disagreement rate is a number rather than an argument.

IT MONITORS ONLY WHEN MONITORING CAN MEAN SOMETHING. After labelling and the
comparison, `monitor.readiness()` asks — in three COUNTs, no DataFrame — whether
enough matured outcomes exist ON THE SERVING MODEL VERSION AND THE CURRENT
OUTCOME DEFINITION. Below the threshold it logs `not_ready` at INFO with the
matured and required counts and stops. Above it, `monitor_model` runs.

Both the comparison and the monitor are contained: each is a read-only
diagnostic that runs after `actual_outcome` is already committed, so neither can
influence a label, and a raise in either is logged and recorded in the return
value rather than failing the task. A labelling run that did its work correctly
must not report itself failed because a read-out beside it broke.

SCHEDULED AT 19:15, ahead of the 19:30 ingest and the 19:45 repayment labeller.
Order matters: ingest_daily applies bank actions (SETTLED / WRITTEN_OFF /
RECALL / DECEASED) to cases, and running after it would censor a prediction on a
bank action that landed AFTER the outcome window closed. Labelling first means
each prediction is judged against the state that existed during its own window.
"""
from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()


def _comparison_digest(db, model_name: str) -> dict:
    """The dual-labelling read-out, minus the per-row examples.

    The full report carries up to ten example rows for eyeballing; the task
    return value goes into the Celery result backend and the structlog line, so
    it keeps the counts and drops the payload. `compare_all` has already written
    every row's detail to `model_predictions.label_comparison`, which is where
    the examples should be read from anyway.
    """
    from app.ml.pipeline.label_comparison import compare_all

    report = compare_all(db, model_name)
    return {k: v for k, v in report.items() if k != "examples"}


def _monitoring_digest(db, model_name: str) -> dict:
    """Run drift/performance monitoring, but only once it can say anything.

    THE GATE IS THE POINT. `monitor_model` needs matured outcomes, and the first
    of ours matures 2026-10-08. Scheduling it unconditionally would emit a
    `insufficient_data` verdict every night for a month before it ever carried a
    signal — and a monitoring line that is noise 30 times before it is news once
    has already taught everyone to scroll past it. `readiness()` costs three
    COUNTs and no DataFrame, so the nightly cost of not-yet-ready is negligible.

    NOT-READY IS INFO, NOT A WARNING. Nothing is wrong when outcomes have not
    matured; it is the expected state of a young model. Logging it at warning
    would make a healthy system look broken, which is the same failure as the
    noise above wearing a different severity.

    VERSION CONSISTENCY IS ENFORCED BY THE GATE, not checked afterwards. The
    count is of rows matching BOTH the serving model version and the current
    outcome definition, so a mixed population cannot reach the threshold by
    borrowing rows labelled under a rule the model was never validated against.
    """
    from app.ml.pipeline.monitor import monitor_model, readiness

    gate = readiness(db, model_name)
    if not gate.ready:
        logger.info("model_outcomes.monitor_not_ready", **gate.to_dict())
        return {"status": gate.status, **gate.to_dict()}

    report = monitor_model(db, model_name,
                           version=gate.model_version,
                           outcome_definition_version=gate.outcome_definition_version)
    digest = report.to_dict()
    # The decile and calibration tables are for a person reading a report, not
    # for the Celery result backend. Counts, metrics and the verdict travel.
    for heavy in ("decile_table", "calibration_table"):
        digest.pop(heavy, None)
    logger.info("model_outcomes.monitored", model=model_name,
                verdict=report.verdict,
                retrain_recommended=report.retrain_recommended,
                n_matured=report.n_matured, version=report.version,
                outcome_definition_version=report.outcome_definition_version)
    return {"status": "ready", **digest}


def _retrain_trigger(db, model_name: str, monitoring: dict) -> dict:
    """Open a candidate when — and only when — monitoring genuinely asked.

    FOUR REASONS NOT TO RETRAIN, and each is a different fact rather than a
    shade of the same one:

      * `not_ready`      — too few matured outcomes to judge anything;
      * `insufficient_outcome_variation` — enough rows, one class, so
        discrimination is undefined. Emphatically not evidence of decay;
      * `insufficient_data` — the monitor ran and could not compute;
      * `healthy`         — it judged, and the model is fine.

    Only `retrain_recommended` proceeds. `start_retraining` then applies the
    idempotency rules — one candidate per monitoring event, and never a second
    while one is still active — and returns None for each, so a suppressed
    trigger is reported rather than silently skipped.
    """
    from app.ml.pipeline.lifecycle import monitoring_event_id, start_retraining

    verdict = monitoring.get("verdict")
    if monitoring.get("status") != "ready":
        return {"status": "skipped", "why": monitoring.get("status") or "unknown"}
    if not monitoring.get("retrain_recommended"):
        return {"status": "not_needed", "verdict": verdict}

    cand = start_retraining(db, model_name, monitoring)
    if cand is None:
        return {"status": "suppressed", "verdict": verdict,
                "monitoring_run_id": monitoring_event_id(monitoring),
                "why": "duplicate monitoring event, an active candidate already "
                       "exists, or ML_AUTO_RETRAIN_ENABLED is off"}

    from app.workers.tasks.model_retraining import run_candidate_training
    run_candidate_training.delay(cand.id)
    logger.warning("model_outcomes.retraining_enqueued", model=model_name,
                   candidate=cand.id, monitoring_run_id=cand.monitoring_run_id,
                   reasons=cand.trigger_reasons)
    return {"status": "enqueued", "candidate_id": cand.id,
            "monitoring_run_id": cand.monitoring_run_id,
            "reasons": cand.trigger_reasons, "verdict": verdict}


@celery_app.task(name="app.workers.tasks.model_outcomes.attach_model_outcomes",
                 bind=True)
def attach_model_outcomes(self, model_name: str = "recovery_risk"):
    from app.core.database import SessionLocal
    from app.ml.pipeline.outcomes import attach_outcomes

    db = SessionLocal()
    try:
        summary = attach_outcomes(db, model_name)
        logger.info("model_outcomes.done", **summary)

        # ── The other labeller's opinion, measured and stored ────────────────
        # 2026-09-08. Runs AFTER the real labelling and writes only
        # `label_comparison`, so `actual_outcome` is already final and cannot be
        # influenced by it. Its failure is logged and swallowed on purpose: this
        # is a monitoring read-out, and an outcome labeller that has already
        # committed correct labels must not be reported as failed because a
        # diagnostic beside it raised.
        try:
            summary["label_comparison"] = _comparison_digest(db, model_name)
        except Exception as exc:
            logger.warning("model_outcomes.comparison_failed", model=model_name,
                           error=str(exc), error_type=type(exc).__name__,
                           exc_info=True)
            summary["label_comparison"] = {"error": type(exc).__name__}

        # ── Drift and performance monitoring, gated on maturity ─────────────
        # 2026-09-09. Same containment as the comparison above and for the same
        # reason: labelling has already committed, and a read-only monitor that
        # raises must not turn a successful labelling run into a failed task.
        # The error is recorded in the return value as well as logged, so a
        # swallowed failure is still visible to whoever reads the result.
        try:
            summary["monitoring"] = _monitoring_digest(db, model_name)
        except Exception as exc:
            logger.warning("model_outcomes.monitoring_failed", model=model_name,
                           error=str(exc), error_type=type(exc).__name__,
                           exc_info=True)
            summary["monitoring"] = {"status": "error",
                                     "error": type(exc).__name__}

        # ── The retrain trigger — 2026-09-09 ────────────────────────────────
        # Until today `retrain_recommended` was a boolean in the dict above and
        # nothing read it. This is the reader. It opens a CANDIDATE; it cannot
        # promote one, and the candidate cannot reach production without a
        # person (see ml/pipeline/lifecycle.py).
        #
        # Contained like the two diagnostics before it and for the same reason:
        # labelling has already committed, and a retraining orchestrator that
        # raises must not report a correct labelling run as failed.
        try:
            summary["retraining"] = _retrain_trigger(db, model_name,
                                                     summary.get("monitoring") or {})
        except Exception as exc:
            logger.error("model_outcomes.retrain_trigger_failed", model=model_name,
                         error=str(exc), error_type=type(exc).__name__,
                         exc_info=True)
            summary["retraining"] = {"status": "error",
                                     "error": type(exc).__name__}

        return summary
    except Exception as exc:
        db.rollback()
        # Reported, never swallowed. Two scheduled tasks in this repo were once
        # found reporting work they had not done (fixed 2026-09-06), and an
        # outcome labeller that fails quietly leaves the feedback loop looking
        # closed while it is open.
        logger.error("model_outcomes.failed", model=model_name, error=str(exc),
                     error_type=type(exc).__name__, exc_info=True)
        raise
    finally:
        db.close()
