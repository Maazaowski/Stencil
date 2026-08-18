"""Provider-agnostic AI client factory.

``get_ai_client()`` returns a client whose ``.chat.completions.create(**kwargs)``
behaves the same regardless of provider, so the existing call sites and ai_debug
need no changes. For OpenAI it is the real SDK client; for Anthropic it is a thin
adapter that translates the OpenAI Chat Completions request/response to and from
the Anthropic Messages API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import structlog

from stencil import runtime_settings, secrets
from stencil.llm.config import normalize_provider

logger = structlog.get_logger()


def active_provider() -> str:
    return normalize_provider(str(runtime_settings.runtime_value("llm_provider")))


def get_ai_client(purpose: str | None = None) -> Any:
    """Build an LLM client for the active provider, keyed to its resolved API key."""
    provider = active_provider()
    api_key = secrets.get_provider_key(provider)
    logger.debug("llm.client_create", provider=provider, purpose=purpose)
    if provider == "anthropic":
        return AnthropicChatAdapter(api_key=api_key)
    from openai import OpenAI

    return OpenAI(
        api_key=api_key,
        max_retries=runtime_settings.openai_max_retries(),
        timeout=runtime_settings.openai_timeout(),
    )


# --- OpenAI-shaped response shim -------------------------------------------------


@dataclass
class _Usage:
    prompt_tokens: int
    completion_tokens: int


@dataclass
class _Message:
    content: str | None
    role: str = "assistant"
    refusal: str | None = None


@dataclass
class _Choice:
    message: _Message
    finish_reason: str | None
    index: int = 0


@dataclass
class _ChatCompletion:
    choices: list[_Choice]
    usage: _Usage
    model: str | None = None


# --- OpenAI -> Anthropic translation --------------------------------------------

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$", re.DOTALL)

# Anthropic stop_reason -> OpenAI finish_reason. "max_tokens" -> "length" is the
# important one: completion_to_json branches on it to report truncation.
_FINISH_REASON = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "refusal": "refusal",
    "pause_turn": "stop",
}


def _split_system(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Pull OpenAI system messages into Anthropic's top-level ``system`` string."""
    system_parts: list[str] = []
    conversation: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "system":
            system_parts.append(_content_to_text(message.get("content")))
        else:
            conversation.append(message)
    system = "\n\n".join(p for p in system_parts if p) or None
    return system, conversation


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content or "")


def _translate_message(message: dict[str, Any]) -> dict[str, Any]:
    role = message.get("role", "user")
    content = message.get("content")
    if isinstance(content, str):
        return {"role": role, "content": content}
    if isinstance(content, list):
        return {"role": role, "content": [_translate_part(part) for part in content]}
    return {"role": role, "content": str(content or "")}


def _translate_part(part: Any) -> dict[str, Any]:
    if not isinstance(part, dict):
        return {"type": "text", "text": str(part)}
    if part.get("type") == "text":
        return {"type": "text", "text": str(part.get("text", ""))}
    if part.get("type") == "image_url":
        url = ((part.get("image_url") or {}).get("url")) or ""
        match = _DATA_URL_RE.match(url)
        if match:
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": match.group("mime"), "data": match.group("data")},
            }
        return {"type": "image", "source": {"type": "url", "url": url}}
    # Unknown part type — pass its text if any, else drop to a stub.
    return {"type": "text", "text": str(part.get("text", ""))}


def _extract_schema(response_format: dict[str, Any] | None) -> dict[str, Any] | None:
    if not response_format:
        return None
    inner = response_format.get("json_schema") or {}
    schema = inner.get("schema")
    return schema if isinstance(schema, dict) else (inner if inner else None)


def _anthropic_schema(schema: Any) -> Any:
    """Convert OpenAI-compatible JSON Schema quirks to Anthropic's stricter form."""
    if isinstance(schema, list):
        return [_anthropic_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    converted = {key: _anthropic_schema(value) for key, value in schema.items()}
    schema_type = converted.get("type")
    if not isinstance(schema_type, list) or "null" not in schema_type:
        return converted

    non_null_types = [item for item in schema_type if item != "null"]
    non_null_branch = {key: value for key, value in converted.items() if key not in {"type", "enum"}}
    if len(non_null_types) == 1:
        non_null_branch["type"] = non_null_types[0]
    elif non_null_types:
        non_null_branch["type"] = non_null_types

    if "enum" in converted:
        enum_values = converted.get("enum") or []
        non_null_enum = [item for item in enum_values if item is not None]
        if non_null_enum:
            non_null_branch["enum"] = non_null_enum

    branches: list[dict[str, Any]] = []
    if non_null_types:
        branches.append(non_null_branch)
    branches.append({"type": "null"})
    return {"anyOf": branches}


def _text_from(message: Any) -> str | None:
    for block in getattr(message, "content", None) or []:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", None)
    return None


class AnthropicChatAdapter:
    """Exposes ``.chat.completions.create(**openai_kwargs)`` over the Anthropic SDK."""

    def __init__(self, *, api_key: str):
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key)
        self.chat = _Chat(self)

    def _create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
        max_completion_tokens: int | None = None,
        timeout: float | int | None = None,
        **_ignored: Any,
    ) -> _ChatCompletion:
        system, conversation = _split_system(messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": int(max_completion_tokens or 4096),
            "messages": [_translate_message(m) for m in conversation],
        }
        if system:
            kwargs["system"] = system
        schema = _extract_schema(response_format)
        if schema is not None:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": _anthropic_schema(schema)}}

        # Use streaming even though callers expect a final object: extraction can ask
        # for large completions, and the Anthropic SDK's streaming helper is the
        # supported path for collecting those into one final message. No temperature /
        # top_p / thinking params — Claude 4.x rejects them; adaptive thinking is the default.
        seconds = float(timeout or runtime_settings.openai_timeout())
        with self._client.with_options(timeout=seconds).messages.stream(**kwargs) as stream:
            message = stream.get_final_message()

        text = _text_from(message)
        stop_reason = getattr(message, "stop_reason", None)
        refusal = None
        if stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            refusal = getattr(details, "explanation", None) or "refused"
        usage = getattr(message, "usage", None)
        return _ChatCompletion(
            choices=[
                _Choice(
                    message=_Message(content=text, refusal=refusal),
                    finish_reason=_FINISH_REASON.get(stop_reason or "", stop_reason),
                )
            ],
            usage=_Usage(
                prompt_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                completion_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            ),
            model=getattr(message, "model", model),
        )


class _Chat:
    def __init__(self, adapter: AnthropicChatAdapter):
        self.completions = _Completions(adapter)


class _Completions:
    def __init__(self, adapter: AnthropicChatAdapter):
        self._adapter = adapter

    def create(self, **kwargs: Any) -> _ChatCompletion:
        return self._adapter._create(**kwargs)
