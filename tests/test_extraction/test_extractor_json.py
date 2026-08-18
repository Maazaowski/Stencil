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

