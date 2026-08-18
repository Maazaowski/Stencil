"""Dev-only AI-call capture: traced_chat_completion dumps prompts when debug is on."""

from types import SimpleNamespace

import pytest

from stencil import ai_debug


def _response(content='{"ok": 1}'):
    return SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=content))],
    )


class _FakeClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._response


@pytest.fixture()
def debug_on(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_debug.settings, "work_dir", tmp_path)
    real = ai_debug.runtime_settings.runtime_value
    monkeypatch.setattr(
        ai_debug.runtime_settings, "runtime_value",
        lambda name: True if name == "debug" else real(name),
    )
    return tmp_path / "ai_debug"


def _call(client, **extra):
    return ai_debug.traced_chat_completion(
        client,
        call_type="extraction",
        context="Acme Corp",
        model="gpt-5.5",
        messages=[
            {"role": "system", "content": "SYSTEM PROMPT TEXT"},
            {"role": "user", "content": "USER PROMPT BODY"},
        ],
        response_format={"type": "json_schema", "json_schema": {"name": "x"}},
        max_completion_tokens=4096,
        **extra,
    )


def test_dumps_full_prompt_and_metrics_when_debug_on(debug_on):
    client = _FakeClient(_response())
    out = _call(client)
    assert out is client._response  # transparent passthrough

    files = list(debug_on.glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "SYSTEM PROMPT TEXT" in text
    assert "USER PROMPT BODY" in text
    assert "extraction" in text and "gpt-5.5" in text
    assert '"tokens_input": 100' in text and '"tokens_output": 20' in text
    assert '"finish_reason": "stop"' in text


def test_no_dump_when_debug_off(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_debug.settings, "work_dir", tmp_path)
    monkeypatch.setattr(ai_debug.runtime_settings, "runtime_value", lambda name: False)
    _call(_FakeClient(_response()))
    assert not (tmp_path / "ai_debug").exists()


def test_image_parts_are_redacted(debug_on):
    client = _FakeClient(_response())
    ai_debug.traced_chat_completion(
        client, call_type="classification", context="x.pdf", model="gpt-5.5",
        messages=[
            {"role": "system", "content": "S"},
            {"role": "user", "content": [
                {"type": "text", "text": "look at this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 4000}},
            ]},
        ],
        response_format={"type": "json_schema", "json_schema": {"name": "c"}},
        max_completion_tokens=2048,
    )
    text = next(debug_on.glob("*.md")).read_text(encoding="utf-8")
    assert "look at this" in text
    assert "[image omitted" in text
    assert "AAAA" not in text  # base64 never written


def test_retention_prunes_to_cap(debug_on, monkeypatch):
    monkeypatch.setattr(ai_debug.settings, "ai_debug_max_files", 2)
    for _ in range(5):
        _call(_FakeClient(_response()))
    assert len(list(debug_on.glob("*.md"))) == 2


def test_error_call_is_dumped_and_reraised(debug_on):
    client = _FakeClient(exc=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        _call(client)
    files = list(debug_on.glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "boom" in text and '"status": "error"' in text


def test_endpoint_forbidden_when_debug_off(monkeypatch):
    """Prompts must never be exposed in production — the API gates on debug."""
    from fastapi.testclient import TestClient

    from stencil.api import ai_debug as api_ai_debug
    from stencil.main import app

    monkeypatch.setattr(
        api_ai_debug.runtime_settings, "runtime_value",
        lambda name: False if name == "debug" else None,
    )
    resp = TestClient(app).get("/api/v1/debug/ai-calls")
    assert resp.status_code == 403
