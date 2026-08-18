"""Small audit/blame helpers for user-visible configuration changes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from stencil.db.models import AuditEvent, User

SYSTEM_ACTOR = "system"


def actor_label(user: User | None, fallback: str = SYSTEM_ACTOR) -> str:
    if user is None:
        return fallback
    return (user.username or user.email or fallback).strip() or fallback


def actor_snapshot(user: User | None) -> dict[str, Any]:
    return {
        "user_id": user.id if user else None,
        "email": user.email if user else None,
        "username": user.username if user else None,
        "display": actor_label(user),
    }


def audit_stamp(user: User | None, when: datetime | None = None) -> dict[str, Any]:
    return {
        **actor_snapshot(user),
        "at": (when or datetime.now()).isoformat(),
    }


def append_event(
    db: Session,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    actor: User | None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor_user_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        actor_username=actor.username if actor else None,
        event_metadata=metadata or {},
    )
    db.add(event)
    return event
