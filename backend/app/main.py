# ─── CHANGELOG (prototype → product) ───
# 2026-07-21 — Added the AppException handler below (line 70-75) so
# services can raise a stable error `code` alongside the existing
# `detail` message string, per final_changes.md §8 ("typed error codes,
# not free text"). Nothing else in this file changed. See changelog.md
# for full context.
# 2026-07-31 — Mounted the Command Centre contract router (bottom of file).
# It hangs off the app directly rather than off api_router because the
# integration contract fixes its paths at /api/field-ops/*, outside our
# /api/v1 namespace. See api/v1/endpoints/field_ops.py for the mapping.
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import structlog

from app.core.config import settings
from app.core.database import engine
from app.core.errors import AppException
from app.api.v1.router import api_router
from app.api.v1.endpoints import field_ops

logger = structlog.get_logger()

limiter = Limiter(key_func=get_remote_address, default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"])


def _sync_demo_contact() -> None:
    """DEMO_MODE only: on startup, force the showcase customer (DEMO0003)
    name/phone to match DEMO_CONTACT_NAME / DEMO_CONTACT_PHONE in .env. Lets you
    swap the demo number (CEO / manager / teammate) with a .env edit + restart —
    no reseed, no SQL. No-op if the customer isn't present."""
    from sqlalchemy.orm import Session
    from app.models.customer import Customer

    with Session(engine) as db:
        cust = db.query(Customer).filter(Customer.customer_ref == settings.DEMO_CONTACT_REF).first()
        if cust is None:
            return
        cust.full_name = settings.DEMO_CONTACT_NAME
        cust.phone_primary = settings.DEMO_CONTACT_PHONE
        db.commit()
        logger.info("demo_contact_synced", name=settings.DEMO_CONTACT_NAME,
                    phone=settings.DEMO_CONTACT_PHONE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", service=settings.APP_NAME, env=settings.APP_ENV)
    if settings.DEMO_MODE:
        try:
            _sync_demo_contact()
        except Exception as exc:  # never block startup on a demo convenience
            logger.warning("demo_contact_sync_failed", error=str(exc))
    yield
    logger.info("shutdown", service=settings.APP_NAME)
    engine.dispose()


app = FastAPI(
    title="TIQCollect API",
    description="Enterprise Collections Management System — Backend API",
    version="1.0.0",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    response = await call_next(request)
    logger.info(
        "http_request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        ip=request.client.host if request.client else None,
    )
    return response


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code.value},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


app.include_router(api_router)
# Command Centre integration contract — paths are fixed at /api/field-ops/*,
# so this cannot sit under api_router's /api/v1 prefix.
app.include_router(field_ops.router)


# ── Serve the built React SPA (production / Docker only) ────────────────────
# The frontend build is copied to ./static in the image; in local development
# that directory does not exist and this whole block is skipped, leaving the
# Vite dev server and its /api proxy in charge exactly as before.
#
# Mounted AFTER every router: the catch-all below matches any path, so anything
# registered after it would be unreachable.
#
# axios already uses a relative "/api/v1" base with no environment override, so
# serving the SPA from this same origin needs no frontend change and no CORS
# entry — the browser only ever talks to one host.
_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")

if os.path.isdir(_STATIC_DIR):
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    _assets = os.path.join(_STATIC_DIR, "assets")
    if os.path.isdir(_assets):
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        """Serve a real file when one exists, otherwise index.html.

        The fallback is what makes client-side routes like /agent/cases work on
        a hard refresh or a pasted link — the server has no such route, so
        without it every deep link would 404.
        """
        # Never let an unmatched API path fall through to index.html: a caller
        # would get 200 and a page of HTML instead of an honest 404.
        if full_path.startswith(("api/", "ws/")):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = os.path.join(_STATIC_DIR, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
