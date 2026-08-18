"""Login/session primitives: bcrypt passwords, opaque cookie sessions.

Security posture (pilot-appropriate, single tenant):
- Passwords stored only as bcrypt hashes (users.password_hash).
- Session cookie carries an opaque random token; the DB stores only its
  SHA-256 (auth_sessions.token_hash), so a DB leak exposes no live sessions.
- Expired sessions are purged opportunistically on login.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

import bcrypt
import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from stencil.config import settings
from stencil.db.models import AuthSession, User

logger = structlog.get_logger()

SESSION_COOKIE = "stencil_session"


def session_cookie_secure() -> bool:
    """Resolve the cookie's Secure flag.

    Explicit ST_SESSION_COOKIE_SECURE wins; otherwise Secure is ON unless the
    app is in debug mode (where it is served over plain HTTP and the browser
    would discard a Secure cookie).
    """
    configured = settings.session_cookie_secure
    if configured is not None:
        return bool(configured)
    return not settings.debug


# ── Passwords ───────────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


# ── Sessions ────────────────────────────────────────────────────────────────


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user_id: int) -> tuple[str, datetime]:
    """Create a session row and return (raw cookie token, expiry)."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(days=max(1, settings.session_ttl_days))
    db.add(AuthSession(token_hash=_token_hash(token), user_id=user_id, expires_at=expires_at))
    db.commit()
    return token, expires_at


def validate_session(db: Session, token: str | None) -> User | None:
    """Return the session's user when the token is valid and unexpired."""
    if not token:
        return None
    row = db.get(AuthSession, _token_hash(token))
    if row is None or row.expires_at < datetime.now():
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        return None
    return user


def destroy_session(db: Session, token: str | None) -> None:
    if not token:
        return
    row = db.get(AuthSession, _token_hash(token))
    if row is not None:
        db.delete(row)
        db.commit()


def purge_expired_sessions(db: Session) -> None:
    db.execute(delete(AuthSession).where(AuthSession.expires_at < datetime.now()))
    db.commit()


def destroy_other_sessions(db: Session, user_id: int, *, keep_token: str | None) -> None:
    """Revoke every session for a user except the one presenting ``keep_token``.

    Used after a password change so any other device/browser is signed out.
    Does not commit — callers batch it with the password update.
    """
    stmt = delete(AuthSession).where(AuthSession.user_id == user_id)
    if keep_token:
        stmt = stmt.where(AuthSession.token_hash != _token_hash(keep_token))
    db.execute(stmt)


# ── Login / bootstrap ───────────────────────────────────────────────────────


def authenticate(db: Session, email: str, password: str) -> User | None:
    """Verify credentials; returns the user or None (no user enumeration)."""
    user = db.execute(
        select(User).where(func.lower(User.email) == email.strip().lower())
    ).scalar_one_or_none()
    if user is None:
        # Burn comparable time so missing-user and wrong-password are
        # indistinguishable to a timing observer.
        verify_password(password, hash_password("timing-equalizer"))
        return None
    if not verify_password(password, user.password_hash):
        return None
    if not user.is_active or user.deleted_at is not None:
        return None
    user.last_login_at = datetime.now()
    db.commit()
    return user


def user_count(db: Session) -> int:
    return int(db.execute(select(func.count(User.id))).scalar_one())


def bootstrap_admin(db: Session) -> None:
    """Create the first admin from ST_ADMIN_EMAIL/ST_ADMIN_PASSWORD.

    Runs at startup. Only acts while the users table is EMPTY, so the env vars
    can (and should) be removed after first boot. With an empty table and no
    env creds, login is impossible — warn loudly.
    """
    if user_count(db) > 0:
        return
    if settings.admin_email and settings.admin_password:
        db.add(User(
            email=settings.admin_email.strip().lower(),
            username="Admin",
            password_hash=hash_password(settings.admin_password),
            role="admin",
            is_active=True,
        ))
        db.commit()
        logger.info("auth.bootstrap_admin_created", email=settings.admin_email)
    else:
        logger.warning(
            "auth.no_users",
            message="users table is empty and ST_ADMIN_EMAIL/ST_ADMIN_PASSWORD are not set — "
            "login is impossible and the API runs OPEN until a user exists.",
        )
