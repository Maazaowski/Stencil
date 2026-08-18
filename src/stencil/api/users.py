"""Admin-managed in-app users."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from stencil import auth
from stencil.api.deps import AdminUser, DbSession
from stencil.audit import append_event
from stencil.db.models import User

router = APIRouter(prefix="/users", tags=["users"])

USER_ROLES = {"admin", "user"}


class UserResponse(BaseModel):
    id: int
    email: str
    username: str | None = None
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None
    deleted_at: datetime | None = None
    last_login_at: datetime | None = None

    model_config = {"from_attributes": True}


class UserCreateRequest(BaseModel):
    email: str
    username: str | None = None
    password: str = Field(min_length=8)
    role: str = "user"


class UserUpdateRequest(BaseModel):
    email: str | None = None
    username: str | None = None
    password: str | None = Field(default=None, min_length=8)
    role: str | None = None
    is_active: bool | None = None


def _normalize_email(email: str) -> str:
    value = email.strip().lower()
    if "@" not in value:
        raise HTTPException(status_code=400, detail="Valid email is required")
    return value


def _validate_role(role: str) -> str:
    if role not in USER_ROLES:
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'user'")
    return role


def _active_admin_count(db) -> int:
    return int(
        db.execute(
            select(func.count(User.id)).where(
                User.role == "admin",
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        ).scalar_one()
    )


def _ensure_not_last_admin(db, user: User, *, next_role: str | None = None, next_active: bool | None = None) -> None:
    role = next_role if next_role is not None else user.role
    is_active = next_active if next_active is not None else user.is_active
    if user.role == "admin" and (role != "admin" or not is_active):
        if _active_admin_count(db) <= 1:
            raise HTTPException(status_code=409, detail="Cannot remove the last active admin")


@router.get("", response_model=list[UserResponse])
def list_users(db: DbSession, _admin: AdminUser):
    return list(db.scalars(select(User).order_by(User.created_at.desc(), User.id.desc())).all())


@router.post("", response_model=UserResponse, status_code=201)
def create_user(db: DbSession, body: UserCreateRequest, admin: AdminUser):
    if admin is None and auth.user_count(db) > 0:
        raise HTTPException(status_code=403, detail="Admin access required")
    email = _normalize_email(body.email)
    role = "admin" if auth.user_count(db) == 0 else _validate_role(body.role)
    existing = db.execute(select(User).where(func.lower(User.email) == email)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="User email already exists")
    user = User(
        email=email,
        username=(body.username or "").strip() or None,
        password_hash=auth.hash_password(body.password),
        role=role,
        is_active=True,
        created_by_user_id=admin.id if admin else None,
        updated_by_user_id=admin.id if admin else None,
    )
    db.add(user)
    db.flush()
    append_event(db, entity_type="user", entity_id=str(user.id), action="created", actor=admin)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(db: DbSession, user_id: int, body: UserUpdateRequest, admin: AdminUser):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    role = _validate_role(body.role) if body.role is not None else user.role
    is_active = body.is_active if body.is_active is not None else user.is_active
    _ensure_not_last_admin(db, user, next_role=role, next_active=is_active)

    if body.email is not None:
        email = _normalize_email(body.email)
        existing = db.execute(
            select(User).where(func.lower(User.email) == email, User.id != user.id)
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=409, detail="User email already exists")
        user.email = email
    if body.username is not None:
        user.username = body.username.strip() or None
    if body.password:
        user.password_hash = auth.hash_password(body.password)
    user.role = role
    user.is_active = is_active
    if is_active and user.deleted_at is not None:
        user.deleted_at = None
        user.deleted_by_user_id = None
    user.updated_by_user_id = admin.id if admin else None
    append_event(db, entity_type="user", entity_id=str(user.id), action="updated", actor=admin)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", response_model=UserResponse)
def delete_user(db: DbSession, user_id: int, admin: AdminUser):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    _ensure_not_last_admin(db, user, next_active=False)
    user.is_active = False
    user.deleted_at = datetime.now()
    user.deleted_by_user_id = admin.id if admin else None
    user.updated_by_user_id = admin.id if admin else None
    append_event(db, entity_type="user", entity_id=str(user.id), action="deleted", actor=admin)
    db.commit()
    db.refresh(user)
    return user
