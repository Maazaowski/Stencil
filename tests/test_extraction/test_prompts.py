"""Static checks for extraction prompt guardrails."""

from stencil.extraction.prompts import EXTRACTION_SYSTEM_PROMPT


def test_extraction_prompt_preserves_cross_page_parent_child_context():
    assert "document structure" in EXTRACTION_SYSTEM_PROMPT
    assert "not isolated page-by-page guesses" in EXTRACTION_SYSTEM_PROMPT
    assert "Preserve parent-child" in EXTRACTION_SYSTEM_PROMPT
    assert "across page breaks" in EXTRACTION_SYSTEM_PROMPT
    assert "carry that context forward" in EXTRACTION_SYSTEM_PROMPT


def test_extraction_prompt_keeps_output_generation_downstream():
    assert "Produce structured invoice facts only" in EXTRACTION_SYSTEM_PROMPT
    assert "downstream code handles workbook" in EXTRACTION_SYSTEM_PROMPT


def test_extraction_prompt_requires_internal_extraction_plan_and_reconciliation():
    assert "Before extracting rows, build an internal extraction plan" in EXTRACTION_SYSTEM_PROMPT
    assert "row granularity" in EXTRACTION_SYSTEM_PROMPT
    assert "service ID source" in EXTRACTION_SYSTEM_PROMPT
    assert "billing reference source" in EXTRACTION_SYSTEM_PROMPT
    assert "amount source" in EXTRACTION_SYSTEM_PROMPT
    assert "tax source" in EXTRACTION_SYSTEM_PROMPT
    assert "reconciliation targets" in EXTRACTION_SYSTEM_PROMPT
    assert "return only the requested JSON" in EXTRACTION_SYSTEM_PROMPT
    assert "reconcile extracted line amounts" in EXTRACTION_SYSTEM_PROMPT
