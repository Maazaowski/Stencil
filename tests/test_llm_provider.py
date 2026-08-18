"""Multi-provider LLM layer — offline unit tests (stubbed Anthropic, no network)."""

from decimal import Decimal

from stencil.llm import client as llm_client
from stencil.llm import default_model, models_for, normalize_provider
from stencil.pricing import estimate_cost_usd

# --- provider catalog ----------------------------------------------------------


def test_provider_catalog():
    assert normalize_provider("anthropic") == "anthropic"
    assert normalize_provider("bogus") == "openai"  # fallback
    assert models_for("anthropic")[0] == "claude-opus-4-8"
    assert default_model("anthropic") == "claude-opus-4-8"


def test_claude_pricing_resolves():
    # opus 4.8: $5 input / $25 output per 1M -> 30.0 for 1M each.
    assert estimate_cost_usd("claude-opus-4-8", 1_000_000, 1_000_000) == Decimal("30.000000")


# --- Anthropic adapter translation (fake client, no network) -------------------


def _adapter_with_fake(message):
    """Build an AnthropicChatAdapter whose underlying client returns ``message``."""
    captured: dict = {}

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def get_final_message(self):
            return message

    class _Messages:
        def stream(self, **kwargs):
            captured.update(kwargs)
            return _Stream()

    class _Fake:
        def with_options(self, **_):
            return self

        messages = _Messages()

    adapter = llm_client.AnthropicChatAdapter.__new__(llm_client.AnthropicChatAdapter)
    adapter._client = _Fake()
    adapter.chat = llm_client._Chat(adapter)
    return adapter, captured


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Usage:
    input_tokens = 13
    output_tokens = 8


class _Msg:
    def __init__(self, content, stop_reason="end_turn", stop_details=None):
        self.content = content
        self.usage = _Usage()
        self.stop_reason = stop_reason
        self.stop_details = stop_details
        self.model = "claude-opus-4-8"


def test_request_and_response_translation():
    adapter, captured = _adapter_with_fake(_Msg([_Block('{"ok": true}')]))
    resp = adapter.chat.completions.create(
        model="claude-opus-4-8",
        messages=[
            {"role": "system", "content": "Be precise."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Extract"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
                ],
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "n", "schema": {"type": "object"}, "strict": True},
        },
        max_completion_tokens=2048,
        timeout=99,
    )
    # request: system pulled out, schema -> output_config, max_completion_tokens -> max_tokens
    assert captured["system"] == "Be precise."
    assert captured["max_tokens"] == 2048
    assert captured["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}}}
    image = captured["messages"][0]["content"][1]
    assert image == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}
    # response: OpenAI-shaped shim
    assert resp.choices[0].message.content == '{"ok": true}'
    assert resp.choices[0].finish_reason == "stop"
    assert resp.usage.prompt_tokens == 13 and resp.usage.completion_tokens == 8


def test_anthropic_schema_converts_nullable_enum():
    adapter, captured = _adapter_with_fake(_Msg([_Block('{"rows": []}')]))
    adapter.chat.completions.create(
        model="claude-opus-4-8",
        messages=[{"role": "user", "content": "Extract"}],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "compact_line_items",
                "schema": {
                    "type": "object",
                    "properties": {
                        "rows": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "c": {
                                        "type": ["string", "null"],
                                        "enum": ["recurring", "tax", "unknown", None],
                                    }
                                },
                                "required": ["c"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["rows"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    )

    row_props = captured["output_config"]["format"]["schema"]["properties"]["rows"]["items"]["properties"]
    assert row_props["c"] == {
        "anyOf": [
            {"type": "string", "enum": ["recurring", "tax", "unknown"]},
            {"type": "null"},
        ]
    }


def test_max_tokens_maps_to_length():
    adapter, _ = _adapter_with_fake(_Msg([_Block("partial")], stop_reason="max_tokens"))
    resp = adapter.chat.completions.create(model="claude-opus-4-8", messages=[{"role": "user", "content": "x"}])
    assert resp.choices[0].finish_reason == "length"  # completion_to_json treats this as truncation


def test_refusal_yields_empty_content():
    details = type("D", (), {"explanation": "declined"})()
    adapter, _ = _adapter_with_fake(_Msg([], stop_reason="refusal", stop_details=details))
    resp = adapter.chat.completions.create(model="claude-opus-4-8", messages=[{"role": "user", "content": "x"}])
    assert resp.choices[0].message.content is None
    assert resp.choices[0].finish_reason == "refusal"
    assert resp.choices[0].message.refusal == "declined"


# --- settings API: key masking + provider switch -------------------------------


def test_settings_response_masks_keys_and_switches_provider(monkeypatch):
    import stencil.api.settings as api_settings
    from stencil import runtime_settings as rs
    from stencil.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-env")
    monkeypatch.setattr(settings, "secret_key", "")  # in-app storage off
    base = rs.env_runtime_settings()
    base["llm_provider"] = "anthropic"
    monkeypatch.setattr(api_settings, "get_runtime_settings", lambda *a, **k: dict(base))

    response = api_settings._settings_to_response()
    assert response.llm_provider == "anthropic"
    assert response.provider_model_options["anthropic"][0] == "claude-opus-4-8"
    assert response.openai_model_options == response.provider_model_options["anthropic"]
    assert response.anthropic_api_key_set is True
    assert response.secret_storage_enabled is False
    # the raw key is never part of the response
    assert not hasattr(response, "openai_api_key")
    assert not hasattr(response, "anthropic_api_key")
