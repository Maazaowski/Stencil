"""Settings API persistence tests."""

from fastapi.testclient import TestClient

from stencil.config import settings
from stencil.main import app


def _clear_runtime_cache(monkeypatch):
    import stencil.runtime_settings as runtime_settings

    monkeypatch.setattr(runtime_settings, "_cache", None)
    monkeypatch.setattr(runtime_settings, "_cache_loaded_at", 0.0)


def test_model_settings_persist_and_round_trip(isolated_db, monkeypatch):
    _clear_runtime_cache(monkeypatch)
    monkeypatch.setattr(settings, "openai_model_classification", "env-classification")
    monkeypatch.setattr(settings, "openai_model_extraction", "env-extraction")
    monkeypatch.setattr(settings, "openai_model_model_generation", "env-generation")
    client = TestClient(app)

    response = client.put(
        "/api/v1/settings",
        json={
            "openai_model_classification": "gpt-4o-mini",
            "openai_model_extraction": "gpt-4o",
            "openai_model_model_generation": "gpt-4.1",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["openai_model_classification"] == "gpt-4o-mini"
    assert body["openai_model_extraction"] == "gpt-4o"
    assert body["openai_model_model_generation"] == "gpt-4.1"
    assert "gpt-4o" in body["openai_model_options"]
    assert settings.openai_model_extraction == "gpt-4o"

    monkeypatch.setattr(settings, "openai_model_extraction", "changed-env-after-save")
    _clear_runtime_cache(monkeypatch)

    loaded = client.get("/api/v1/settings")

    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["openai_model_extraction"] == "gpt-4o"


def test_worker_concurrency_persists_and_round_trips(isolated_db, monkeypatch):
    _clear_runtime_cache(monkeypatch)
    monkeypatch.setattr(settings, "worker_concurrency", 2)
    client = TestClient(app)

    response = client.put("/api/v1/settings", json={"worker_concurrency": 4})

    assert response.status_code == 200, response.text
    assert response.json()["worker_concurrency"] == 4
    assert settings.worker_concurrency == 4

    loaded = client.get("/api/v1/settings")

    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["worker_concurrency"] == 4


def test_worker_concurrency_save_resizes_running_worker(isolated_db, monkeypatch):
    import stencil.api.settings as api_settings

    _clear_runtime_cache(monkeypatch)
    monkeypatch.setattr(settings, "worker_concurrency", 2)
    calls = []

    monkeypatch.setattr(
        api_settings,
        "_resize_running_workers",
        lambda old, new: calls.append((old, new)),
    )
    client = TestClient(app)

    response = client.put("/api/v1/settings", json={"worker_concurrency": 5})

    assert response.status_code == 200, response.text
    assert calls == [(2, 5)]


def test_llm_provider_and_key_status_round_trip(isolated_db, monkeypatch):
    import stencil.api.settings as api_settings

    _clear_runtime_cache(monkeypatch)
    monkeypatch.setattr(api_settings.secrets, "provider_key_status", lambda: {"openai": True, "anthropic": True})
    monkeypatch.setattr(api_settings.secrets, "secret_storage_enabled", lambda: False)
    client = TestClient(app)

    response = client.put(
        "/api/v1/settings",
        json={
            "llm_provider": "anthropic",
            "openai_model_classification": "claude-opus-4-8",
            "openai_model_extraction": "claude-sonnet-4-6",
            "openai_model_model_generation": "claude-haiku-4-5",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["llm_provider"] == "anthropic"
    assert body["openai_model_options"][0] == "claude-opus-4-8"
    assert body["openai_api_key_set"] is True
    assert body["anthropic_api_key_set"] is True
    assert body["secret_storage_enabled"] is False
    assert "openai_api_key" not in body
    assert "anthropic_api_key" not in body


def test_provider_key_endpoint_rejects_without_secret_key(isolated_db, monkeypatch):
    import stencil.api.settings as api_settings

    _clear_runtime_cache(monkeypatch)
    monkeypatch.setattr(api_settings.secrets, "secret_storage_enabled", lambda: False)
    monkeypatch.setattr(settings, "secret_key", "")
    client = TestClient(app)

    response = client.put("/api/v1/settings/keys", json={"provider": "anthropic", "api_key": "sk-ant"})

    assert response.status_code == 400
    assert "ST_SECRET_KEY" in response.json()["detail"]


def test_settings_rejects_unknown_llm_provider(isolated_db, monkeypatch):
    _clear_runtime_cache(monkeypatch)
    client = TestClient(app)

    response = client.put("/api/v1/settings", json={"llm_provider": "gemini"})

    assert response.status_code == 400
    assert "Unknown provider" in response.json()["detail"]
