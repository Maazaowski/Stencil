"""Per-call-type eval runners.

Each runner executes one AI call type live against a labeled case, captures the
prompts it sent (via ``ai_debug.capture_session``), produces the processed output,
and returns a scored ``CaseResult``. Failures degrade to a status=error result so
one bad case never aborts a whole run.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from stencil import ai_debug
from stencil.evals import scoring
from stencil.evals.dataset import EvalCase

logger = structlog.get_logger()

CALL_TYPES = ["classification", "extraction", "model_authoring", "profile_authoring"]


@dataclass
class CaseResult:
    case_id: str
    layout_id: str
    call_type: str
    status: str = "success"  # success | error | skipped
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    prompt_files: list[str] = field(default_factory=list)
    output_rows: list[list] = field(default_factory=list)
    expected_rows: list[list] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "layout_id": self.layout_id,
            "call_type": self.call_type,
            "status": self.status,
            "error": self.error,
            "metrics": self.metrics,
            "prompt_files": self.prompt_files,
            "output_rows": self.output_rows,
            "expected_rows": self.expected_rows,
            "columns": self.columns,
        }


def run_case(case: EvalCase, call_type: str) -> CaseResult:
    started = time.perf_counter()
    runner = {
        "classification": _run_classification,
        "extraction": _run_extraction,
        "model_authoring": _run_model_authoring,
        "profile_authoring": _run_profile_authoring,
    }.get(call_type)
    try:
        if runner is None:
            result = CaseResult(case.case_id, case.layout_id, call_type, status="error",
                                error=f"unknown call_type {call_type}")
        else:
            result = runner(case)
    except Exception as exc:  # one bad case must not abort the run
        logger.error("eval.case_failed", case_id=case.case_id, call_type=call_type, error=str(exc))
        result = CaseResult(case.case_id, case.layout_id, call_type, status="error", error=str(exc))
    duration_ms = int((time.perf_counter() - started) * 1000)
    result.metrics = dict(result.metrics or {})
    result.metrics.setdefault("duration_ms", duration_ms)
    return result


# ---------------------------------------------------------------------------


def _captured_cost(prompt_files: list[str]) -> dict:
    """Sum tokens/cost/latency from the captured ai_debug dumps for this case."""
    tin = tout = dur = 0
    cost = 0.0
    directory = ai_debug.ai_debug_dir()
    for name in prompt_files:
        try:
            meta = _read_meta(directory / name)
        except Exception:
            continue
        tin += int(meta.get("tokens_input") or 0)
        tout += int(meta.get("tokens_output") or 0)
        dur += int(meta.get("duration_ms") or 0)
        cost += float(meta.get("est_cost_usd") or 0.0)
    return {"tokens_input": tin, "tokens_output": tout, "est_cost_usd": round(cost, 6), "latency_ms": dur}


def _read_meta(path) -> dict:
    text = path.read_text(encoding="utf-8")
    start = text.find("{")
    end = text.find("```", start)
    return json.loads(text[start:end].strip()) if start >= 0 and end > start else {}


def _extract_to_preview(case: EvalCase, profile):
    """Shared: AI-extract the case PDF and build the delivered preview under a profile."""
    from stencil.extraction.extractor import build_extracted_document, extract_invoice
    from stencil.extraction.layout import extract_layout_document, render_layout_text
    from stencil.extraction.normalization import apply_layout_profile_hints
    from stencil.fields.loader import resolve_merged_field_schema
    from stencil.output.preview import preview_for_profile

    schema = resolve_merged_field_schema(profile)
    layout = extract_layout_document(case.pdf_path)
    source_text = "\n".join(render_layout_text(layout))
    output_type = profile.classification.output_type

    with ai_debug.capture_session() as prompt_files:
        result = extract_invoice(
            case.pdf_path,
            supplier_name=profile.identity.canonical_name,
            output_type=output_type,
            supplier_profile=profile,
        )
    doc = build_extracted_document(
        result.raw_data, intake_id=f"eval-{case.case_id.replace('/', '-')}",
        extraction_result=result, output_type=output_type, field_schema=schema,
    )
    apply_layout_profile_hints(doc, layout, profile, preserve_existing_line_items=bool(result.ai_calls))
    preview = preview_for_profile(doc, profile, extraction_path="ai")
    return doc, layout, source_text, preview, list(prompt_files)


def _score_deliverable(case, profile, preview, source_text, prompt_files) -> dict:
    from stencil.output.mapper import output_spec_to_columns
    from stencil.specs.loader import resolve_output_spec

    columns = output_spec_to_columns(resolve_output_spec(profile))
    actual_rows = preview.get("rows") or []
    expected_rows = scoring.expected_dicts_to_lists(case.expected_rows(), columns)
    return {
        "columns": [c.xlsx_header for c in columns],
        "actual_rows": actual_rows,
        "metrics": {
            "deliverable": scoring.diff_metrics(case.expected_rows(), actual_rows, columns),
            "hallucinations": scoring.hallucination_flags(actual_rows, columns, source_text, expected_rows),
            "consistency": scoring.consistency_from_preview(preview),
            **_captured_cost(prompt_files),
        },
    }


def _run_extraction(case: EvalCase) -> CaseResult:
    profile = case.load_profile()
    _doc, _layout, source_text, preview, prompt_files = _extract_to_preview(case, profile)
    scored = _score_deliverable(case, profile, preview, source_text, prompt_files)
    return CaseResult(
        case.case_id, case.layout_id, "extraction",
        metrics=scored["metrics"], prompt_files=prompt_files,
        output_rows=scored["actual_rows"], columns=scored["columns"],
        expected_rows=scoring.expected_dicts_to_lists(
            case.expected_rows(), _columns_for(profile)),
    )


def _run_classification(case: EvalCase) -> CaseResult:
    from stencil.classification.classifier import classify_invoice

    profile = case.load_profile()
    with ai_debug.capture_session() as prompt_files:
        result = classify_invoice(case.pdf_path)
    expected = {"supplier_name": profile.identity.canonical_name, "output_type": profile.classification.output_type}
    actual = {"supplier_name": result.supplier_name, "output_type": result.output_type, "confidence": result.confidence}
    metrics = {"classification": scoring.classification_metrics(expected, actual)}
    metrics.update(_captured_cost(list(prompt_files)))
    return CaseResult(
        case.case_id, case.layout_id, "classification",
        metrics=metrics, prompt_files=list(prompt_files),
    )


def _run_profile_authoring(case: EvalCase) -> CaseResult:
    from stencil.extraction.layout import render_layout_text
    from stencil.extraction.normalization import apply_layout_profile_hints
    from stencil.fields.loader import resolve_merged_field_schema
    from stencil.models.authoring import _build_target
    from stencil.output.preview import preview_for_profile
    from stencil.profiles.authoring import (
        InvoiceEvidence,
        author_profile,
        draft_to_supplier_profile_dict,
    )
    from stencil.profiles.schema import SupplierProfile
    from stencil.specs.loader import resolve_output_spec

    base_profile = case.load_profile()
    schema = resolve_merged_field_schema(base_profile)
    spec = resolve_output_spec(base_profile)

    # Ground the assistant on a real extraction of the case, then author + score.
    doc, layout, source_text, _preview, extract_files = _extract_to_preview(case, base_profile)
    from stencil.output.mapper import output_spec_to_columns
    columns = output_spec_to_columns(spec)
    expected_lists = scoring.expected_dicts_to_lists(case.expected_rows(), columns)
    evidence = [InvoiceEvidence(
        label=case.pdf_path.name,
        page_texts=render_layout_text(layout),
        target=_build_target(doc),
        expected_rows=[[("" if c is None else str(c)) for c in row] for row in expected_lists],
    )]

    with ai_debug.capture_session() as author_files:
        authored = author_profile(field_schema=schema, output_spec=spec, invoices=evidence,
                                  conversation=[], current_draft=None)
    profile_dict = draft_to_supplier_profile_dict(
        authored.draft, output_spec_id=base_profile.output_spec_id,
        field_schema_id=base_profile.field_schema_id, field_schema=schema)
    profile_dict["profile_id"] = "eval.authored"
    profile_dict["status"] = "draft"
    authored_profile = SupplierProfile.model_validate(profile_dict)

    apply_layout_profile_hints(doc, layout, authored_profile)
    preview = preview_for_profile(doc, authored_profile, extraction_path="ai")
    prompt_files = list(extract_files) + list(author_files)
    scored = _score_deliverable(case, authored_profile, preview, source_text, prompt_files)
    return CaseResult(
        case.case_id, case.layout_id, "profile_authoring",
        metrics=scored["metrics"], prompt_files=prompt_files,
        output_rows=scored["actual_rows"], columns=scored["columns"], expected_rows=expected_lists,
    )


def _run_model_authoring(case: EvalCase) -> CaseResult:
    # The model-authoring prompt needs the full training-orchestration setup
    # (sampled layout evidence, fingerprint, retry/diff loop in models/training.py),
    # which is heavier than a synchronous case run. Surfaced as skipped so the suite
    # is honest; the committed model.json is already exercised by the corpus tests.
    return CaseResult(
        case.case_id, case.layout_id, "model_authoring",
        status="skipped",
        error="model-authoring eval requires the training orchestration (follow-up)",
    )


def _columns_for(profile):
    from stencil.output.mapper import output_spec_to_columns
    from stencil.specs.loader import resolve_output_spec

    return output_spec_to_columns(resolve_output_spec(profile))
