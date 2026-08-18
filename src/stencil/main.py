"""FastAPI application entry point for Stencil."""

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from stencil.api.router import api_router
from stencil.config import settings

logger = structlog.get_logger()

# /api/v1 paths reachable without a session. Login must be open (chicken/egg);
# everything else under /api/v1 requires a valid session cookie.
_AUTH_OPEN_PATHS = {"/api/v1/auth/login"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_directories()
    # Import committed disk JSON (profiles/schemas/specs) into MySQL if missing.
    # Runs after `alembic upgrade head` (backend command), so the tables exist.
    try:
        from stencil.db.seed import seed_registries_from_disk

        seed_registries_from_disk()
    except Exception as exc:  # pragma: no cover - never block app startup
        structlog.get_logger().warning("startup.seed_failed", error=str(exc))
    # Create the first admin from ST_ADMIN_EMAIL/ST_ADMIN_PASSWORD while the
    # users table is empty (warns loudly when it stays empty).
    try:
        from stencil import auth as _auth
        from stencil.db.session import SessionLocal

        db = SessionLocal()
        try:
            _auth.bootstrap_admin(db)
        finally:
            db.close()
    except Exception as exc:  # pragma: no cover - never block app startup
        structlog.get_logger().warning("startup.auth_bootstrap_failed", error=str(exc))
    # Warm the accounts snapshot in the background if it's missing or stale. The
    # page always serves the last snapshot; this just keeps it reasonably fresh
    # without ever walking the mount inside a request.
    try:
        from stencil.accounts import sync as _accounts_sync

        if not _accounts_sync.sync_in_progress() and _accounts_sync.snapshot_is_stale(
            settings.accounts_sync_ttl_hours
        ):
            from stencil.tasks.worker import accounts_sync_task

            _accounts_sync.write_sync_status({
                "status": "queued", "customers_done": 0, "customers_total": None,
                "accounts_found": 0, "error": None,
            })
            accounts_sync_task.delay()
            structlog.get_logger().info("startup.accounts_sync_queued")
    except Exception as exc:  # pragma: no cover - never block app startup
        structlog.get_logger().warning("startup.accounts_sync_failed", error=str(exc))
    yield


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_session(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Session gate on /api/v1 (HTTP-only cookie set by /auth/login).

    Open: everything outside /api/v1 (/, /health, /docs) and /auth/login.
    WebSocket upgrades don't pass through HTTP middleware — the live-events
    socket stays open (read-only stream; tightening tracked in backlog).
    Lockout safety: while the users table is EMPTY (fresh install before
    bootstrap), requests are allowed — bootstrap_admin logs the warning.
    """
    path = request.url.path
    if not path.startswith("/api/v1") or path in _AUTH_OPEN_PATHS:
        return await call_next(request)

    from stencil import auth as _auth
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        user = _auth.validate_session(db, request.cookies.get(_auth.SESSION_COOKIE))
        if user is None and _auth.user_count(db) > 0:
            return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    except Exception:  # pragma: no cover - DB down: fail closed for /api/v1
        return JSONResponse(status_code=503, content={"detail": "Auth backend unavailable"})
    finally:
        db.close()
    return await call_next(request)


app.include_router(api_router)


@app.get("/")
async def root():
    return {
        "status": "ok",
        "app": settings.app_name,
        "health": "/health",
        "docs": "/docs",
        "api": "/api/v1",
    }


@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.app_name}
