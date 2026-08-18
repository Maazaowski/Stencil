"""Cooperative pipeline cancellation via Redis."""

import structlog

from stencil.config import settings

logger = structlog.get_logger()

_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis

        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def _cancel_key(intake_id: str) -> str:
    return f"cancel:{intake_id}"


def request_cancel(intake_id: str) -> None:
    try:
        _get_redis().set(_cancel_key(intake_id), "1", ex=86400)
    except Exception as exc:
        logger.warning("cancel.request_failed", intake_id=intake_id, error=str(exc))


def clear_cancel(intake_id: str) -> None:
    try:
        _get_redis().delete(_cancel_key(intake_id))
    except Exception as exc:
        logger.warning("cancel.clear_failed", intake_id=intake_id, error=str(exc))


def is_cancelled(intake_id: str) -> bool:
    try:
        return bool(_get_redis().get(_cancel_key(intake_id)))
    except Exception as exc:
        logger.warning("cancel.check_failed", intake_id=intake_id, error=str(exc))
        return False


class PipelineCancelledError(Exception):
    """Raised when pipeline processing was cancelled by the user."""


def check_cancelled(intake_id: str) -> None:
    if is_cancelled(intake_id):
        raise PipelineCancelledError("Processing cancelled by user")
