"""Record one ai.llm_calls metering row, OFF the request path (perf A3).

core/llm._record_usage used to open a THIRD bare SessionLocal() per LLM call
(on top of the request's db + analytics db), so a burst of narrative calls
starved the primary pool. It now enqueues this fire-and-forget task instead: the
write runs on a Celery worker with its own pool and the long job timeout, never
holding a request-path connection.

Isolation (the reason _record_usage always used its own session) is preserved —
this is an independent transaction, so a usage row can neither see nor be undone
by the caller's in-flight transaction. It also RESOLVES the RLS-cutover note:
the worker runs as tiq_jobs (BYPASSRLS), so the tenant-less insert is fine after
the API moves to tiq_app, where the old in-request bare session would have been
rejected by ai.llm_calls' policy.
"""
from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()


@celery_app.task(name="app.workers.tasks.llm_usage.record_llm_usage_task",
                 bind=True, max_retries=2, default_retry_delay=10)
def record_llm_usage_task(self, *, bank_id: str | None, provider: str, model: str, feature: str,
                          input_tokens: int, output_tokens: int, cache_tokens: int, cost: float):
    from app.core.database import SessionLocal
    from app.models.llm_call import LLMCall
    db = None
    try:
        db = SessionLocal()
        db.add(LLMCall(
            bank_id=bank_id, provider=provider, model=model, feature=feature,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_tokens=cache_tokens, cost=cost,
        ))
        db.commit()
    except Exception as exc:  # noqa: BLE001 — a metering row never matters more than the request
        if db is not None:
            db.rollback()
        logger.warning("llm.usage_record_failed", feature=feature, model=model,
                       error=str(exc), error_type=type(exc).__name__)
    finally:
        if db is not None:
            db.close()
