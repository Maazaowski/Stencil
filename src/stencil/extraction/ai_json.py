"""Shared OpenAI JSON response parsing helpers."""

from __future__ import annotations

import json
from typing import Any


class AIJSONError(ValueError):
    """Base error for AI responses that cannot be parsed as usable JSON."""


class TruncatedAIJSONError(AIJSONError):
    """Raised when an AI JSON response is empty or malformed because it hit length."""


def completion_to_json(response: Any, *, max_output_tokens: int | None = None) -> dict:
    """Parse a chat-completion response into JSON with actionable errors."""
    choice = response.choices[0]
    message = choice.message
    content = getattr(message, "content", None)
    finish_reason = getattr(choice, "finish_reason", None)

    budget = f" ({max_output_tokens})" if max_output_tokens is not None else ""

    if isinstance(content, str) and content.strip():
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            error_cls = TruncatedAIJSONError if finish_reason == "length" else AIJSONError
            raise error_cls(
                "AI returned malformed JSON "
                f"(finish_reason={finish_reason}, length={len(content)}, "
                f"error={exc.msg} at line {exc.lineno} column {exc.colno}). "
                "The response was likely truncated or otherwise invalid; "
                "reduce the requested extraction scope or increase ST_OPENAI_MAX_OUTPUT_TOKENS."
            ) from exc

        # Parseable JSON is not proof of a complete answer. A length-terminated
        # response is incomplete by definition -- with a strict schema it can
        # still close cleanly while omitting rows, and accepting it is how a
        # 656-page document silently returns a short answer. Callers that can
        # subdivide catch this and retry smaller; callers that cannot must fail.
        if finish_reason == "length":
            raise TruncatedAIJSONError(
                "AI response parsed as JSON but stopped at the completion token "
                f"budget{budget}, so it is truncated rather than complete. "
                "Reduce the requested extraction scope; raising "
                "ST_OPENAI_MAX_OUTPUT_TOKENS only moves the ceiling."
            )
        return parsed

    refusal = getattr(message, "refusal", None)
    if finish_reason == "length":
        raise TruncatedAIJSONError(
            "AI returned empty/truncated output - the completion token budget"
            f"{budget} was exhausted before producing JSON. "
            "Increase ST_OPENAI_MAX_OUTPUT_TOKENS."
        )
    raise AIJSONError(
        f"AI returned no JSON content (finish_reason={finish_reason}, refusal={refusal!r})"
    )

