from types import SimpleNamespace

import pytest

from stencil.extraction.extractor import _completion_to_json


def _response(content: str, finish_reason: str = "stop"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, refusal=None),
            )
        ]
    )


def test_completion_to_json_reports_malformed_json_context():
    with pytest.raises(ValueError) as exc_info:
        _completion_to_json(_response('{"line_items":[{"description":"unfinished}', "length"))

    message = str(exc_info.value)
    assert "AI returned malformed JSON" in message
    assert "finish_reason=length" in message
    assert "length=" in message
    assert "increase ST_OPENAI_MAX_OUTPUT_TOKENS" in message


def test_valid_json_that_stopped_at_the_token_ceiling_is_still_truncated():
    """Parseable JSON is not proof of a complete answer.

    With a strict response schema a length-terminated response can still close
    cleanly while omitting rows.  Accepting it is how a 656-page document
    silently returns a short answer instead of failing.
    """
    from stencil.extraction.ai_json import TruncatedAIJSONError

    with pytest.raises(TruncatedAIJSONError) as exc_info:
        _completion_to_json(_response('{"rows":[{"s":"A","a":"1.00"}]}', "length"))

    message = str(exc_info.value)
    assert "truncated rather than complete" in message
    # It must not advise simply raising the ceiling -- that hides the problem
    # one document size further out.
    assert "only moves the ceiling" in message


def test_complete_json_is_returned_untouched():
    payload = _completion_to_json(_response('{"rows":[{"s":"A","a":"1.00"}]}', "stop"))
    assert payload == {"rows": [{"s": "A", "a": "1.00"}]}


def test_empty_content_at_the_ceiling_names_the_budget():
    from stencil.extraction.ai_json import TruncatedAIJSONError, completion_to_json

    with pytest.raises(TruncatedAIJSONError) as exc_info:
        completion_to_json(_response("", "length"), max_output_tokens=32768)
    assert "(32768)" in str(exc_info.value)
