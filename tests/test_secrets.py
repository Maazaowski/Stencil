"""Encrypted provider-key storage — offline unit tests (no DB, no real API)."""

import pytest
from cryptography.fernet import Fernet

import stencil.db.registry as registry
import stencil.db.session as session
import stencil.secrets as secrets
from stencil.config import settings


class _FakeDB:
    def close(self) -> None:  # SessionLocal() context
        pass


def _patch_store(monkeypatch) -> dict:
    """Replace the DB credential CRUD with an in-memory dict."""
    store: dict[str, str] = {}
    monkeypatch.setattr(session, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(
        registry, "upsert_provider_credential",
        lambda db, *, provider, key_ciphertext: store.__setitem__(provider, key_ciphertext),
    )
    monkeypatch.setattr(
        registry, "get_provider_credential",
        lambda db, provider: type("R", (), {"key_ciphertext": store[provider]})() if provider in store else None,
    )
    monkeypatch.setattr(
        registry, "delete_provider_credential",
        lambda db, provider: store.pop(provider, None) is not None,
    )
    return store


def test_round_trip_encrypts_at_rest(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", Fernet.generate_key().decode())
    store = _patch_store(monkeypatch)

    assert secrets.secret_storage_enabled() is True
    secrets.set_provider_key("anthropic", "sk-ant-secret")
    assert secrets.get_provider_key("anthropic") == "sk-ant-secret"
    # ciphertext at rest must not equal the plaintext key
    assert store["anthropic"] != "sk-ant-secret"
    assert secrets.provider_key_status()["anthropic"] is True

    assert secrets.clear_provider_key("anthropic") is True
    assert "anthropic" not in store


def test_env_fallback_when_no_stored_key(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "")  # in-app storage disabled
    monkeypatch.setattr(settings, "openai_api_key", "sk-openai-env")
    assert secrets.secret_storage_enabled() is False
    assert secrets.get_provider_key("openai") == "sk-openai-env"


def test_write_rejected_without_secret_key(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "")
    with pytest.raises(secrets.SecretsDisabledError):
        secrets.set_provider_key("openai", "sk-test")


def test_unknown_provider_rejected(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", Fernet.generate_key().decode())
    with pytest.raises(ValueError):
        secrets.get_provider_key("gemini")
