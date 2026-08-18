"""Pipeline event publishing via Redis pub/sub for live progress tracking."""

import json
from datetime import UTC, datetime

import structlog

from stencil.config import settings

logger = structlog.get_logger()

# Redis client (lazy init)
_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def publish_event(
    intake_id: str,
    step: str,
    status: str,
    message: str = "",
    details: dict | None = None,
) -> None:
    """Publish a pipeline event to Redis pub/sub channel for an intake.

    The frontend subscribes via WebSocket to receive these in real time.
    """
    event = {
        "type": "pipeline_event",
        "intake_id": intake_id,
        "step": step,
        "status": status,
        "message": message,
        "details": details or {},
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    channel = f"pipeline:{intake_id}"
    try:
        _get_redis().publish(channel, json.dumps(event))
    except Exception as e:
        # Non-critical — don't break the pipeline if Redis is down
        logger.warning("events.publish_failed", intake_id=intake_id, error=str(e))


def publish_completed(intake_id: str) -> None:
    """Publish a completion event so the WebSocket client knows to stop listening."""
    publish_event(intake_id, step="pipeline", status="completed", message="Pipeline completed successfully")


def publish_failed(intake_id: str, error: str) -> None:
    """Publish a failure event."""
    publish_event(intake_id, step="pipeline", status="failed", message=error)
