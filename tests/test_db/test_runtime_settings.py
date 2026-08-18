from stencil.config import settings
from stencil.runtime_settings import (
    apply_runtime_settings,
    get_runtime_settings,
    openai_model_extraction,
    save_runtime_settings,
    worker_concurrency,
)


def _clear_runtime_cache(monkeypatch):
    import stencil.runtime_settings as runtime_settings

    monkeypatch.setattr(runtime_settings, "_cache", None)
    monkeypatch.setattr(runtime_settings, "_cache_loaded_at", 0.0)


def test_runtime_settings_persist_across_settings_object_changes(isolated_db, monkeypatch):
    _clear_runtime_cache(monkeypatch)
    monkeypatch.setattr(settings, "openai_model_extraction", "env-model")

    saved = save_runtime_settings({"openai_model_extraction": "gpt-4o"})
    monkeypatch.setattr(settings, "openai_model_extraction", "different-env-model")
    _clear_runtime_cache(monkeypatch)

    loaded = get_runtime_settings(force_refresh=True)

    assert saved["openai_model_extraction"] == "gpt-4o"
    assert loaded["openai_model_extraction"] == "gpt-4o"
    assert openai_model_extraction() == "gpt-4o"


def test_apply_runtime_settings_updates_local_process_settings(isolated_db, monkeypatch):
    _clear_runtime_cache(monkeypatch)
    save_runtime_settings({
        "openai_model_classification": "gpt-4o-mini",
        "openai_model_extraction": "gpt-4o",
        "openai_model_model_generation": "gpt-4.1",
    })

    applied = apply_runtime_settings()

    assert applied["openai_model_extraction"] == "gpt-4o"
    assert settings.openai_model_classification == "gpt-4o-mini"
    assert settings.openai_model_extraction == "gpt-4o"
    assert settings.openai_model_model_generation == "gpt-4.1"


def test_worker_concurrency_is_mutable_runtime_setting(isolated_db, monkeypatch):
    _clear_runtime_cache(monkeypatch)
    monkeypatch.setattr(settings, "worker_concurrency", 2)

    save_runtime_settings({"worker_concurrency": 5})

    assert worker_concurrency() == 5
    assert settings.worker_concurrency == 5

