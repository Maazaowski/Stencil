"""Encrypted storage + resolution of LLM provider API keys.

Keys entered in the app are encrypted at rest with Fernet (``ST_SECRET_KEY``) and
stored in the ``provider_credentials`` table. The plaintext key is never persisted
in clear, never returned by the API, and is decrypted only in-process to build an
LLM client. When ``ST_SECRET_KEY`` is unset, in-app storage is disabled and a
provider's key falls back to its ``ST_*_API_KEY`` env var (read path still works).
"""

from __future__ import annotations

import structlog
from cryptography.fernet import Fernet, InvalidToken

from stencil.config import settings

logger = structlog.get_logger()

# provider -> the config attribute holding its env-var fallback key.
_ENV_KEY_ATTR = {"openai": "openai_api_key", "anthropic": "anthropic_api_key"}


class SecretsDisabledError(RuntimeError):
    """Raised when an in-app key write is attempted without ST_SECRET_KEY set."""


def secret_storage_enabled() -> bool:
    """True when a valid ST_SECRET_KEY is configured (in-app key storage allowed)."""
    if not settings.secret_key:
        return False
    try:
        _fernet()
        return True
    except SecretsDisabledError:
        return False


def _fernet() -> Fernet:
    key = settings.secret_key
    if not key:
        raise SecretsDisabledError(
            "ST_SECRET_KEY is not set; in-app API key storage is disabled. Set "
            "ST_SECRET_KEY (a Fernet key) to store keys, or use the ST_*_API_KEY env vars."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:  # malformed key
        raise SecretsDisabledError(f"ST_SECRET_KEY is not a valid Fernet key: {exc}") from exc


def _validate_provider(provider: str) -> str:
    if provider not in _ENV_KEY_ATTR:
        raise ValueError(f"Unknown provider: {provider!r}")
    return provider


def set_provider_key(provider: str, api_key: str) -> None:
    """Encrypt and store a provider's API key. Requires ST_SECRET_KEY."""
    _validate_provider(provider)
    if not api_key or not api_key.strip():
        raise ValueError("API key must not be empty")
    ciphertext = _fernet().encrypt(api_key.strip().encode()).decode()

    from stencil.db import registry
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        registry.upsert_provider_credential(db, provider=provider, key_ciphertext=ciphertext)
    finally:
        db.close()
    logger.info("secrets.key_set", provider=provider)


def clear_provider_key(provider: str) -> bool:
    """Remove a provider's stored key (env fallback still applies afterward)."""
    _validate_provider(provider)
    from stencil.db import registry
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        removed = registry.delete_provider_credential(db, provider)
    finally:
        db.close()
    if removed:
        logger.info("secrets.key_cleared", provider=provider)
    return removed


def _stored_key(provider: str) -> str | None:
    """Decrypt the stored key for ``provider``, or None if absent/unreadable."""
    if not settings.secret_key:
        return None
    from stencil.db import registry
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        row = registry.get_provider_credential(db, provider)
    except Exception as exc:  # pragma: no cover - DB unavailable fallback
        logger.warning("secrets.load_failed", provider=provider, error=str(exc))
        return None
    finally:
        db.close()
    if row is None:
        return None
    try:
        return _fernet().decrypt(row.key_ciphertext.encode()).decode()
    except (InvalidToken, SecretsDisabledError) as exc:
        logger.warning("secrets.decrypt_failed", provider=provider, error=str(exc))
        return None


def get_provider_key(provider: str) -> str:
    """Resolve a provider's API key: in-app encrypted key first, then env fallback."""
    _validate_provider(provider)
    stored = _stored_key(provider)
    if stored:
        return stored
    return str(getattr(settings, _ENV_KEY_ATTR[provider], "") or "")


def provider_key_status() -> dict[str, bool]:
    """Whether each provider has a usable key (in-app or env)."""
    return {provider: bool(get_provider_key(provider)) for provider in _ENV_KEY_ATTR}
