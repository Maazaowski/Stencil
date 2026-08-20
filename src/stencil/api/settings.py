"""Settings API - read and update runtime configuration."""

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from stencil import secrets
from stencil.api.deps import AdminUser, DbSession
from stencil.audit import append_event
from stencil.config import settings
from stencil.llm import PROVIDERS, models_for, normalize_provider
from stencil.runtime_settings import (
    apply_runtime_settings,
    get_runtime_settings,
    save_runtime_settings,
)

router = APIRouter(prefix="/settings", tags=["settings"])
logger = structlog.get_logger()


class SettingsResponse(BaseModel):
    """Read-only view of current application settings."""

    # Folder paths
    data_dir: str
    inbound_dir: str
    processing_dir: str
    completed_dir: str
    exceptions_dir: str
    archive_dir: str
    supplier_profiles_dir: str
    extraction_models_dir: str

    # LLM provider + keys
    llm_provider: str
    providers: list[str]
    provider_model_options: dict[str, list[str]]
    openai_api_key_set: bool  # never expose the actual key
    anthropic_api_key_set: bool
    secret_storage_enabled: bool  # ST_SECRET_KEY present -> in-app key storage allowed
    openai_model_classification: str
    openai_model_extraction: str
    openai_model_model_generation: str
    openai_model_options: list[str]  # active provider's models (legacy field)
    openai_max_retries: int
    openai_timeout: int
    openai_max_output_tokens: int
    ai_extraction_mode: str
    ai_chunk_max_layout_chars: int
    ai_chunk_overlap_rows: int
    ai_chunk_max_retries: int
    ai_chunk_concurrency: int
    worker_concurrency: int

    # Thresholds
    confidence_threshold: float
    reconciliation_variance_threshold: float
    model_validation_required_successes: int
    model_validation_max_failures: int

    # File watcher
    watcher_poll_interval: float
    watcher_stable_seconds: float

    # Misc
    log_level: str
    debug: bool


class SettingsUpdate(BaseModel):
    """Mutable settings that can be changed at runtime."""

    # Provider + models
    llm_provider: str | None = None
    openai_model_classification: str | None = None
    openai_model_extraction: str | None = None
    openai_model_model_generation: str | None = None
    openai_max_retries: int | None = Field(default=None, ge=1, le=10)
    openai_timeout: int | None = Field(default=None, ge=10, le=600)
    openai_max_output_tokens: int | None = Field(default=None, ge=1024, le=200000)
    ai_extraction_mode: str | None = None
    ai_chunk_max_layout_chars: int | None = Field(default=None, ge=1000, le=250000)
    ai_chunk_overlap_rows: int | None = Field(default=None, ge=0, le=50)
    ai_chunk_max_retries: int | None = Field(default=None, ge=0, le=10)
    ai_chunk_concurrency: int | None = Field(default=None, ge=1, le=16)
    worker_concurrency: int | None = Field(default=None, ge=1, le=16)

    # Thresholds
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    reconciliation_variance_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    model_validation_required_successes: int | None = Field(default=None, ge=1, le=20)
    model_validation_max_failures: int | None = Field(default=None, ge=1, le=20)

    # File watcher
    watcher_poll_interval: float | None = Field(default=None, ge=0.5, le=30.0)
    watcher_stable_seconds: float | None = Field(default=None, ge=1.0, le=60.0)

    # Misc
    log_level: str | None = None
    debug: bool | None = None


def _provider_model_options(runtime: dict[str, Any]) -> dict[str, list[str]]:
    """Per-provider model lists. The active provider's list also includes any
    currently-selected models that aren't in the catalog, so they stay selectable."""
    options = {provider: models_for(provider) for provider in PROVIDERS}
    active = normalize_provider(runtime.get("llm_provider"))
    current = [
        runtime["openai_model_classification"],
        runtime["openai_model_extraction"],
        runtime["openai_model_model_generation"],
    ]
    options[active] = list(dict.fromkeys([*options.get(active, []), *[m for m in current if m]]))
    return options


def _settings_to_response() -> SettingsResponse:
    runtime = apply_runtime_settings(get_runtime_settings())
    provider = normalize_provider(runtime.get("llm_provider"))
    provider_options = _provider_model_options(runtime)
    key_status = secrets.provider_key_status()
    return SettingsResponse(
        llm_provider=provider,
        providers=list(PROVIDERS),
        provider_model_options=provider_options,
        anthropic_api_key_set=bool(key_status.get("anthropic")),
        secret_storage_enabled=secrets.secret_storage_enabled(),
        data_dir=str(settings.data_dir),
        inbound_dir=str(settings.inbound_dir),
        processing_dir=str(settings.processing_dir),
        completed_dir=str(settings.completed_dir),
        exceptions_dir=str(settings.exceptions_dir),
        archive_dir=str(settings.archive_dir),
        supplier_profiles_dir=str(settings.supplier_profiles_dir),
        extraction_models_dir=str(settings.extraction_models_dir),
        openai_api_key_set=bool(key_status.get("openai")),
        openai_model_classification=runtime["openai_model_classification"],
        openai_model_extraction=runtime["openai_model_extraction"],
        openai_model_model_generation=runtime["openai_model_model_generation"],
        openai_model_options=provider_options[provider],
        openai_max_retries=runtime["openai_max_retries"],
        openai_timeout=runtime["openai_timeout"],
        openai_max_output_tokens=runtime["openai_max_output_tokens"],
        ai_extraction_mode=runtime["ai_extraction_mode"],
        ai_chunk_max_layout_chars=runtime["ai_chunk_max_layout_chars"],
        ai_chunk_overlap_rows=runtime["ai_chunk_overlap_rows"],
        ai_chunk_max_retries=runtime["ai_chunk_max_retries"],
        ai_chunk_concurrency=runtime["ai_chunk_concurrency"],
        worker_concurrency=runtime["worker_concurrency"],
        confidence_threshold=runtime["confidence_threshold"],
        reconciliation_variance_threshold=runtime["reconciliation_variance_threshold"],
        model_validation_required_successes=runtime["model_validation_required_successes"],
        model_validation_max_failures=runtime["model_validation_max_failures"],
        watcher_poll_interval=runtime["watcher_poll_interval"],
        watcher_stable_seconds=runtime["watcher_stable_seconds"],
        log_level=runtime["log_level"],
        debug=runtime["debug"],
    )


def _resize_running_workers(old_concurrency: int | None, new_concurrency: int | None) -> None:
    """Best-effort live Celery pool resize for the current worker process."""
    if old_concurrency is None or new_concurrency is None:
        return
    delta = int(new_concurrency) - int(old_concurrency)
    if delta == 0:
        return

    try:
        from stencil.tasks.worker import app as worker_app

        if delta > 0:
            worker_app.control.pool_grow(delta, reply=False)
        else:
            worker_app.control.pool_shrink(abs(delta), reply=False)
        logger.info(
            "settings.worker_concurrency_resized",
            old_concurrency=old_concurrency,
            new_concurrency=new_concurrency,
            delta=delta,
        )
    except Exception as exc:  # pragma: no cover - broker/worker may be unavailable
        logger.warning(
            "settings.worker_concurrency_resize_failed",
            old_concurrency=old_concurrency,
            new_concurrency=new_concurrency,
            error=str(exc),
        )


@router.get("", response_model=SettingsResponse)
def get_settings() -> SettingsResponse:
    """Return current application settings (sensitive values masked)."""
    return _settings_to_response()


@router.put("", response_model=SettingsResponse)
def update_settings(payload: SettingsUpdate, db: DbSession, admin: AdminUser) -> SettingsResponse:
    """Update mutable runtime settings.

    Changes are applied in-memory for the current process.
    Folder paths and database URLs cannot be changed at runtime.
    """
    updates = payload.model_dump(exclude_none=True)
    if "llm_provider" in updates and updates["llm_provider"] not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {updates['llm_provider']}")
    current = get_runtime_settings(force_refresh=True)
    old_worker_concurrency = current.get("worker_concurrency")
    saved = save_runtime_settings(updates)
    if "worker_concurrency" in updates:
        _resize_running_workers(old_worker_concurrency, saved.get("worker_concurrency"))
    append_event(
        db,
        entity_type="runtime_settings",
        entity_id="global",
        action="updated",
        actor=admin,
        metadata={"changed": sorted(updates)},
    )
    return _settings_to_response()


class ProviderKeyUpdate(BaseModel):
    """Write-only API key for a provider. The key is never returned in any response."""

    provider: str
    api_key: str


@router.put("/keys", response_model=SettingsResponse)
def set_provider_key(payload: ProviderKeyUpdate, db: DbSession, admin: AdminUser) -> SettingsResponse:
    """Store a provider's API key encrypted at rest. Requires ST_SECRET_KEY."""
    if payload.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {payload.provider}")
    try:
        secrets.set_provider_key(payload.provider, payload.api_key)
    except secrets.SecretsDisabledError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("settings.provider_key_set", provider=payload.provider)
    append_event(
        db,
        entity_type="provider_credential",
        entity_id=payload.provider,
        action="key_set",
        actor=admin,
    )
    return _settings_to_response()


@router.delete("/keys/{provider}", response_model=SettingsResponse)
def clear_provider_key(provider: str, db: DbSession, admin: AdminUser) -> SettingsResponse:
    """Remove a provider's stored key (env-var fallback still applies)."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    secrets.clear_provider_key(provider)
    logger.info("settings.provider_key_cleared", provider=provider)
    append_event(
        db,
        entity_type="provider_credential",
        entity_id=provider,
        action="key_cleared",
        actor=admin,
    )
    return _settings_to_response()
