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
# 2026-09-24 — The SPA catch-all keeps static file serving inside the
#   static root (_spa_target); mounting moved into _mount_spa so it can be
#   tested over HTTP. See the note at _spa_target.
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import structlog

from app.core.config import settings
from app.core.database import engine
from app.core.errors import AppException
from app.core.ratelimit import limiter
from app.api.v1.router import api_router
from app.api.v1.endpoints import field_ops

logger = structlog.get_logger()

# `limiter` comes from app.core.ratelimit — see that module for why it is not
# built here and why no SlowAPIMiddleware is added. Only the routes decorated
# with @limiter.limit are limited; nothing was, before 2026-09-14.


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


def _sync_leave_statuses() -> None:
    """Bring Agent.status in step with approved leave. The 00:10 Celery task
    does this nightly, but beat never replays a crontab it slept through, and
    a stale ON_DUTY is what every manager screen used to show for an agent on
    approved leave. Readers now derive duty from the leave table regardless
    (services/leave_service.py); this keeps the stored column honest too."""
    from sqlalchemy.orm import Session
    from app.services.leave_service import LeaveService

    with Session(engine) as db:
        out = LeaveService(db).sync_statuses()
        logger.info("leave_statuses_synced", **out)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", service=settings.APP_NAME, env=settings.APP_ENV)
    if settings.DEMO_MODE:
        try:
            _sync_demo_contact()
        except Exception as exc:  # never block startup on a demo convenience
            logger.warning("demo_contact_sync_failed", error=str(exc))
    try:
        _sync_leave_statuses()
    except Exception as exc:  # a missing table on a first boot must not block startup
        logger.warning("leave_status_sync_failed", error=str(exc))
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
class _GZipExceptEventStream(GZipMiddleware):
    """GZip everything except the live event stream.

    2026-09-24 — found by measuring, not reading: /events/stream connected
    (200) and the event reached Redis, yet a client waited 8 s and received
    nothing. Starlette 0.41's GZipMiddleware compresses `text/event-stream`
    like any other body (newer Starlette exempts it), and zlib holds small
    writes back until a deflate block fills — so each ~300-byte event sat in
    the compressor indefinitely. Any client that sends Accept-Encoding: gzip
    (every browser, httpx by default) saw a silent, "connected" stream.
    """

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").endswith("/events/stream"):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app.add_middleware(_GZipExceptEventStream, minimum_size=1000)


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


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


# 2026-09-24 — static file serving could read outside the static root. The
# requested path is now checked on its face (no NUL byte, not absolute, no
# drive, still under static/ after normpath) and then resolved with realpath,
# which also follows symlinks; anything that does not land inside static/
# answers the normal 404. The api/ and ws/ refusal is unchanged.
def _spa_target(full_path: str, static_dir: str) -> str | None:
    """What the SPA catch-all should serve for `full_path`, or None for a 404."""
    # Never let an unmatched API path fall through to index.html: a caller
    # would get 200 and a page of HTML instead of an honest 404.
    if full_path.startswith(("api/", "ws/")):
        return None
    # Refused before the filesystem is touched: a NUL byte (realpath raises on
    # it), an absolute path or a drive (os.path.join would drop the root), and
    # a path that leaves static/ on its face.
    if "\x00" in full_path or os.path.isabs(full_path) or os.path.splitdrive(full_path)[0]:
        return None
    root = os.path.realpath(static_dir)
    if not _within(os.path.normpath(os.path.join(root, full_path)), root):
        return None
    try:
        candidate = os.path.realpath(os.path.join(root, full_path))
    except (ValueError, OSError):
        return None
    if not _within(candidate, root):
        return None                  # a symlink that leaves static/
    if full_path and os.path.isfile(candidate):
        return candidate
    # 2026-09-24 (I01) — a MISSING *.js is an honest 404, not index.html: a
    # stale client asking for an old hashed chunk after a deploy, or a service
    # worker polling a rolled-back /sw.js, must read "gone", not get a page of
    # HTML it tries to run as a script. A real .js file is served above.
    if full_path.lower().endswith(".js"):
        return None
    return os.path.join(root, "index.html")


def _mount_spa(app: FastAPI, static_dir: str) -> None:
    """Serve the built SPA from `static_dir`: /assets as static files, every
    other unmatched path through _spa_target. A function so a test can mount
    it on a fresh app over a temporary directory and go through HTTP."""
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    assets = os.path.join(static_dir, "assets")
    if os.path.isdir(assets):
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        """Serve a real file when one exists, otherwise index.html — except a
        missing *.js path, which is an honest 404 (see _spa_target above).

        The fallback is what makes client-side routes like /agent/cases work on
        a hard refresh or a pasted link — the server has no such route, so
        without it every deep link would 404.
        """
        target = _spa_target(full_path, static_dir)
        if target is None:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(target)


if os.path.isdir(_STATIC_DIR):
    _mount_spa(app, _STATIC_DIR)
