"""Login/logout/session endpoints."""

from datetime import timedelta

import structlog
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from stencil import auth
from stencil.api.deps import DbSession
from stencil.config import settings

logger = structlog.get_logger()

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class SessionInfo(BaseModel):
    id: int
    email: str
    username: str | None = None
    role: str = "user"
    is_admin: bool = False


def _session_info(user) -> SessionInfo:
    return SessionInfo(
        id=user.id,
        email=user.email,
        username=user.username,
        role=user.role,
        is_admin=user.role == "admin",
    )


@router.post("/login", response_model=SessionInfo)
def login(body: LoginRequest, response: Response, db: DbSession):
    auth.purge_expired_sessions(db)
    user = auth.authenticate(db, body.email, body.password)
    if user is None:
        # Generic message — never reveal whether the email exists.
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token, _expires_at = auth.create_session(db, user.id)
    response.set_cookie(
        key=auth.SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=auth.session_cookie_secure(),
        samesite="lax",
        # Derive the lifetime from the configured TTL rather than from
        # last_login_at, which is None on a first login and silently degraded
        # the cookie to a browser-session cookie.
        max_age=int(timedelta(days=max(1, settings.session_ttl_days)).total_seconds()),
        path="/",
    )
    logger.info("auth.login", email=user.email)
    return _session_info(user)


@router.post("/logout")
def logout(request: Request, response: Response, db: DbSession):
    auth.destroy_session(db, request.cookies.get(auth.SESSION_COOKIE))
    response.delete_cookie(
        auth.SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=auth.session_cookie_secure(),
        samesite="lax",
    )
    return {"status": "ok"}


@router.get("/me", response_model=SessionInfo)
def me(request: Request, db: DbSession):
    user = auth.validate_session(db, request.cookies.get(auth.SESSION_COOKIE))
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _session_info(user)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
def change_password(body: ChangePasswordRequest, request: Request, db: DbSession):
    """Self-service password change: any signed-in user, own password only.

    Requires the current password (a stolen session cookie alone cannot rotate
    credentials). All OTHER sessions are revoked; the current one stays valid.
    """
    token = request.cookies.get(auth.SESSION_COOKIE)
    user = auth.validate_session(db, token)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not auth.verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=422, detail="New password must be at least 8 characters")
    user.password_hash = auth.hash_password(body.new_password)
    auth.destroy_other_sessions(db, user.id, keep_token=token)
    db.commit()
    logger.info("auth.password_changed", email=user.email)
    return {"status": "ok"}
