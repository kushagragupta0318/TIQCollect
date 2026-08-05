from fastapi import APIRouter
from app.core.database import check_db_connection
import redis as redis_lib
from app.core.config import settings

router = APIRouter(tags=["Health"])


@router.get("/health", summary="Liveness probe")
async def health():
    return {"status": "ok", "service": settings.APP_NAME}


@router.get("/ready", summary="Readiness probe — checks all dependencies")
async def ready():
    checks: dict[str, str] = {}

    checks["database"] = "ok" if check_db_connection() else "error"

    try:
        r = redis_lib.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        r.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"

    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "ready" if all_ok else "degraded", "checks": checks}
