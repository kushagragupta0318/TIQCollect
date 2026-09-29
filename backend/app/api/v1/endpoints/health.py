import redis as redis_lib
import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import check_db_connection
from app.core.ratelimit import limiter, storage_state

router = APIRouter(tags=["Health"])
logger = structlog.get_logger()


@router.get("/health", summary="Liveness probe")
async def health():
    return {"status": "ok", "service": settings.APP_NAME}


# A plain `def`, so the blocking database and Redis checks run in the threadpool
# instead of stalling the event loop. The status code is the answer: an
# orchestrator or load balancer reads 200 vs 503, not the JSON body.
@router.get("/ready", summary="Readiness probe — checks all dependencies")
def ready():
    checks: dict[str, str] = {}

    checks["database"] = "ok" if check_db_connection() else "error"

    try:
        r = redis_lib.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        r.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        logger.warning("ready.redis_unreachable", error=type(exc).__name__)
        checks["redis"] = "error"

    all_ok = all(v == "ok" for v in checks.values())
    body = {"status": "ready" if all_ok else "degraded", "checks": checks,
            # Informational, never a 503: the fallback still limits, per process.
            "rate_limit_storage": storage_state(limiter)}
    return body if all_ok else JSONResponse(status_code=503, content=body)
