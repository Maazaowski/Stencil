"""Dev-only capture of every OpenAI call's full prompt + response.

A single chokepoint, ``traced_chat_completion``, wraps ``client.chat.completions
.create`` so every AI call site can record exactly what was sent and received —
the system+user prompt, the requested JSON schema, params, and the response with
token counts and cost. This is the evidence base for prompt engineering.

Gated on the live ``debug`` runtime setting (``ST_DEBUG``): when off (production
default) the wrapper is a transparent passthrough that writes nothing. A dump
failure can never break the AI call — it is best-effort and swallowed.
"""

from __future__ import annotations

import contextvars
import json
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog

from stencil import runtime_settings
from stencil.config import settings
from stencil.pricing import estimate_cost_usd

logger = structlog.get_logger()

_MAX_CONTEXT_SLUG = 40

# When set (e.g. during an eval run), capture is forced on regardless of the debug
# setting, and each written dump's filename is appended to the sink list.
_capture_sink: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar("ai_debug_sink", default=None)


@contextmanager
def capture_session():
    """Force prompt capture on for the duration and collect the dump filenames."""
    sink: list[str] = []
    token = _capture_sink.set(sink)
    try:
        yield sink
    finally:
        _capture_sink.reset(token)


def traced_chat_completion(client, *, call_type: str, context: Any = None, **create_kwargs):
    """Call ``client.chat.completions.create(**create_kwargs)`` and, in dev mode,
    dump the request + response to ``work_dir/ai_debug``. Returns the response
    unchanged; on error, dumps the request + error and re-raises."""
    started = time.time()
    try:
        response = client.chat.completions.create(**create_kwargs)
    except Exception as exc:
        _safe_dump(call_type, context, create_kwargs, None, exc, started)
        raise
    _safe_dump(call_type, context, create_kwargs, response, None, started)
    return response


def ai_debug_dir() -> Path:
    return settings.work_dir / "ai_debug"


def debug_capture_enabled() -> bool:
    if _capture_sink.get() is not None:
        return True
    try:
        return bool(runtime_settings.runtime_value("debug"))
    except Exception:
        return False


# ---------------------------------------------------------------------------


def _safe_dump(call_type, context, create_kwargs, response, exc, started) -> None:
    try:
        if not debug_capture_enabled():
            return
        _dump(call_type, context, create_kwargs, response, exc, int((time.time() - started) * 1000))
    except Exception as dump_exc:  # never let capture break the AI call
        logger.warning("ai_debug.dump_failed", call_type=call_type, error=str(dump_exc))


def _dump(call_type, context, create_kwargs, response, exc, duration_ms) -> None:
    model = create_kwargs.get("model")
    messages = create_kwargs.get("messages") or []
    schema = (create_kwargs.get("response_format") or {}).get("json_schema")

    usage = getattr(response, "usage", None)
    tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
    choice = (getattr(response, "choices", None) or [None])[0]
    finish_reason = getattr(choice, "finish_reason", None) if choice else None
    content = getattr(getattr(choice, "message", None), "content", None) if choice else None

    meta = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "call_type": call_type,
        "context": str(context) if context is not None else None,
        "model": model,
        "max_tokens": create_kwargs.get("max_completion_tokens"),
        "finish_reason": finish_reason,
        "tokens_input": tokens_in,
        "tokens_output": tokens_out,
        "est_cost_usd": float(estimate_cost_usd(model, tokens_in, tokens_out)),
        "duration_ms": duration_ms,
        "status": "error" if exc is not None else "success",
        "error": str(exc) if exc is not None else None,
    }

    body = _render_markdown(meta, messages, schema, content, exc)

    directory = ai_debug_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%f")[:-3]
    name = f"{stamp}__{uuid4().hex[:8]}__{_slug(call_type)}__{_slug(meta['context'] or '')}.md"
    (directory / name).write_text(body, encoding="utf-8")
    sink = _capture_sink.get()
    if sink is not None:
        sink.append(name)
    _prune(directory)


def _render_markdown(meta, messages, schema, content, exc) -> str:
    parts = [
        "```json",
        json.dumps(meta, indent=2),
        "```",
        f"\n# AI call: {meta['call_type']}",
    ]
    for message in messages:
        role = str(message.get("role", "?")).capitalize()
        parts.append(f"\n## {role}")
        parts.append(_render_message_content(message.get("content")))
    if schema is not None:
        parts.append("\n## JSON schema requested")
        parts.append("```json\n" + json.dumps(schema, indent=2) + "\n```")
    parts.append("\n## Response")
    if exc is not None:
        parts.append(f"**ERROR:** {exc}")
    else:
        parts.append("```\n" + (content or "(empty)") + "\n```")
    return "\n".join(parts) + "\n"


def _render_message_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        rendered = []
        for part in content:
            if not isinstance(part, dict):
                rendered.append(str(part))
            elif part.get("type") == "text":
                rendered.append(str(part.get("text", "")))
            elif part.get("type") == "image_url":
                url = ((part.get("image_url") or {}).get("url")) or ""
                kb = int(len(url) * 0.75 / 1024)
                rendered.append(f"[image omitted: ~{kb} KB]")
            else:
                rendered.append(f"[{part.get('type', 'part')} omitted]")
        return "\n".join(rendered)
    return str(content)


def _slug(value: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in str(value)).strip("-")
    return (safe or "none")[:_MAX_CONTEXT_SLUG]


def _prune(directory: Path) -> None:
    try:
        cap = int(settings.ai_debug_max_files)
    except Exception:
        cap = 300
    if cap <= 0:
        return
    files = sorted(directory.glob("*.md"))
    for stale in files[:-cap]:
        stale.unlink(missing_ok=True)
