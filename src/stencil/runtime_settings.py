"""Persisted runtime settings shared across backend, worker, and watcher."""

from __future__ import annotations

import time
from typing import Any

import structlog

from stencil.config import settings

logger = structlog.get_logger()

RUNTIME_SETTINGS_KEY = "global"
CACHE_TTL_SECONDS = 2.0

OPENAI_MODEL_OPTIONS = [
    "gpt-5.5",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.1",
    "gpt-5",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4o",
    "gpt-4o-mini",
]

MUTABLE_SETTING_NAMES = (
    "llm_provider",
    "openai_model_classification",
    "openai_model_extraction",
    "openai_model_model_generation",
    "openai_max_retries",
    "openai_timeout",
    "openai_max_output_tokens",
    "ai_extraction_mode",
    "ai_chunk_max_layout_chars",
    "ai_chunk_overlap_rows",
    "ai_chunk_max_retries",
    "ai_chunk_concurrency",
    "profile_discovery_engine_enabled",
    "profile_discovery_shadow_mode",
    "profile_discovery_max_ai_chunks",
    "profile_discovery_max_prompt_tokens",
    "profile_discovery_invoice_concurrency",
    "profile_discovery_max_refinements",
    "profile_discovery_historical_id_coverage",
    "worker_concurrency",
    "confidence_threshold",
    "reconciliation_variance_threshold",
    "model_validation_required_successes",
    "model_validation_max_failures",
    "watcher_poll_interval",
    "watcher_stable_seconds",
    "log_level",
    "debug",
)

_cache: dict[str, Any] | None = None
_cache_loaded_at = 0.0


def env_runtime_settings() -> dict[str, Any]:
    """Return mutable runtime defaults from process env-backed settings."""
    return {name: getattr(settings, name) for name in MUTABLE_SETTING_NAMES if hasattr(settings, name)}


def get_runtime_settings(*, force_refresh: bool = False) -> dict[str, Any]:
    """Read persisted runtime settings with env fallback.

    Workers call this frequently. A tiny TTL keeps cross-process changes visible
    without adding a DB query to every property access inside one pipeline step.
    """
    global _cache, _cache_loaded_at
    now = time.monotonic()
    if not force_refresh and _cache is not None and now - _cache_loaded_at < CACHE_TTL_SECONDS:
        return dict(_cache)

    merged = env_runtime_settings()
    try:
        from stencil.db import registry
        from stencil.db.session import SessionLocal

        db = SessionLocal()
        try:
            row = registry.get_runtime_setting(db, RUNTIME_SETTINGS_KEY)
        finally:
            db.close()
        if row is not None and isinstance(row.value, dict):
            merged.update({key: value for key, value in row.value.items() if key in MUTABLE_SETTING_NAMES})
    except Exception as exc:  # pragma: no cover - DB unavailable fallback
        logger.warning("runtime_settings.load_failed", error=str(exc))

    _cache = merged
    _cache_loaded_at = now
    return dict(merged)


def save_runtime_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """Persist mutable settings and mirror them into the current process."""
    invalid = sorted(set(updates) - set(MUTABLE_SETTING_NAMES))
    if invalid:
        raise ValueError(f"Unsupported runtime setting(s): {', '.join(invalid)}")

    current = get_runtime_settings(force_refresh=True)
    current.update({key: value for key, value in updates.items() if value is not None})

    from stencil.db import registry
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        registry.upsert_runtime_setting(db, key=RUNTIME_SETTINGS_KEY, value=current)
    finally:
        db.close()

    apply_runtime_settings(current)
    global _cache, _cache_loaded_at
    _cache = dict(current)
    _cache_loaded_at = time.monotonic()
    return dict(current)


def apply_runtime_settings(values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply persisted values to the local settings object and return them."""
    values = values or get_runtime_settings()
    for key, value in values.items():
        if key in MUTABLE_SETTING_NAMES and hasattr(settings, key):
            setattr(settings, key, value)
    return dict(values)


def runtime_value(name: str):
    return get_runtime_settings().get(name, getattr(settings, name))


def openai_model_classification() -> str:
    return str(runtime_value("openai_model_classification"))


def openai_model_extraction() -> str:
    return str(runtime_value("openai_model_extraction"))


def openai_model_model_generation() -> str:
    return str(runtime_value("openai_model_model_generation"))


def openai_max_retries() -> int:
    return int(runtime_value("openai_max_retries"))


def openai_timeout() -> int:
    return int(runtime_value("openai_timeout"))


def openai_max_output_tokens() -> int:
    return int(runtime_value("openai_max_output_tokens"))


def ai_extraction_mode() -> str:
    return str(runtime_value("ai_extraction_mode"))


def worker_concurrency() -> int:
    return int(runtime_value("worker_concurrency"))
