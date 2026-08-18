"""Shared API dependencies — DB session, pagination parameters."""

from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from stencil import auth
from stencil.db.models import User
from stencil.db.session import get_db

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(request: Request, db: DbSession) -> User | None:
    user = auth.validate_session(db, request.cookies.get(auth.SESSION_COOKIE))
    if user is None:
        if auth.user_count(db) == 0:
            return None
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_current_user(current_user: Annotated[User | None, Depends(get_current_user)]) -> User:
    if current_user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return current_user


def require_admin(current_user: Annotated[User | None, Depends(get_current_user)]) -> User | None:
    """Admin gate.

    ``current_user`` is None only in the fresh-install window where the users
    table is still empty (``get_current_user`` allows it so the first admin can
    be bootstrapped). Once any user exists this is always a real ``User``, so a
    non-admin is rejected here.
    """
    if current_user is None:
        return None
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


CurrentUser = Annotated[User | None, Depends(get_current_user)]
RequiredUser = Annotated[User, Depends(require_current_user)]
AdminUser = Annotated[User | None, Depends(require_admin)]


class PaginationParams(BaseModel):
    page: int = 1
    per_page: int = 25


def get_pagination(
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
) -> PaginationParams:
    return PaginationParams(page=page, per_page=per_page)


Pagination = Annotated[PaginationParams, Depends(get_pagination)]
