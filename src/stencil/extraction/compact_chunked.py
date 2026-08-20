"""Compact, chunked AI extraction for large invoices.

This module keeps the public canonical invoice contract unchanged. The compact
shape is only the internal wire format between Stencil and the AI model.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

from stencil import runtime_settings
from stencil.ai_debug import traced_chat_completion
from stencil.extraction.ai_json import AIJSONError, TruncatedAIJSONError, completion_to_json
from stencil.extraction.instructions import compile_instructions
from stencil.extraction.layout import (
    LayoutDocument,
    VisualRow,
    extract_layout_document,
    scan_pdf_pages,
)
from stencil.extraction.markers import marker_in_text, marker_terms
from stencil.extraction.page_roles import classify_pages
from stencil.extraction.plan_executor import execute_extraction_plan, resolve_plan_page_numbers
from stencil.extraction.prompts import build_extraction_user_prompt
from stencil.output.mapper import output_spec_to_columns
from stencil.profiles.plan import effective_extraction_plan
from stencil.profiles.schema import SupplierProfile
from stencil.specs.loader import resolve_output_spec

logger = structlog.get_logger()

DOC_ALIAS_TO_FIELD = {
    "sn": "supplier_name",
    "in": "invoice_number",
    "id": "invoice_date",
    "dd": "due_date",
    "ac": "account_number",
    "cu": "currency",
    "bp0": "billing_period_start",
    "bp1": "billing_period_end",
    "po": "po_number",
    "pt": "payment_terms",
    "st": "subtotal",
    "tx": "tax",
    "fe": "fees",
    "cc": "current_charges",
    "td": "total_due",
    "tr": "tax_rate",
}
FIELD_TO_DOC_ALIAS = {value: key for key, value in DOC_ALIAS_TO_FIELD.items()}

ROW_ALIAS_TO_FIELD = {
    "r": "_row_ref",
    "s": "service_id",
    "b": "billing_reference",
    "d": "description",
    "c": "charge_type",
    "a": "amount",
    "x": "tax_amount",
    "cu": "currency",
    "bp0": "billing_period_start",
    "bp1": "billing_period_end",
    "q": "quantity",
    "u": "unit_rate",
    "pl": "plan_cost",
    "eq": "equipment_cost",
    "p": "source_page",
}
FIELD_TO_ROW_ALIAS = {value: key for key, value in ROW_ALIAS_TO_FIELD.items()}

DEFAULT_DOC_FIELDS = {
    "supplier_name",
    "invoice_number",
    "invoice_date",
    "due_date",
    "account_number",
    "currency",
    "subtotal",
    "tax",
    "fees",
    "current_charges",
    "total_due",
    "tax_rate",
}
DEFAULT_ROW_FIELDS = {"service_id", "billing_reference", "charge_type", "amount", "tax_amount"}
TOTAL_FIELDS = {"subtotal", "tax", "fees", "current_charges", "total_due", "tax_rate"}
HEADER_FIELDS = set(DOC_ALIAS_TO_FIELD.values()) - TOTAL_FIELDS


@dataclass
class LayoutRowLine:
    page: int
    row_id: str
    text: str


@dataclass(frozen=True)
class CandidateRow:
    row_id: str
    page: int
    service_id: str
    evidence: str
    billing_reference: str | None = None
    amount: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None


@dataclass
class LayoutChunk:
    chunk_id: str
    rows: list[LayoutRowLine]
    retry_depth: int = 0
    candidates: list[CandidateRow] = field(default_factory=list)
    missing_candidates: list[CandidateRow] = field(default_factory=list)
    strict_retry: bool = False

    @property
    def char_count(self) -> int:
        return sum(len(row.text) + 1 for row in self.rows)


@dataclass
class CompactCall:
    call_type: str
    ai_model_name: str
    tokens_input: int = 0
    tokens_output: int = 0
    duration_ms: int = 0
    status: str = "success"
    error_message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "call_type": self.call_type,
            "ai_model_name": self.ai_model_name,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error_message": self.error_message,
            "details": self.details,
        }


@dataclass
class CompactExtraction:
    raw_data: dict[str, Any]
    tokens_input: int
    tokens_output: int
    duration_ms: int
    ai_model_name: str
    ai_calls: list[dict[str, Any]]
    artifacts: dict[str, Any]


def _plan_rows_to_compact(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for row in rows:
        payload = {
            FIELD_TO_ROW_ALIAS[field]: value
            for field, value in row.items()
            if field in FIELD_TO_ROW_ALIAS and value is not None
        }
        row_ref = row.get("source_row_id")
        if row_ref:
            payload[FIELD_TO_ROW_ALIAS["_row_ref"]] = row_ref
        if payload:
            compact.append(payload)
    return compact


def _plan_document_to_compact(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        FIELD_TO_DOC_ALIAS[field]: value
        for field, value in fields.items()
        if field in FIELD_TO_DOC_ALIAS and value is not None
    }


def extract_invoice_compact_chunked(
    pdf_path: Path,
    *,
    client,
    supplier_name: str | None,
    output_type: str,
    supplier_profile: SupplierProfile | None,
    supplier_profile_data: dict | None,
    field_schema,
    artifact_dir: Path | None = None,
    page_numbers: list[int] | None = None,
) -> Any:
    """Extract a PDF with compact scalar + chunked line-item AI calls.

    ``page_numbers`` (1-based) hard-restricts which pages are read. It overrides
    the discovery plan's own page selection, because a caller that asks for a
    specific sample means it -- see ``models/sample_authoring``.
    """
    from stencil.extraction.extractor import ExtractionResult

    started = time.time()
    model_name = runtime_settings.openai_model_extraction()
    page_count = _pdf_page_count(pdf_path)
    plan_enabled = bool(runtime_settings.runtime_value("profile_discovery_engine_enabled"))
    plan_is_persisted = bool(
        supplier_profile is not None and supplier_profile.advanced.extraction_plan is not None
    )
    persisted_plan = (
        effective_extraction_plan(supplier_profile)
        if supplier_profile is not None and plan_enabled
        else None
    )
    shadow_mode = bool(runtime_settings.runtime_value("profile_discovery_shadow_mode"))
    authoring_validation = artifact_dir is not None and "authoring" in {
        part.lower() for part in artifact_dir.parts
    }
    effective_shadow_mode = (shadow_mode and not authoring_validation) or not plan_is_persisted
    scan = scan_pdf_pages(pdf_path) if persisted_plan is not None else None
    plan_pages = resolve_plan_page_numbers(persisted_plan, scan) if persisted_plan and scan else []
    plan_region_missing = bool(persisted_plan and persisted_plan.regions and not plan_pages)
    if plan_region_missing:
        # A stale/sample-specific marker must never reduce extraction to scalar
        # header/footer pages. Full-document AI extraction is the safe fallback.
        plan_pages = list(range(1, page_count + 1))
    # Scalars still need the invoice header/footer. In active mode only these and
    # the deterministic line-item region receive coordinate layout processing.
    scalar_pages = set(range(1, min(page_count, 5) + 1))
    scalar_pages.update(range(max(1, page_count - 1), page_count + 1))
    selected_layout_pages = sorted(set(plan_pages) | scalar_pages)
    artifact_dir = _prepare_artifact_dir(artifact_dir)
    if page_numbers:
        requested_pages: list[int] | None = sorted(
            {n for n in page_numbers if 1 <= n <= (page_count or n)}
        )
    elif effective_shadow_mode or persisted_plan is None:
        requested_pages = None
    else:
        requested_pages = selected_layout_pages
    layout_document = extract_layout_document(
        pdf_path,
        max_pages=page_count or 1,
        include_markdown=False,
        page_numbers=requested_pages,
    )
    _write_json(artifact_dir, "layout.json", layout_document.model_dump(mode="json"))
    all_rows = _layout_rows(layout_document)
    detail_rows = _detail_region_rows(all_rows, supplier_profile_data)
    if not detail_rows:
        detail_rows = all_rows

    doc_fields, row_fields = _fields_needed_for_compact_output(supplier_profile)
    page_map = classify_pages(layout_document)
    scalar_context = _scalar_context_rows(
        all_rows, supplier_profile_data, context_pages=set(page_map.context_pages())
    )
    plan_result = (
        execute_extraction_plan(persisted_plan, layout_document)
        if persisted_plan is not None
        else None
    )
    deterministic_headers = (
        _plan_document_to_compact(plan_result.document_fields) if plan_result is not None else {}
    )
    unresolved_doc_fields = doc_fields - {
        DOC_ALIAS_TO_FIELD[alias] for alias in deterministic_headers
    }

    calls: list[CompactCall] = []
    chunk_artifacts: list[dict[str, Any]] = []
    scalar_prompt = _build_scalar_prompt(
        supplier_name=supplier_name,
        output_type=output_type,
        supplier_profile_data=supplier_profile_data,
        field_schema=field_schema,
        doc_fields=unresolved_doc_fields,
        rows=scalar_context,
    )
    scalar_data = _call_json_schema(
        client,
        call_type="ai_scalars",
        prompt=scalar_prompt,
        schema=build_compact_scalar_schema(doc_fields),
        details={"row_count": len(scalar_context)},
        calls=calls,
    )
    scalar_data["h"] = {**deterministic_headers, **(scalar_data.get("h") or {})}
    _write_json(artifact_dir, "scalars.compact.json", scalar_data)

    if persisted_plan is not None:
        plan_result = execute_extraction_plan(
            persisted_plan,
            layout_document,
            initial_fields={
                DOC_ALIAS_TO_FIELD.get(alias, alias): value
                for alias, value in (scalar_data.get("h") or {}).items()
            },
        )
    deterministic_rows = _plan_rows_to_compact(plan_result.rows) if plan_result is not None else []
    deterministic_success = bool(
        plan_result is not None
        and deterministic_rows
        and not plan_result.reconciliation_failures
        and not effective_shadow_mode
    )

    candidates = detect_candidate_rows(detail_rows, supplier_profile_data)
    chunks = [] if deterministic_success else chunk_layout_rows(detail_rows, candidates=candidates)
    compact_rows: list[dict[str, Any]] = list(deterministic_rows if deterministic_success else [])
    chunk_concurrency = int(runtime_settings.runtime_value("ai_chunk_concurrency"))
    if chunk_concurrency > 1 and len(chunks) > 1:
        max_workers = max(1, chunk_concurrency)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    _extract_chunk_rows,
                    client,
                    chunk,
                    output_type=output_type,
                    supplier_profile_data=supplier_profile_data,
                    field_schema=field_schema,
                    row_fields=row_fields,
                    calls=calls,
                    artifact_dir=artifact_dir,
                    chunk_artifacts=chunk_artifacts,
                )
                for chunk in chunks
            ]
            for future in futures:
                compact_rows.extend(future.result())
    else:
        for chunk in chunks:
            compact_rows.extend(
                _extract_chunk_rows(
                    client,
                    chunk,
                    output_type=output_type,
                    supplier_profile_data=supplier_profile_data,
                    field_schema=field_schema,
                    row_fields=row_fields,
                    calls=calls,
                    artifact_dir=artifact_dir,
                    chunk_artifacts=chunk_artifacts,
                )
            )

    merged_rows = merge_compact_rows(compact_rows)
    if persisted_plan is not None and persisted_plan.row_context_rules:
        _apply_row_context_rules(merged_rows, all_rows, persisted_plan)
    ungrounded_rows = [] if deterministic_success else [
        row for row in merged_rows if not _clean_row_ref(row.get("r"))
    ]
    if ungrounded_rows:
        merged_rows = [row for row in merged_rows if _clean_row_ref(row.get("r"))]
    rejected_rows: list[dict[str, Any]] = []
    if candidates and bool(
        ((supplier_profile_data or {}).get("advanced") or {}).get("require_line_item_identifier")
    ):
        valid_identifiers = {
            _normalize_candidate_id(value)
            for candidate in candidates
            for value in (candidate.service_id, candidate.billing_reference)
            if _normalize_candidate_id(value)
        }
        accepted_rows = []
        for row in merged_rows:
            identifiers = {
                _normalize_candidate_id(row.get("s")),
                _normalize_candidate_id(row.get("b")),
            } - {""}
            if identifiers and identifiers.isdisjoint(valid_identifiers):
                rejected_rows.append(row)
            else:
                accepted_rows.append(row)
        merged_rows = accepted_rows
    line_item_defaults = _line_item_defaults_from_context(
        scalar_context,
        supplier_profile_data,
        field_schema,
        excluded_row_ids={row.row_id for row in detail_rows},
    )
    missing_candidates = _missing_candidates(candidates, merged_rows)
    recovered_candidate_rows = _recover_missing_total_candidate_rows(
        supplier_profile_data,
        missing_candidates,
    )
    if recovered_candidate_rows:
        merged_rows = merge_compact_rows([*merged_rows, *recovered_candidate_rows])
        missing_candidates = _missing_candidates(candidates, merged_rows)
    # A chunk that truncated and was recovered by bisection still tells us the
    # document is at the output ceiling -- the next one like it may not recover.
    truncated_calls = [call for call in calls if call.details.get("truncated")]
    completeness_warnings = _completeness_warnings(candidates, merged_rows, missing_candidates)
    reconciliation_warnings = _total_candidate_reconciliation_warnings(
        candidates,
        scalar_data.get("h") or {},
        detail_rows,
        supplier_profile_data,
    )
    compact_payload = {
        "h": scalar_data.get("h") or {},
        "rows": merged_rows,
        "line_item_defaults": line_item_defaults,
    }
    raw_data = compact_to_legacy_raw(
        compact_payload,
        supplier_name=supplier_name,
        output_type=output_type,
        currency_rules=(supplier_profile_data or {}).get("advanced", {}).get("currency"),
        line_item_hints=(supplier_profile_data or {}).get("advanced", {}).get("line_item_hints"),
    )
    # Confidence used to be stamped 1.0 unconditionally, which made it a fake
    # signal in the one column operators read: of jobs reporting >=99%, 40%
    # failed reconciliation. Report the one completeness fact this path actually
    # measures -- the share of detected candidate rows that survived extraction.
    if candidates:
        raw_data["overall_confidence"] = round(
            max(0.0, 1.0 - (len(missing_candidates) / len(candidates))), 4
        )
    elif truncated_calls:
        # No candidates to measure against, but we know output was cut short.
        raw_data["overall_confidence"] = 0.0

    extraction_warnings = [*completeness_warnings, *reconciliation_warnings]
    if truncated_calls:
        budget = runtime_settings.openai_max_output_tokens()
        extraction_warnings.insert(
            0,
            f"{len(truncated_calls)} AI call(s) hit the {budget}-token output ceiling and "
            "were retried on smaller inputs. The delivered rows may still be incomplete; "
            "this document is too large for single-pass extraction.",
        )
    if plan_region_missing:
        extraction_warnings.insert(
            0,
            "Configured line-item region was not found; processed the full document instead.",
        )
    if rejected_rows:
        examples = ", ".join(
            str(row.get("s") or row.get("b") or "unknown") for row in rejected_rows[:10]
        )
        extraction_warnings.append(
            f"Rejected {len(rejected_rows)} ungrounded line item row(s) whose identifiers "
            f"did not match source candidates. Examples: {examples}"
        )
    if ungrounded_rows:
        extraction_warnings.append(
            f"Rejected {len(ungrounded_rows)} line item row(s) without valid source row references."
        )
    if extraction_warnings:
        raw_data["warnings"] = [*raw_data.get("warnings", []), *extraction_warnings]
    if rejected_rows or ungrounded_rows:
        raw_data["exceptions"] = [
            *raw_data.get("exceptions", []),
            extraction_warnings[-1],
        ]
        raw_data["exceptions"] = [*raw_data.get("exceptions", []), *extraction_warnings]

    # Setup conflicts (notes contradicting executable profile fields) are a
    # configuration problem, not an extraction failure: warn, never except.
    compiled = compile_instructions(
        supplier_profile=supplier_profile_data,
        field_schema=field_schema,
        output_type=output_type,
        supplier_name=supplier_name,
    )
    if compiled.conflicts:
        compiled_payload = compiled.as_dict()
        raw_data["conflicts"] = compiled_payload["conflicts"]
        raw_data["ignored_notes"] = compiled_payload["ignored_notes"]
        raw_data["warnings"] = [*raw_data.get("warnings", []), *compiled.conflict_warnings()]

    duration_ms = int((time.time() - started) * 1000)
    manifest = {
        "mode": "compact_chunked",
        "page_count": page_count,
        "layout_row_count": len(all_rows),
        "detail_row_count": len(detail_rows),
        "candidate_row_count": len(candidates),
        "missing_candidate_count": len(missing_candidates),
        "missing_candidate_examples": [candidate.service_id for candidate in missing_candidates[:20]],
        "recovered_candidate_row_count": len(recovered_candidate_rows),
        "reconciliation_warnings": reconciliation_warnings,
        "chunk_count": len(chunks),
        "truncated_call_count": len(truncated_calls),
        "requested_page_numbers": requested_pages,
        "discovery_plan": {
            "available": persisted_plan is not None,
            "persisted": plan_is_persisted,
            "shadow_mode": shadow_mode,
            "authoring_validation": authoring_validation,
            "selected_pages": plan_pages,
            "layout_pages": [page.page_number for page in layout_document.pages],
            "deterministic_row_count": len(deterministic_rows),
            "used_deterministic_rows": deterministic_success,
            "reconciliation_failures": (
                plan_result.reconciliation_failures if plan_result is not None else []
            ),
            "fallback_reason": (
                None if deterministic_success
                else "shadow_mode" if persisted_plan is not None and effective_shadow_mode
                else "no_plan" if persisted_plan is None
                else "plan_produced_no_rows" if not deterministic_rows
                else "plan_reconciliation_failed"
            ),
        },
        "compact_row_count": len(merged_rows),
        "tokens_input": sum(call.tokens_input for call in calls),
        "tokens_output": sum(call.tokens_output for call in calls),
        "duration_ms": duration_ms,
        "chunks": chunk_artifacts,
        "calls": [call.as_dict() for call in calls],
    }
    _write_json(artifact_dir, "manifest.json", manifest)

    logger.info(
        "extraction.compact_chunked.completed",
        supplier=supplier_name,
        chunks=len(chunks),
        line_items=len(raw_data.get("line_items", [])),
        tokens=manifest["tokens_input"] + manifest["tokens_output"],
        duration_ms=duration_ms,
    )

    return ExtractionResult(
        raw_data=raw_data,
        tokens_input=manifest["tokens_input"],
        tokens_output=manifest["tokens_output"],
        duration_ms=duration_ms,
        ai_model_name=model_name,
        ai_calls=[call.as_dict() for call in calls],
        artifacts={key: value for key, value in manifest.items() if key != "calls"},
    )


def build_compact_scalar_schema(doc_fields: set[str] | None = None) -> dict[str, Any]:
    aliases = _doc_aliases(doc_fields or DEFAULT_DOC_FIELDS)
    return {
        "name": "compact_invoice_scalars",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "h": _strict_object({alias: _schema_for_doc_field(field) for alias, field in aliases.items()}),
            },
            "required": ["h"],
            "additionalProperties": False,
        },
    }


def build_compact_line_item_schema(row_fields: set[str] | None = None) -> dict[str, Any]:
    aliases = _row_aliases(row_fields or DEFAULT_ROW_FIELDS)
    return {
        "name": "compact_invoice_line_items",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "rows": {
                    "type": "array",
                    "items": _strict_object(
                        {alias: _schema_for_row_field(field) for alias, field in aliases.items()}
                    ),
                },
            },
            "required": ["rows"],
            "additionalProperties": False,
        },
    }


def chunk_layout_rows(rows: list[LayoutRowLine], candidates: list[CandidateRow] | None = None) -> list[LayoutChunk]:
    if not rows:
        return [LayoutChunk(chunk_id="0001", rows=[])]

    max_chars = max(1000, int(runtime_settings.runtime_value("ai_chunk_max_layout_chars")))
    overlap = max(0, int(runtime_settings.runtime_value("ai_chunk_overlap_rows")))
    candidates_by_row_id: dict[str, list[CandidateRow]] = {}
    for candidate in candidates or []:
        candidates_by_row_id.setdefault(candidate.row_id, []).append(candidate)

    def _chunk(chunk_id: str, chunk_rows: list[LayoutRowLine]) -> LayoutChunk:
        chunk_candidates = [
            candidate
            for row in chunk_rows
            for candidate in candidates_by_row_id.get(row.row_id, [])
        ]
        return LayoutChunk(chunk_id=chunk_id, rows=chunk_rows, candidates=chunk_candidates)

    chunks: list[LayoutChunk] = []
    current: list[LayoutRowLine] = []
    current_chars = 0

    for row in rows:
        row_chars = len(row.text) + 1
        if current and current_chars + row_chars > max_chars:
            chunks.append(_chunk(f"{len(chunks) + 1:04d}", current))
            current = current[-overlap:] if overlap else []
            current_chars = sum(len(existing.text) + 1 for existing in current)
        current.append(row)
        current_chars += row_chars

    if current:
        chunks.append(_chunk(f"{len(chunks) + 1:04d}", current))
    return chunks


def detect_candidate_rows(rows: list[LayoutRowLine], supplier_profile_data: dict | None) -> list[CandidateRow]:
    """Detect profile-owned rows that should probably produce delivered rows.

    This is a completeness guard for AI chunking, not deterministic extraction.
    It intentionally activates only for layouts with enough structured hints to
    avoid policing free-form invoices.
    """
    advanced = (supplier_profile_data or {}).get("advanced") or {}
    structure = advanced.get("document_structure") or {}
    hints = advanced.get("line_item_hints") or {}
    include_zero_amount = bool(
        advanced.get("include_zero_amount_line_items") or hints.get("include_zero_amount_line_items")
    )
    plan = advanced.get("extraction_plan") or {}
    selector = plan.get("row_selector") or {}
    identifier_pattern = str(selector.get("identifier_pattern") or "").strip()
    include_pattern = str(selector.get("include_pattern") or "").strip()
    if identifier_pattern:
        planned = _plan_identifier_candidates(
            rows,
            identifier_pattern=identifier_pattern,
            include_pattern=include_pattern,
        )
        if planned:
            return planned
    return _reject_label_candidates(_detect_candidate_rows(rows, structure, hints, include_zero_amount), hints)


def _plan_identifier_candidates(
    rows: list[LayoutRowLine],
    *,
    identifier_pattern: str,
    include_pattern: str,
) -> list[CandidateRow]:
    """Apply the profile-owned identifier regex before layout-specific detectors."""
    try:
        identifier_re = re.compile(identifier_pattern, re.IGNORECASE)
        include_re = re.compile(include_pattern, re.IGNORECASE) if include_pattern else None
    except re.error:
        return []
    candidates: list[CandidateRow] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if include_re is not None and include_re.search(row.text) is None:
            continue
        match = identifier_re.search(row.text)
        if match is None:
            continue
        service_id = match.group(1) if match.lastindex else match.group(0)
        key = (row.row_id, service_id)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(CandidateRow(
            row_id=row.row_id,
            page=row.page,
            service_id=service_id,
            evidence=_clean_layout_value(row.text)[:500],
        ))
    return candidates


def _detect_candidate_rows(
    rows: list[LayoutRowLine],
    structure: dict[str, Any],
    hints: dict[str, Any],
    include_zero_amount: bool,
) -> list[CandidateRow]:
    if _table_candidate_detection_enabled(hints):
        return _table_candidate_rows(rows, hints)
    if _total_row_candidate_detection_enabled(hints):
        return _total_row_candidate_rows(rows, hints, include_zero_amount=include_zero_amount)
    # Before the marker detector: when detail_start_marker is a section heading
    # ("SERVICE LEVEL ACTIVITY") rather than a per-service row prefix, the marker
    # detector finds only the heading, reads no id from it, and yields nothing.
    if _charge_row_candidate_detection_enabled(hints):
        return _charge_row_candidate_rows(rows, hints, include_zero_amount=include_zero_amount)
    if _column_service_total_candidate_detection_enabled(hints):
        return _column_service_total_candidate_rows(rows, hints, include_zero_amount=include_zero_amount)
    if _marker_candidate_detection_enabled(structure, hints):
        return _marker_candidate_rows(rows, structure, hints, include_zero_amount=include_zero_amount)
    if _labeled_service_candidate_detection_enabled(hints):
        return _labeled_service_candidate_rows(rows, hints, include_zero_amount=include_zero_amount)
    return []


def _reject_label_candidates(
    candidates: list[CandidateRow], hints: dict[str, Any]
) -> list[CandidateRow]:
    """Drop candidates whose "service id" is really a printed column label.

    Such a candidate can never be satisfied: the model will never emit a row
    whose service_id is the word "Description". Each one keeps the chunk in the
    missing-candidate state, driving strict retries and recursive splits to the
    depth cap. On GTT this turned 15 chunks into ~75 sequential AI calls and 47
    minutes, and still produced a 6%-complete workbook.
    """
    labels = {
        str(label).strip().lower()
        for label in [
            *(hints.get("table_column_labels") or []),
            hints.get("service_id_column_label"),
            hints.get("billing_reference_column_label"),
            hints.get("amount_column_label"),
            hints.get("tax_amount_column_label"),
        ]
        if str(label or "").strip()
    }
    if not labels:
        return candidates
    return [c for c in candidates if str(c.service_id).strip().lower() not in labels]


_CELL_RE = re.compile(
    r"\[\s*(?P<cell_id>p\d+\.r\d+\.c\d+)\s+c(?P<col>\d+)\s+(?P<role>\S+)\s+"
    r"x=(?P<x0>-?[\d.]+)-(?P<x1>-?[\d.]+)\]\s*(?P<text>[^|\[]*)"
)


def _parsed_cells(text: str) -> list[tuple[str, float, float, str]]:
    """(role, x0, x1, text) for each rendered cell of a layout row line."""
    cells = []
    for match in _CELL_RE.finditer(text):
        cells.append((
            match.group("role"),
            float(match.group("x0")),
            float(match.group("x1")),
            match.group("text").strip(),
        ))
    return cells


def _column_service_total_candidate_detection_enabled(hints: dict[str, Any]) -> bool:
    """Opt in to column-aware service-total candidates.

    Some invoices print independent service blocks in two side-by-side columns.
    Their circuit label and delivered total can land in different AI chunks even
    though layout coordinates still identify the owning column.  This detector is
    deliberately narrow so profiles without this complete policy keep their
    historical candidate behavior.
    """
    return bool(
        not str(hints.get("line_item_granularity") or "").strip()
        and str(hints.get("service_id_preference") or "").strip() == "child_identifier"
        and str(hints.get("billing_reference_preference") or "").strip() == "separate_column"
        and str(hints.get("service_id_column_label") or "").strip()
        and str(hints.get("amount_column_label") or "").strip()
        and str(hints.get("service_id_value_pattern") or "").strip()
        and str(hints.get("amount_source") or "").strip() == "label_amount"
    )


def _column_service_total_candidate_rows(
    rows: list[LayoutRowLine],
    hints: dict[str, Any],
    *,
    include_zero_amount: bool = False,
) -> list[CandidateRow]:
    """Anchor a circuit id to its labeled total in the same physical column."""
    service_label = str(hints.get("service_id_column_label") or "").strip()
    amount_label = str(hints.get("amount_column_label") or "").strip()
    service_pattern = str(hints.get("service_id_value_pattern") or "").strip()
    billing_label = str(hints.get("billing_reference_column_label") or "").strip()
    billing_pattern = str(hints.get("billing_reference_value_pattern") or "").strip()
    starts = _layout_column_starts(rows, (service_label, amount_label))
    if not starts:
        return []

    candidates: list[CandidateRow] = []
    seen: set[tuple[str, str]] = set()
    for row_index, row in enumerate(rows):
        cells = _parsed_cells(row.text)
        for _role, x0, _x1, text in cells:
            if amount_label.lower() not in text.lower():
                continue
            lower, upper = _layout_column_band(x0, starts)
            amount = _last_amount_in_column(cells, lower, upper)
            if amount is None or (not include_zero_amount and amount == 0):
                continue
            service_match = _nearest_service_in_column(
                rows,
                row_index,
                lower=lower,
                upper=upper,
                service_pattern=service_pattern,
                amount_label=amount_label,
            )
            if service_match is None:
                continue
            service_id, service_row_index = service_match
            key = (row.row_id, service_id)
            if key in seen:
                continue
            seen.add(key)
            evidence = _column_candidate_evidence(
                rows,
                service_row_index,
                row_index,
                lower=lower,
                upper=upper,
            )
            candidates.append(CandidateRow(
                row_id=row.row_id,
                page=row.page,
                service_id=service_id,
                evidence=evidence,
                billing_reference=_billing_reference_in_column(
                    rows,
                    service_row_index,
                    row_index,
                    lower=lower,
                    upper=upper,
                    billing_label=billing_label,
                    billing_pattern=billing_pattern,
                ),
                amount=amount,
            ))
    return candidates


def _layout_column_starts(rows: list[LayoutRowLine], labels: tuple[str, ...]) -> list[float]:
    positions = sorted(
        x0
        for row in rows
        for _role, x0, _x1, text in _parsed_cells(row.text)
        if any(label and label.lower() in text.lower() for label in labels)
    )
    clusters: list[list[float]] = []
    for position in positions:
        if not clusters or position - clusters[-1][-1] > 80:
            clusters.append([position])
        else:
            clusters[-1].append(position)
    return [min(cluster) for cluster in clusters]


def _layout_column_band(x0: float, starts: list[float]) -> tuple[float, float]:
    index = max((i for i, start in enumerate(starts) if start <= x0 + 10), default=0)
    lower = starts[index] - 20
    upper = starts[index + 1] if index + 1 < len(starts) else float("inf")
    return lower, upper


def _cell_in_column(x0: float, lower: float, upper: float) -> bool:
    return lower <= x0 < upper


def _last_amount_in_column(
    cells: list[tuple[str, float, float, str]], lower: float, upper: float
) -> Decimal | None:
    amounts = [
        (x0, _candidate_decimal(text))
        for role, x0, _x1, text in cells
        if role in {"amount", "identifier"} and _cell_in_column(x0, lower, upper)
    ]
    parsed = [(x0, amount) for x0, amount in amounts if amount is not None]
    return max(parsed, key=lambda item: item[0])[1] if parsed else None


def _nearest_service_in_column(
    rows: list[LayoutRowLine],
    amount_row_index: int,
    *,
    lower: float,
    upper: float,
    service_pattern: str,
    amount_label: str,
) -> tuple[str, int] | None:
    for row_index in range(amount_row_index, -1, -1):
        cells = _parsed_cells(rows[row_index].text)
        for _role, x0, _x1, text in cells:
            if not _cell_in_column(x0, lower, upper):
                continue
            service_id = _service_id_from_pattern(text, service_pattern)
            if service_id:
                return service_id, row_index
        if row_index != amount_row_index and any(
            amount_label.lower() in text.lower() and _cell_in_column(x0, lower, upper)
            for _role, x0, _x1, text in cells
        ):
            break
    # A service can continue from the right column at the bottom of one page to
    # the left column at the top of the next.  Only use an across-column fallback
    # when the circuit is on an earlier page and no other delivered total appears
    # between it and the current amount row.
    amount_page = rows[amount_row_index].page
    for row_index in range(amount_row_index - 1, -1, -1):
        cells = _parsed_cells(rows[row_index].text)
        if any(amount_label.lower() in text.lower() for _role, _x0, _x1, text in cells):
            break
        if rows[row_index].page >= amount_page:
            continue
        for _role, _x0, _x1, text in cells:
            service_id = _service_id_from_pattern(text, service_pattern)
            if service_id:
                return service_id, row_index
    return None


def _candidate_decimal(text: str) -> Decimal | None:
    match = re.fullmatch(r"\s*\$?\s*(-?\d[\d,]*\.\d{2})\s*(CR)?\s*", text, re.IGNORECASE)
    if not match:
        return None
    value = _parse_money_value(match.group(1))
    if value is None:
        return None
    return -abs(value) if match.group(2) else value


def _billing_reference_in_column(
    rows: list[LayoutRowLine],
    start_index: int,
    end_index: int,
    *,
    lower: float,
    upper: float,
    billing_label: str,
    billing_pattern: str,
) -> str | None:
    for row in rows[start_index:end_index + 1]:
        for _role, x0, _x1, text in _parsed_cells(row.text):
            if not _cell_in_column(x0, lower, upper):
                continue
            if billing_pattern:
                value = _service_id_from_pattern(text, billing_pattern)
                if value:
                    return value
            if billing_label and billing_label.lower() in text.lower():
                return billing_label
    return None


def _column_candidate_evidence(
    rows: list[LayoutRowLine],
    start_index: int,
    end_index: int,
    *,
    lower: float,
    upper: float,
) -> str:
    text = " ".join(
        cell_text
        for row in rows[start_index:end_index + 1]
        for _role, x0, _x1, cell_text in _parsed_cells(row.text)
        if _cell_in_column(x0, lower, upper)
    )
    return _candidate_evidence(text)


def _charge_row_candidate_detection_enabled(hints: dict[str, Any]) -> bool:
    """Opt-in: only layouts that declare the child-identifier charge-row policy.

    Deliberately narrow. A looser gate would change candidate behavior for every
    existing per_charge_row profile.
    """
    return bool(
        str(hints.get("line_item_granularity") or "").strip() == "per_charge_row"
        and str(hints.get("service_id_preference") or "").strip() == "child_identifier"
        and str(hints.get("service_id_column_label") or "").strip()
        and str(hints.get("service_id_value_pattern") or "").strip()
    )


def _service_column_bounds_from_rows(
    rows: list[LayoutRowLine], label: str
) -> tuple[float, float] | None:
    for row in rows:
        if _layout_role(row.text) != "header":
            continue
        for _role, x0, x1, text in _parsed_cells(row.text):
            if text.strip().lower() == label.strip().lower():
                return (x0 - 4.0, x1 + 40.0)
    return None


def _charge_row_candidate_rows(
    rows: list[LayoutRowLine],
    hints: dict[str, Any],
    *,
    include_zero_amount: bool = False,
) -> list[CandidateRow]:
    """Every printed charge row is a delivered row; carry the child id down to it.

    Only rows that actually carry a money amount become candidates. A candidate
    the AI can never satisfy (a header, a label, a row with no amount) drives the
    strict-retry/split loop forever — that is what happened on GTT, where the
    literal word "Description" became a permanently-missing candidate.
    """
    pattern_text = str(hints.get("service_id_value_pattern") or "").strip()
    try:
        pattern = re.compile(pattern_text)
    except re.error:
        return []
    bounds = _service_column_bounds_from_rows(rows, str(hints.get("service_id_column_label") or ""))

    candidates: list[CandidateRow] = []
    current: str | None = None
    for row in rows:
        role = _layout_role(row.text)
        if role in ("header", "group_total"):
            # A `Total <id>` row re-prints an id it does not own.
            continue
        cells = _parsed_cells(row.text)
        for _role, x0, _x1, text in cells:
            if bounds and not (bounds[0] <= x0 <= bounds[1]):
                continue
            match = pattern.match(text)
            if match:
                current = (match.group(1) if match.groups() else match.group(0)).strip()
            break  # only a row's leading cell can declare its identifier

        amounts = _money_values(row.text)
        if not current or not amounts:
            continue
        if not include_zero_amount and _first_money_value(amounts) == Decimal("0"):
            continue
        candidates.append(
            CandidateRow(
                row_id=row.row_id,
                page=row.page,
                service_id=current,
                evidence=_candidate_evidence(row.text),
            )
        )
    return candidates


def _first_money_value(amounts: list[str]) -> Decimal | None:
    raw = amounts[0].replace("$", "").replace(",", "").strip()
    try:
        return Decimal(raw)
    except Exception:
        return None


def _marker_candidate_detection_enabled(structure: dict[str, Any], hints: dict[str, Any]) -> bool:
    return bool(
        marker_terms(structure.get("detail_start_marker"))
        and str(hints.get("service_id_value_pattern") or "").strip()
    )


def _labeled_service_candidate_detection_enabled(hints: dict[str, Any]) -> bool:
    service_label = str(hints.get("service_id_column_label") or "").strip()
    return bool(
        service_label
        and str(hints.get("line_item_granularity") or "").strip() == "per_service_group"
        and not str(hints.get("service_id_value_pattern") or "").strip()
    )


def _total_row_candidate_detection_enabled(hints: dict[str, Any]) -> bool:
    return bool(
        str(hints.get("line_item_granularity") or "").strip() == "per_total_group"
        and str(hints.get("service_id_preference") or "").strip()
        in {"total_row_identifier", "parent_identifier"}
    )


def _total_row_candidate_rows(
    rows: list[LayoutRowLine],
    hints: dict[str, Any],
    *,
    include_zero_amount: bool = False,
) -> list[CandidateRow]:
    candidates: list[CandidateRow] = []
    seen: set[tuple[str, str]] = set()
    same_billing_ref = str(hints.get("billing_reference_preference") or "").strip() == "same_as_service_id"
    for row in rows:
        for service_id, segment in _total_row_segments(_clean_layout_value(row.text)):
            if not _looks_like_service_total_identifier(service_id):
                continue
            amount, tax_amount, total_amount = _total_row_amount_parts(segment)
            if amount is None:
                continue
            if not include_zero_amount and amount == 0 and (total_amount is None or total_amount == 0):
                continue
            key = (row.row_id, service_id)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                CandidateRow(
                    row_id=row.row_id,
                    page=row.page,
                    service_id=service_id,
                    evidence=_candidate_evidence(segment),
                    billing_reference=service_id if same_billing_ref else None,
                    amount=amount,
                    tax_amount=tax_amount,
                    total_amount=total_amount,
                )
            )
    return candidates


def _labeled_service_candidate_rows(
    rows: list[LayoutRowLine],
    hints: dict[str, Any],
    *,
    include_zero_amount: bool = False,
) -> list[CandidateRow]:
    candidates: list[CandidateRow] = []
    seen: set[tuple[str, str]] = set()
    current_service_id: str | None = None
    current_rows: list[LayoutRowLine] = []

    def add_current() -> None:
        nonlocal current_service_id, current_rows
        if not current_service_id or not current_rows:
            current_service_id = None
            current_rows = []
            return
        anchor = _service_group_anchor(current_rows)
        if anchor is None:
            current_service_id = None
            current_rows = []
            return
        if not include_zero_amount and _marker_candidate_amount(anchor) == Decimal("0"):
            current_service_id = None
            current_rows = []
            return
        key = (anchor.row_id, current_service_id)
        if key not in seen:
            seen.add(key)
            candidates.append(
                CandidateRow(
                    row_id=anchor.row_id,
                    page=anchor.page,
                    service_id=current_service_id,
                    evidence=_candidate_evidence(" ".join(row.text for row in current_rows)),
                    billing_reference=_marker_candidate_billing_reference(current_rows, hints),
                )
            )
        current_service_id = None
        current_rows = []

    for row in rows:
        service_id = _service_id_from_labeled_row(row.text, hints)
        if service_id:
            add_current()
            current_service_id = service_id
            current_rows = [row]
            continue
        if current_service_id:
            current_rows.append(row)
    add_current()
    return candidates


def _service_group_anchor(rows: list[LayoutRowLine]) -> LayoutRowLine | None:
    for row in rows:
        if _is_total_row(row.text) and _money_values(row.text):
            return row
    for row in reversed(rows):
        if _money_values(row.text):
            return row
    return None


def _is_total_row(text: str) -> bool:
    cleaned = _clean_layout_value(text).lower()
    return bool(re.search(r"\btotal\b", cleaned))


def _marker_candidate_rows(
    rows: list[LayoutRowLine],
    structure: dict[str, Any],
    hints: dict[str, Any],
    *,
    include_zero_amount: bool = False,
) -> list[CandidateRow]:
    start_markers = marker_terms(structure.get("detail_start_marker"))
    end_markers = marker_terms(structure.get("detail_end_marker"))
    pattern = str(hints.get("service_id_value_pattern") or "").strip()
    if not start_markers or not pattern:
        return []

    candidates: list[CandidateRow] = []
    seen: set[tuple[str, str]] = set()
    current_service_id: str | None = None
    current_start: LayoutRowLine | None = None
    current_rows: list[LayoutRowLine] = []

    def add_current() -> None:
        nonlocal current_service_id, current_start, current_rows
        if not current_service_id or not current_start:
            current_service_id = None
            current_start = None
            current_rows = []
            return
        anchor = _marker_candidate_anchor(current_rows, hints) or current_start
        if not include_zero_amount and _marker_candidate_amount(anchor) == Decimal("0"):
            current_service_id = None
            current_start = None
            current_rows = []
            return
        key = (anchor.row_id, current_service_id)
        if key not in seen:
            seen.add(key)
            candidates.append(
                CandidateRow(
                    row_id=anchor.row_id,
                    page=anchor.page,
                    service_id=current_service_id,
                    evidence=_candidate_evidence(" ".join(row.text for row in current_rows)),
                    billing_reference=_marker_candidate_billing_reference(current_rows, hints),
                )
            )
        current_service_id = None
        current_start = None
        current_rows = []

    for row in rows:
        if not marker_in_text(start_markers, row.text):
            if current_start:
                current_rows.append(row)
                if end_markers and marker_in_text(end_markers, row.text):
                    add_current()
            continue
        add_current()
        service_id = _service_id_from_pattern(row.text, pattern)
        if not service_id:
            continue
        current_service_id = service_id
        current_start = row
        current_rows = [row]
        if end_markers and marker_in_text(end_markers, row.text):
            add_current()
    add_current()
    return candidates


def _marker_candidate_anchor(rows: list[LayoutRowLine], hints: dict[str, Any]) -> LayoutRowLine | None:
    amount_markers = marker_terms(hints.get("amount_column_label"))
    if amount_markers:
        for row in rows:
            if marker_in_text(amount_markers, row.text) and _money_values(row.text):
                return row
    for row in reversed(rows):
        if _money_values(row.text):
            return row
    return None


def _marker_candidate_billing_reference(rows: list[LayoutRowLine], hints: dict[str, Any]) -> str | None:
    pattern = str(hints.get("billing_reference_value_pattern") or "").strip()
    if pattern:
        for row in rows:
            value = _service_id_from_pattern(row.text, pattern)
            if value:
                return value
    amount_markers = marker_terms(hints.get("amount_column_label"))
    for row in rows:
        if amount_markers and marker_in_text(amount_markers, row.text):
            break
        text = _strip_layout_prefix(row.text)
        amount_values = _money_values(text)
        if not amount_values:
            continue
        before_amount = text.split(amount_values[0], 1)[0]
        before_amount = before_amount.split("|", 1)[0]
        candidate = _clean_layout_value(before_amount)
        if candidate and not marker_in_text(["monthly charges", "one-time charges", "savings", "total"], candidate):
            return candidate
    return None


def _strip_layout_prefix(text: str) -> str:
    text = re.sub(r"^p\d+\.r\d+\s+role=\w+\s+", "", text).strip()
    return re.sub(r"^p\d+\.r\d+\s+", "", text).strip()


def _clean_layout_value(value: Any) -> str:
    raw = str(value or "")
    text = _strip_layout_prefix(raw)
    had_layout_markup = text != raw or bool(re.search(r"\[\s*p\d+\.r\d+\.c\d+\s+[^\]]+\]", text))
    text = re.sub(r"\[\s*p\d+\.r\d+\.c\d+\s+[^\]]+\]\s*", "", text)
    if had_layout_markup:
        text = re.sub(r"\s*\|\s*", " ", text)
    return re.sub(r"\s+", " ", text).strip(" |")


def _marker_candidate_amount(row: LayoutRowLine) -> Decimal | None:
    values = _money_values(row.text)
    if not values:
        return None
    raw = values[-1].replace("$", "").replace(",", "").strip()
    try:
        return Decimal(raw)
    except Exception:
        return None


def _table_candidate_rows(rows: list[LayoutRowLine], hints: dict[str, Any]) -> list[CandidateRow]:
    candidates: list[CandidateRow] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not _looks_like_account_table_data_row(row.text):
            continue
        service_id = _candidate_table_service_id(row.text, hints)
        if not service_id:
            continue
        key = (row.row_id, service_id)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            CandidateRow(
                row_id=row.row_id,
                page=row.page,
                service_id=service_id,
                evidence=_candidate_evidence(row.text),
            )
        )
    return candidates


def _looks_like_account_table_data_row(text: str) -> bool:
    role = _layout_role(text)
    if role and role != "service_start":
        return False
    cleaned = _clean_layout_value(text).lower()
    if re.search(r"\b(account|name|number)\b.*\b(tax|charges|sub-?tot|line charges)\b", cleaned):
        return False
    if any(
        keyword in cleaned
        for keyword in (
            "goods and service tax",
            "harmonized sales tax",
            "provincial sales tax",
            "line charges",
            "crt levy",
            "subtotal",
            "totals",
        )
    ):
        return False
    return len(_money_values(text)) >= 2


def _layout_role(text: str) -> str | None:
    match = re.search(r"\brole=([A-Za-z0-9_/-]+)\b", text)
    return match.group(1) if match else None


def _table_candidate_detection_enabled(hints: dict[str, Any]) -> bool:
    labels = " ".join(str(label or "") for label in hints.get("table_column_labels") or []).lower()
    service_label = str(hints.get("service_id_column_label") or "").strip().lower()
    amount_label = str(hints.get("amount_column_label") or "").strip().lower()
    tax_label = str(hints.get("tax_amount_column_label") or "").strip().lower()
    amount_source = str(hints.get("amount_source") or "").strip().lower()
    tax_source = str(hints.get("tax_source") or "").strip().lower()
    has_table_amount_context = (
        bool(amount_label)
        or "charges" in labels
        or amount_source in {"ai_table_column", "table_charges_column"}
    )
    has_table_tax_context = (
        bool(tax_label)
        or "tax" in labels
        or "surchrg" in labels
        or tax_source in {"ai_table_column", "table_tax_column"}
    )
    return service_label in {"account", "acct"} and has_table_amount_context and has_table_tax_context


def _service_id_from_pattern(text: str, pattern: str) -> str | None:
    try:
        match = re.search(pattern, text)
    except re.error:
        return None
    if not match:
        return None
    return match.group(1 if match.lastindex else 0).strip()


def _service_id_from_labeled_row(text: str, hints: dict[str, Any]) -> str | None:
    label = str(hints.get("service_id_column_label") or "").strip()
    if not label:
        return None
    cleaned = _clean_layout_value(text)
    try:
        match = re.search(rf"{re.escape(label)}\s*:?\s*([A-Za-z0-9][A-Za-z0-9._/-]*)", cleaned, re.IGNORECASE)
    except re.error:
        return None
    return match.group(1).strip() if match else None


def _candidate_service_id(text: str, hints: dict[str, Any]) -> str | None:
    pattern = hints.get("service_id_value_pattern")
    if pattern:
        service_id = _service_id_from_pattern(text, str(pattern))
        if service_id:
            return service_id
    service_id = _service_id_from_labeled_row(text, hints)
    if service_id:
        return service_id

    # Granite branch summary rows often glue ADJUST + ACCOUNT + CHARGES into
    # text like "0.0005127710 $146.35". Capture the account immediately before
    # the row amount first, then fall back to any 8-digit leading-zero account.
    match = re.search(r"[-$,\d.]+(0\d{7})\s+\$?[-\d,]+\.\d{2}", text)
    if match:
        return match.group(1)
    match = re.search(r"\b(0\d{7})\b", text)
    return match.group(1) if match else None


def _candidate_table_service_id(text: str, hints: dict[str, Any]) -> str | None:
    pattern = hints.get("service_id_value_pattern")
    if pattern:
        service_id = _service_id_from_pattern(text, str(pattern))
        if service_id:
            return service_id

    # Granite branch rows may glue ADJUST + ACCOUNT + CHARGES together, e.g.
    # "0.0005127710 $146.35". Prefer the identifier immediately before money.
    match = re.search(r"[-$,\d.]+(\d{8})\s+\$?[-\d,]+\.\d{2}", text)
    if match:
        return match.group(1)
    match = re.search(r"\b(\d{8})\b", text)
    return match.group(1) if match else None


_TOTAL_SEGMENT_RE = re.compile(r"\btotal\s+([A-Z0-9][A-Z0-9\-/.]{4,})\b", re.I)


def _total_row_segments(text: str) -> list[tuple[str, str]]:
    matches = list(_TOTAL_SEGMENT_RE.finditer(text))
    segments: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segments.append((match.group(1).strip(), text[match.start():end].strip()))
    return segments


def _looks_like_service_total_identifier(identifier: str) -> bool:
    value = identifier.strip()
    if not re.search(r"\d", value):
        return False
    return not bool(re.fullmatch(r"\d-[A-Z0-9][A-Z0-9-]*", value, re.I))


def _total_row_amount_parts(segment: str) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    amounts = [_parse_money_value(raw) for raw in _money_values(segment)]
    amounts = [amount for amount in amounts if amount is not None]
    if not amounts:
        return None, None, None
    if len(amounts) >= 3:
        return amounts[-3], amounts[-2], amounts[-1]
    return amounts[-1], None, amounts[-1]


def _parse_money_value(raw: str) -> Decimal | None:
    cleaned = raw.replace("$", "").replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _money_values(text: str) -> list[str]:
    return re.findall(r"\$?\s*-?\d[\d,]*\.\d{2}", text)


def _candidate_evidence(text: str) -> str:
    evidence = re.sub(r"\s+", " ", text).strip()
    return evidence[:240]


def merge_compact_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_refs: set[str] = set()
    seen_values: set[tuple] = set()
    merged: list[dict[str, Any]] = []
    for row in rows:
        row_ref = str(row.get("r") or "").strip()
        # The source row id (r) is unique per printed row, so it is the primary
        # dedup key and kills overlap duplicates between adjacent chunks. Only
        # fall back to value-based dedup for rows that lack an r; otherwise two
        # legitimately distinct charges with identical values would collapse.
        if row_ref:
            if row_ref in seen_refs:
                continue
            seen_refs.add(row_ref)
        else:
            value_key = (
                str(row.get("s") or "").strip(),
                str(row.get("b") or "").strip(),
                _decimal_key(row.get("a")),
                _decimal_key(row.get("x")),
                str(row.get("p") or "").strip(),
            )
            if value_key in seen_values:
                continue
            seen_values.add(value_key)
        merged.append(row)
    indexed = list(enumerate(merged))

    def source_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int, int]:
        index, row = item
        match = re.match(r"^p(\d+)\.r(\d+)", str(row.get("r") or "").strip())
        if match:
            return (int(match.group(1)), int(match.group(2)), 0, index)
        try:
            page = int(row.get("p"))
        except (TypeError, ValueError):
            page = 10**9
        return (page, 10**9, 1, index)

    return [row for _, row in sorted(indexed, key=source_key)]


def _apply_row_context_rules(
    rows: list[dict[str, Any]],
    layout_rows: list[LayoutRowLine],
    plan,
) -> None:
    """Fill missing row fields from anchored context, including across page breaks."""
    active_by_rule: list[dict[str, str]] = [dict() for _ in plan.row_context_rules]
    context_by_ref: dict[str, dict[str, str]] = {}
    for layout_row in layout_rows:
        combined: dict[str, str] = {}
        for index, rule in enumerate(plan.row_context_rules):
            if any(re.search(pattern, layout_row.text, re.IGNORECASE) for pattern in rule.reset_patterns):
                active_by_rule[index].clear()
            try:
                match = re.search(rule.anchor_pattern, layout_row.text)
            except re.error:
                match = None
            if match:
                for field_name, group in rule.field_groups.items():
                    if group <= (match.lastindex or 0):
                        active_by_rule[index][field_name] = match.group(group).strip()
            combined.update(active_by_rule[index])
        context_by_ref[layout_row.row_id] = combined

    for row in rows:
        row_ref = _clean_row_ref(row.get("r"))
        if not row_ref:
            continue
        context = context_by_ref.get(row_ref) or {}
        for field_name, value in context.items():
            alias = FIELD_TO_ROW_ALIAS.get(field_name)
            if alias and _is_empty(row.get(alias)):
                row[alias] = value


def _target_candidates(chunk: LayoutChunk) -> list[CandidateRow]:
    return chunk.missing_candidates or chunk.candidates


def _missing_candidates(candidates: list[CandidateRow], rows: list[dict[str, Any]]) -> list[CandidateRow]:
    if not candidates:
        return []
    emitted_by_ref = {
        str(row.get("r") or "").strip(): row
        for row in rows
        if str(row.get("r") or "").strip()
    }
    emitted_services = {
        _normalize_candidate_id(row.get("s"))
        for row in rows
        if _normalize_candidate_id(row.get("s"))
    }
    missing: list[CandidateRow] = []
    for candidate in candidates:
        if _normalize_candidate_id(candidate.service_id) in emitted_services:
            continue
        emitted_ref = emitted_by_ref.get(candidate.row_id)
        if emitted_ref is not None and not _normalize_candidate_id(emitted_ref.get("s")):
            continue
        missing.append(candidate)
    return missing


def _recover_missing_total_candidate_rows(
    supplier_profile_data: dict | None,
    missing_candidates: list[CandidateRow],
) -> list[dict[str, Any]]:
    hints = ((supplier_profile_data or {}).get("advanced") or {}).get("line_item_hints") or {}
    if not (
        _total_row_candidate_detection_enabled(hints)
        or _column_service_total_candidate_detection_enabled(hints)
    ):
        return []
    rows: list[dict[str, Any]] = []
    for candidate in missing_candidates:
        if candidate.amount is None:
            continue
        row: dict[str, Any] = {
            "r": candidate.row_id,
            "s": candidate.service_id,
            "a": _compact_decimal(candidate.amount),
            "p": candidate.page,
            "c": "recurring",
        }
        if candidate.billing_reference:
            row["b"] = candidate.billing_reference
        if candidate.tax_amount is not None:
            row["x"] = _compact_decimal(candidate.tax_amount)
        rows.append(row)
    return rows


def _compact_decimal(value: Decimal) -> str:
    return format(value, "f")


def _total_candidate_reconciliation_warnings(
    candidates: list[CandidateRow],
    compact_header: dict[str, Any],
    rows: list[LayoutRowLine],
    supplier_profile_data: dict | None,
) -> list[str]:
    hints = ((supplier_profile_data or {}).get("advanced") or {}).get("line_item_hints") or {}
    if not _total_row_candidate_detection_enabled(hints):
        return []
    candidate_total = sum(
        (candidate.total_amount or candidate.amount or Decimal("0"))
        for candidate in candidates
    )
    if candidate_total == 0:
        return []
    account_total = _account_total_from_total_rows(rows, compact_header.get("ac"))
    if account_total is None:
        return []
    if candidate_total.quantize(Decimal("0.01")) == account_total.quantize(Decimal("0.01")):
        return []
    return [
        "Total-row candidate reconciliation mismatch: "
        f"service total candidates sum to {candidate_total:.2f}, "
        f"but the printed account total is {account_total:.2f}."
    ]


def _account_total_from_total_rows(rows: list[LayoutRowLine], account_number: Any) -> Decimal | None:
    normalized_account = _normalize_candidate_id(account_number)
    for row in rows:
        for identifier, segment in _total_row_segments(_clean_layout_value(row.text)):
            normalized_id = _normalize_candidate_id(identifier)
            if not (
                normalized_account and normalized_id == normalized_account
                or re.fullmatch(r"\d-[A-Z0-9][A-Z0-9-]*", identifier.strip(), re.I)
            ):
                continue
            _amount, _tax_amount, total_amount = _total_row_amount_parts(segment)
            if total_amount is not None:
                return total_amount
    return None


def _apply_candidate_defaults(candidates: list[CandidateRow], rows: list[dict[str, Any]]) -> None:
    if not candidates or not rows:
        return
    by_service = {
        _normalize_candidate_id(candidate.service_id): candidate
        for candidate in candidates
        if candidate.billing_reference
    }
    by_ref = {
        str(candidate.row_id or "").strip(): candidate
        for candidate in candidates
        if str(candidate.row_id or "").strip()
    }
    for row in rows:
        candidate = by_ref.get(str(row.get("r") or "").strip())
        if candidate is None:
            candidate = by_service.get(_normalize_candidate_id(row.get("s")))
        if _is_empty(row.get("s")) and candidate:
            row["s"] = candidate.service_id
        if not _is_empty(row.get("b")):
            row["b"] = _clean_layout_value(row.get("b"))
            continue
        if candidate and candidate.billing_reference:
            row["b"] = _clean_layout_value(candidate.billing_reference)


def _line_item_defaults_from_context(
    rows: list[LayoutRowLine],
    supplier_profile_data: dict | None,
    field_schema,
    *,
    excluded_row_ids: set[str] | None = None,
) -> dict[str, str]:
    labels = _line_item_default_labels(supplier_profile_data, field_schema)
    excluded = excluded_row_ids or set()
    context_rows = [row for row in rows if row.row_id not in excluded]
    defaults: dict[str, str] = {}
    for field_name, field_labels in labels.items():
        value = _first_labeled_context_value(context_rows, field_labels)
        if value:
            defaults[field_name] = value
    return defaults


def _line_item_default_labels(supplier_profile_data: dict | None, field_schema) -> dict[str, list[str]]:
    hints = ((supplier_profile_data or {}).get("advanced") or {}).get("line_item_hints") or {}
    labels = {
        "service_id": _split_label_tokens(hints.get("service_id_column_label")),
        "billing_reference": _split_label_tokens(hints.get("billing_reference_column_label")),
    }
    schema_labels = _row_field_label_hints(field_schema, {"service_id", "billing_reference"})
    for field_name, fallback_labels in schema_labels.items():
        existing = labels.setdefault(field_name, [])
        for label in fallback_labels:
            if label.lower() not in {value.lower() for value in existing}:
                existing.append(label)
    return {field: field_labels for field, field_labels in labels.items() if field_labels}


def _row_field_label_hints(field_schema, fields: set[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for schema_field in getattr(field_schema, "fields", []) or []:
        name = getattr(schema_field, "name", None)
        if name not in fields:
            continue
        label_hint = getattr(schema_field, "label_hint", None)
        labels = _split_label_tokens(label_hint)
        if labels:
            out[name] = labels
    return out


def _split_label_tokens(value: Any) -> list[str]:
    tokens: list[str] = []
    for token in str(value or "").split(","):
        label = token.strip()
        if label and label.lower() not in {existing.lower() for existing in tokens}:
            tokens.append(label)
    return tokens


def _first_labeled_context_value(rows: list[LayoutRowLine], labels: list[str]) -> str | None:
    for row in rows:
        value = _labeled_context_value(row.text, labels)
        if value:
            return value
    return None


def _labeled_context_value(text: str, labels: list[str]) -> str | None:
    cleaned = _clean_layout_value(text)
    for label in labels:
        pattern = rf"(?:^|\b){re.escape(label)}\b\s*:?\s*(.+)$"
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if not match:
            continue
        value = _clean_context_default_value(match.group(1), labels)
        if value:
            return value
    return None


def _clean_context_default_value(value: Any, labels: list[str]) -> str | None:
    text = _clean_layout_value(value)
    text = re.sub(r"^\s*[:=-]\s*", "", text).strip()
    for label in labels:
        stop = re.search(rf"\s+\b{re.escape(label)}\b\s*:?", text, re.IGNORECASE)
        if stop:
            text = text[:stop.start()].strip()
    text = text.strip(" :;|")
    return text or None


def _clean_row_ref(value: Any) -> str | None:
    """The AI echoes a printed row id such as ``p5.r12``; keep only that shape."""
    match = re.match(r"^\s*(p\d+\.r\d+)", str(value or ""))
    return match.group(1) if match else None


def _normalize_candidate_id(value: Any) -> str:
    return str(value or "").strip()


def _completeness_warnings(
    candidates: list[CandidateRow],
    rows: list[dict[str, Any]],
    missing_candidates: list[CandidateRow],
) -> list[str]:
    if not missing_candidates:
        return []
    examples = ", ".join(candidate.service_id for candidate in missing_candidates[:10])
    suffix = f" Missing examples: {examples}" if examples else ""
    return [
        (
            "Possible missing line items: "
            f"detected {len(candidates)} candidate rows, extracted {len(rows)}. "
            f"Missing {len(missing_candidates)} candidate row(s).{suffix}"
        )
    ]


def compact_to_legacy_raw(
    compact_payload: dict[str, Any],
    *,
    supplier_name: str | None = None,
    output_type: str = "standard",
    currency_rules: Any = None,
    line_item_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from stencil.fields.currency import normalize_currency_code

    compact_header = compact_payload.get("h") or {}
    line_item_defaults = compact_payload.get("line_item_defaults") or {}
    billing_reference_preference = str((line_item_hints or {}).get("billing_reference_preference") or "").strip()
    header: dict[str, Any] = {}
    raw: dict[str, Any] = {
        "header": header,
        "line_items": [],
        # Placeholder only. The orchestrator overwrites this with a measured
        # completeness ratio once it knows how many detected candidate rows
        # actually survived extraction -- see extract_invoice_compact_chunked.
        "overall_confidence": 1.0,
    }

    def add_warning(message: str | None) -> None:
        if not message:
            return
        if message in raw.get("warnings", []):
            return
        raw["warnings"] = [*raw.get("warnings", []), message]

    for alias, canonical_field in DOC_ALIAS_TO_FIELD.items():
        if alias not in compact_header:
            continue
        value = compact_header.get(alias)
        if _is_empty(value):
            continue
        if canonical_field == "currency":
            result = normalize_currency_code(value, rules=currency_rules)
            add_warning(result.warning)
            value = result.code
            if _is_empty(value):
                continue
        if canonical_field in TOTAL_FIELDS:
            raw[canonical_field] = value
        else:
            header[canonical_field] = value

    if supplier_name and not header.get("supplier_name"):
        header["supplier_name"] = supplier_name
    if not header.get("currency"):
        header["currency"] = normalize_currency_code(None, rules=currency_rules, fallback="USD").code or "USD"

    currency = normalize_currency_code(header.get("currency"), rules=currency_rules, fallback="USD").code or "USD"
    for line_number, compact_row in enumerate(compact_payload.get("rows") or [], start=1):
        row: dict[str, Any] = {
            "line_number": line_number,
            "description": "",
            "charge_type": "unknown",
            "currency": currency,
            "confidence": 1.0,
            # Keep the printed row this line came from, so deterministic repairs
            # (e.g. child service-id carry-forward) can join back to the layout.
            "source_row_id": _clean_row_ref(compact_row.get("r")),
        }
        for alias, canonical_field in ROW_ALIAS_TO_FIELD.items():
            if canonical_field == "_row_ref" or alias not in compact_row:
                continue
            value = compact_row.get(alias)
            if _is_empty(value):
                continue
            if canonical_field == "currency":
                result = normalize_currency_code(value, rules=currency_rules, fallback=currency)
                add_warning(result.warning)
                value = result.code
                if _is_empty(value):
                    continue
            row[canonical_field] = value
        if _is_empty(row.get("service_id")) and not _is_empty(line_item_defaults.get("service_id")):
            row["service_id"] = _clean_layout_value(line_item_defaults.get("service_id"))
        if _is_empty(row.get("billing_reference")) and not _is_empty(line_item_defaults.get("billing_reference")):
            row["billing_reference"] = _clean_layout_value(line_item_defaults.get("billing_reference"))
        if (
            _is_empty(row.get("billing_reference"))
            and billing_reference_preference == "same_as_service_id"
            and not _is_empty(row.get("service_id"))
        ):
            row["billing_reference"] = row["service_id"]
        raw["line_items"].append(row)

    raw["output_type"] = output_type
    return raw


def _extract_chunk_rows(
    client,
    chunk: LayoutChunk,
    *,
    output_type: str,
    supplier_profile_data: dict | None,
    field_schema,
    row_fields: set[str],
    calls: list[CompactCall],
    artifact_dir: Path | None,
    chunk_artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prompt = _build_chunk_prompt(
        chunk,
        output_type=output_type,
        supplier_profile_data=supplier_profile_data,
        field_schema=field_schema,
        row_fields=row_fields,
    )
    try:
        data = _call_json_schema(
            client,
            call_type="ai_line_items_chunk",
            prompt=prompt,
            schema=build_compact_line_item_schema(row_fields),
            details={
                "chunk_id": chunk.chunk_id,
                "retry_depth": chunk.retry_depth,
                "strict_retry": chunk.strict_retry,
                "layout_rows": len(chunk.rows),
                "layout_chars": chunk.char_count,
                "candidate_rows": len(_target_candidates(chunk)),
            },
            calls=calls,
        )
    except AIJSONError:
        if chunk.retry_depth >= int(runtime_settings.runtime_value("ai_chunk_max_retries")) or len(chunk.rows) <= 1:
            raise
        left, right = _split_chunk(chunk)
        rows: list[dict[str, Any]] = []
        for child in (left, right):
            rows.extend(
                _extract_chunk_rows(
                    client,
                    child,
                    output_type=output_type,
                    supplier_profile_data=supplier_profile_data,
                    field_schema=field_schema,
                    row_fields=row_fields,
                    calls=calls,
                    artifact_dir=artifact_dir,
                    chunk_artifacts=chunk_artifacts,
                )
            )
        return rows

    rows = data.get("rows") or []
    target_candidates = _target_candidates(chunk)
    _apply_candidate_defaults(target_candidates, rows)
    missing_candidates = _missing_candidates(target_candidates, rows)
    artifact = {
        "chunk_id": chunk.chunk_id,
        "retry_depth": chunk.retry_depth,
        "strict_retry": chunk.strict_retry,
        "layout_rows": len(chunk.rows),
        "layout_chars": chunk.char_count,
        "candidate_rows": len(target_candidates),
        "missing_candidate_count": len(missing_candidates),
        "missing_candidate_examples": [candidate.service_id for candidate in missing_candidates[:20]],
        "compact_rows": len(rows),
    }
    chunk_artifacts.append(artifact)
    _write_json(artifact_dir, f"chunk_{chunk.chunk_id}.compact.json", {**artifact, "rows": rows})

    if missing_candidates:
        if not chunk.strict_retry:
            retry_chunk = LayoutChunk(
                chunk_id=f"{chunk.chunk_id}r",
                rows=chunk.rows,
                retry_depth=chunk.retry_depth,
                candidates=chunk.candidates,
                missing_candidates=missing_candidates,
                strict_retry=True,
            )
            retry_rows = _extract_chunk_rows(
                client,
                retry_chunk,
                output_type=output_type,
                supplier_profile_data=supplier_profile_data,
                field_schema=field_schema,
                row_fields=row_fields,
                calls=calls,
                artifact_dir=artifact_dir,
                chunk_artifacts=chunk_artifacts,
            )
            return merge_compact_rows([*rows, *retry_rows])

        if chunk.retry_depth < int(runtime_settings.runtime_value("ai_chunk_max_retries")) and len(chunk.rows) > 1:
            split_chunk = LayoutChunk(
                chunk_id=chunk.chunk_id,
                rows=chunk.rows,
                retry_depth=chunk.retry_depth,
                candidates=chunk.candidates,
                missing_candidates=missing_candidates,
                strict_retry=chunk.strict_retry,
            )
            split_rows: list[dict[str, Any]] = []
            for child in _split_chunk(split_chunk):
                if not _target_candidates(child):
                    continue
                split_rows.extend(
                    _extract_chunk_rows(
                        client,
                        child,
                        output_type=output_type,
                        supplier_profile_data=supplier_profile_data,
                        field_schema=field_schema,
                        row_fields=row_fields,
                        calls=calls,
                        artifact_dir=artifact_dir,
                        chunk_artifacts=chunk_artifacts,
                    )
                )
            return merge_compact_rows([*rows, *split_rows])
    return rows


def _call_json_schema(
    client,
    *,
    call_type: str,
    prompt: str,
    schema: dict[str, Any],
    details: dict[str, Any],
    calls: list[CompactCall],
) -> dict[str, Any]:
    model_name = runtime_settings.openai_model_extraction()
    started = time.time()
    response = None
    try:
        response = traced_chat_completion(
            client,
            call_type=call_type,
            context=details.get("chunk_id"),
            model=model_name,
            messages=[
                {"role": "system", "content": _COMPACT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_schema", "json_schema": schema},
            max_completion_tokens=runtime_settings.openai_max_output_tokens(),
            timeout=runtime_settings.openai_timeout(),
        )
        data = completion_to_json(response, max_output_tokens=runtime_settings.openai_max_output_tokens())
    except Exception as exc:
        usage = getattr(response, "usage", None)
        calls.append(
            CompactCall(
                call_type=call_type,
                ai_model_name=model_name,
                tokens_input=getattr(usage, "prompt_tokens", 0) if usage else 0,
                tokens_output=getattr(usage, "completion_tokens", 0) if usage else 0,
                duration_ms=int((time.time() - started) * 1000),
                status="error",
                error_message=str(exc),
                details={**details, "truncated": isinstance(exc, TruncatedAIJSONError)},
            )
        )
        raise

    usage = getattr(response, "usage", None)
    calls.append(
        CompactCall(
            call_type=call_type,
            ai_model_name=model_name,
            tokens_input=getattr(usage, "prompt_tokens", 0) if usage else 0,
            tokens_output=getattr(usage, "completion_tokens", 0) if usage else 0,
            duration_ms=int((time.time() - started) * 1000),
            details={**details, "output_items": len(data.get("rows") or [])},
        )
    )
    return data


# Static instruction blocks for the compact extraction prompts. Kept as named
# constants (rather than inline literals) so the Evals report can render the prompt
# as a template with explicit variable slots — see extraction_prompt_template().
SCALAR_INTRO = "Extract only compact invoice scalar fields from the provided layout rows."
SCALAR_NULL_RULE = "Use null for unavailable fields. Do not infer values that are not printed."
CHUNK_INTRO = "Extract only compact delivered output line items from this bounded layout chunk."
CHUNK_RULES: list[str] = [
    "Return only rows that should appear in the delivered output workbook.",
    "Do not emit MRC, fee, surcharge, tax, subtotal, total, header, footer, duplicate, or noise rows "
    "as separate rows when the profile says the delivered output is grouped.",
    "For grouped output, emit one row per logical service/group. Put the selected group/current-charge/"
    "subtotal amount in row.a and put grouped tax in row.x only when the source prints per-group tax.",
    "Classify row.c as recurring, one_time, tax, fee, credit, adjustment, surcharge, usage, or unknown. "
    "Use recurring for grouped service output rows unless another type is clearly printed.",
    "For each emitted row, set row.r to the best source row id for the delivered amount row, exactly as "
    "printed (for example p3.r12).",
    "Use null for unavailable fields. Amount fields must be numeric with no currency symbols.",
]

DELIVERED_ROW_POLICY_TEMPLATE = """\
--- DELIVERED ROW POLICY ---
The expected output is a delivered workbook, not a full charge-detail reconstruction.
- per_charge_row: emit each billable printed charge row.
- per_service_group: emit one row per service block using the service/current-charge/group total amount.
- per_total_group: emit one row per group ended by a total row using that total row's amount.
Honor this profile's line_item_granularity before any generic line-item wording.
"""


def _build_scalar_prompt(
    *,
    supplier_name: str | None,
    output_type: str,
    supplier_profile_data: dict | None,
    field_schema,
    doc_fields: set[str],
    rows: list[LayoutRowLine],
) -> str:
    aliases = _doc_aliases(doc_fields)
    parts = [
        SCALAR_INTRO,
        f"Supplier: {supplier_name or 'Unknown'}",
        f"Output type: {output_type}",
        SCALAR_NULL_RULE,
        "Compact header aliases:",
    ]
    parts.extend(f"- h.{alias}: {field}" for alias, field in aliases.items())
    parts.append(_profile_context(supplier_profile_data, field_schema))
    parts.append("\n--- LAYOUT CONTEXT ---")
    parts.append(_render_prompt_rows(rows))
    return "\n".join(part for part in parts if part)


def _build_chunk_prompt(
    chunk: LayoutChunk,
    *,
    output_type: str,
    supplier_profile_data: dict | None,
    field_schema,
    row_fields: set[str],
) -> str:
    aliases = _row_aliases(row_fields)
    parts = [
        CHUNK_INTRO,
        f"Output type: {output_type}",
        *CHUNK_RULES,
        "Compact row aliases:",
    ]
    parts.extend(f"- row.{alias}: {field}" for alias, field in aliases.items())
    parts.append(_candidate_prompt_context(chunk))
    parts.append(_profile_context(supplier_profile_data, field_schema, include_delivered_policy=True))
    parts.append(f"\n--- CHUNK {chunk.chunk_id} LAYOUT ROWS ---")
    parts.append(_render_prompt_rows(chunk.rows))
    return "\n".join(part for part in parts if part)


def _candidate_prompt_context(chunk: LayoutChunk) -> str:
    candidates = _target_candidates(chunk)
    if not candidates:
        return ""
    lines = [
        "\n--- CANDIDATE ROW ACCOUNTABILITY ---",
        f"This chunk contains {len(candidates)} candidate delivered row(s) detected from the layout.",
        "Emit one compact row for each candidate unless the row is clearly invalid.",
        "Do not skip middle rows in continuous tables.",
        "Each listed candidate service_id is a distinct delivered service; do not treat continuation-page "
        "services as duplicates just because their blocks look similar.",
    ]
    if chunk.strict_retry:
        lines.append("Previous extraction missed these candidate rows. Focus on returning them now.")
    for candidate in candidates[:80]:
        values = []
        if candidate.amount is not None:
            values.append(f"amount={candidate.amount}")
        if candidate.tax_amount is not None:
            values.append(f"tax_amount={candidate.tax_amount}")
        if candidate.total_amount is not None:
            values.append(f"total_amount={candidate.total_amount}")
        value_suffix = ("; " + "; ".join(values)) if values else ""
        lines.append(
            f"- {candidate.row_id} page {candidate.page}: service_id={candidate.service_id}; "
            f"evidence={candidate.evidence}{value_suffix}"
        )
    if len(candidates) > 80:
        lines.append(f"- ... {len(candidates) - 80} more candidate row(s) in this chunk")
    return "\n".join(lines)


def _profile_context(
    supplier_profile_data: dict | None,
    field_schema,
    *,
    include_delivered_policy: bool = False,
) -> str:
    if not supplier_profile_data:
        return ""
    parts: list[str] = []
    if include_delivered_policy:
        parts.append(_delivered_row_policy(supplier_profile_data))
    # Reuse the existing profile/field hint wording so compact mode honors the
    # same advanced tab configuration as legacy AI extraction.
    parts.append(
        "\n--- PROFILE HINTS ---\n"
        + build_extraction_user_prompt(
            supplier_profile=supplier_profile_data,
            supplier_name=supplier_profile_data.get("identity", {}).get("canonical_name"),
            output_type=supplier_profile_data.get("classification", {}).get("output_type", "standard"),
            field_schema=field_schema,
        )
    )
    return "\n".join(part for part in parts if part)


def _delivered_row_policy(supplier_profile_data: dict | None) -> str:
    advanced = ((supplier_profile_data or {}).get("advanced") or {})
    hints = advanced.get("line_item_hints") or {}
    lines = [DELIVERED_ROW_POLICY_TEMPLATE.rstrip()]
    granularity = hints.get("line_item_granularity")
    if granularity:
        lines.append(f"- This profile's line_item_granularity is {granularity!r}.")
    else:
        lines.append(
            "- This profile does not declare line_item_granularity; emit rows at the source's "
            "delivered service level."
        )
    policy_by_granularity = {
        "per_charge_row": (
            "Emit each billable printed charge row; component fees/taxes are separate only when "
            "they are delivered rows."
        ),
        "per_service_group": "Emit one row per service block; use service/current-charge/group total rows for row.a.",
        "per_total_group": "Emit one row per total group; use the closing total row for row.a.",
    }
    if granularity in policy_by_granularity:
        lines.append(f"- Required behavior: {policy_by_granularity[granularity]}")
    if service_col := hints.get("service_id_column_label"):
        lines.append(f"- service_id source: column/field labeled {service_col!r}.")
    elif service_pref := hints.get("service_id_preference"):
        lines.append(f"- service_id preference: {service_pref}.")
    if billing_col := hints.get("billing_reference_column_label"):
        lines.append(f"- billing_reference source: column/field labeled {billing_col!r}.")
    elif billing_pref := hints.get("billing_reference_preference"):
        lines.append(f"- billing_reference preference: {billing_pref}.")
    if amount_col := hints.get("amount_column_label"):
        lines.append(f"- amount source: column/field/row labeled {amount_col!r}.")
    if tax_col := hints.get("tax_amount_column_label"):
        lines.append(f"- tax_amount source: column/field/row labeled {tax_col!r}; otherwise use null.")
    if amount_source := hints.get("amount_source"):
        lines.append(f"- executable amount policy: {amount_source}.")
    if tax_source := hints.get("tax_source"):
        lines.append(f"- executable tax policy: {tax_source}.")
    tax_mode = hints.get("tax_output_mode") or "extract_exact"
    lines.append(f"- delivered EXT_TAX policy: {tax_mode}.")
    if tax_mode == "calculate":
        lines.append(f"- tax rate source for calculated EXT_TAX: {hints.get('tax_rate_source') or 'auto'}.")
    if service_pattern := hints.get("service_id_value_pattern"):
        lines.append(f"- service_id normalization regex: {service_pattern!r}.")
    if billing_pattern := hints.get("billing_reference_value_pattern"):
        lines.append(f"- billing_reference normalization regex: {billing_pattern!r}.")
    return "\n".join(lines)


def extraction_prompt_template() -> dict:
    """Render the extraction prompt as a template with explicit variable slots.

    Prompts stay in code (the versioned source of truth); this exposes the same
    static text with the invoice-specific parts shown as labeled ``{{placeholders}}``
    so the Evals report can separate the *prompt* from its *variables*
    (profile.json, field_schema, layout). Reflects the active extraction mode.
    """
    from stencil import runtime_settings

    mode = runtime_settings.runtime_value("ai_extraction_mode")
    scalar_user = "\n".join([
        SCALAR_INTRO,
        "Supplier: {{SUPPLIER_NAME}}",
        "Output type: {{OUTPUT_TYPE}}",
        SCALAR_NULL_RULE,
        "Compact header aliases:",
        "{{HEADER_ALIASES}}",
        "{{PROFILE_HINTS}}",
        "\n--- LAYOUT CONTEXT ---",
        "{{LAYOUT_ROWS}}",
    ])
    chunk_user = "\n".join([
        CHUNK_INTRO,
        "Output type: {{OUTPUT_TYPE}}",
        *CHUNK_RULES,
        "Compact row aliases:",
        "{{ROW_ALIASES}}",
        "{{DELIVERED_ROW_POLICY}}",
        "{{PROFILE_HINTS}}",
        "\n--- CHUNK {{CHUNK_ID}} LAYOUT ROWS ---",
        "{{LAYOUT_ROWS}}",
    ])
    return {
        "extraction_mode": mode,
        "system": _COMPACT_SYSTEM_PROMPT,
        "calls": [
            {"call_type": "ai_scalars", "user_template": scalar_user},
            {"call_type": "ai_line_items_chunk", "user_template": chunk_user},
        ],
        "variables": {
            "SUPPLIER_NAME": "profile identity.canonical_name",
            "OUTPUT_TYPE": "profile classification.output_type",
            "HEADER_ALIASES": "compact doc-field aliases (h.*) selected from the field schema + output spec",
            "ROW_ALIASES": "compact row-field aliases (row.*) selected from the field schema + output spec",
            "DELIVERED_ROW_POLICY": "structured delivered-row policy derived from profile advanced.line_item_hints",
            "PROFILE_HINTS": (
                "Compiled instruction block (extraction/instructions.py): explicit precedence, "
                "structured directives from profile.json advanced document_structure / "
                "line_item_hints / currency / field labels, any detected notes-vs-field conflicts, "
                "and the free-text notes"
            ),
            "LAYOUT_ROWS": "coordinate-aware layout rows for the page/chunk (the invoice text)",
            "CHUNK_ID": "id of the bounded layout chunk",
        },
        "note": (
            ""
            if mode == "compact_chunked"
            else f"Active extraction mode is '{mode}'; this template describes the compact_chunked prompts."
        ),
    }


def _fields_needed_for_compact_output(profile: SupplierProfile | None) -> tuple[set[str], set[str]]:
    doc_fields = set(DEFAULT_DOC_FIELDS)
    row_fields = set(DEFAULT_ROW_FIELDS)
    if profile is None:
        return doc_fields, row_fields

    spec = resolve_output_spec(profile)
    for column in output_spec_to_columns(spec):
        for path in (column.canonical_path, column.fallback_path):
            if not path:
                continue
            if path.startswith("line_item."):
                field = path.removeprefix("line_item.")
                if field in FIELD_TO_ROW_ALIAS:
                    row_fields.add(field)
            elif path.startswith("header."):
                field = path.removeprefix("header.")
                if field in FIELD_TO_DOC_ALIAS:
                    doc_fields.add(field)
            elif path in FIELD_TO_DOC_ALIAS:
                doc_fields.add(path)
            elif path == "computed.line_tax":
                row_fields.update({"amount", "tax_amount"})
                doc_fields.update({"tax_rate", "tax", "subtotal"})
    return doc_fields, row_fields


def _layout_rows(document: LayoutDocument) -> list[LayoutRowLine]:
    rows: list[LayoutRowLine] = []
    for page in document.pages:
        for row in page.visual_rows:
            rows.append(LayoutRowLine(page=page.page_number, row_id=row.row_id, text=_render_row(row)))
    return rows


def _render_row(row: VisualRow) -> str:
    cell_parts = []
    for cell in row.cells:
        span_box = cell.normalized_bbox or cell.bbox
        span = f"{span_box.x0:.0f}-{span_box.x1:.0f}"
        cell_id = cell.cell_id or f"p{row.page}.r{row.row_index}.c{cell.column_index}"
        cell_parts.append(f"[{cell_id} c{cell.column_index} {cell.role} x={span}] {cell.text}")
    row_id = row.row_id or f"p{row.page}.r{row.row_index}"
    return f"{row_id} role={row.row_role} " + (" | ".join(cell_parts) if cell_parts else row.text)


def _detail_region_rows(rows: list[LayoutRowLine], supplier_profile_data: dict | None) -> list[LayoutRowLine]:
    advanced = ((supplier_profile_data or {}).get("advanced") or {})
    structure = advanced.get("document_structure") or {}
    line_hints = advanced.get("line_item_hints") or {}
    start_markers = marker_terms(structure.get("detail_start_marker"))
    end_markers = marker_terms(structure.get("detail_end_marker"))
    if not start_markers and not end_markers:
        return rows

    include_end_marker = line_hints.get("line_item_granularity") in {"per_total_group", "per_service_group"}
    in_region = not start_markers
    selected: list[LayoutRowLine] = []
    for row in rows:
        # A (possibly repeated) start marker opens/re-opens the detail region.
        # Per-service layouts (e.g. Rogers wireless) repeat the start/end markers
        # once per service, so an end marker must CLOSE the current region and keep
        # scanning for the next one — not stop the scan after the first service.
        if start_markers and marker_in_text(start_markers, row.text):
            in_region = True
            selected.append(row)
            continue
        if in_region and end_markers and marker_in_text(end_markers, row.text):
            if include_end_marker:
                selected.append(row)
            in_region = False
            continue
        if in_region:
            selected.append(row)
    return selected


def _scalar_context_rows(
    rows: list[LayoutRowLine],
    supplier_profile_data: dict | None,
    context_pages: set[int] | None = None,
) -> list[LayoutRowLine]:
    """Rows shown to the scalar (document-field) call.

    ``context_pages`` comes from deterministic page classification and names the
    pages that structurally carry document-level fields. Without it this falls
    back to "the first five pages and the last two", which finds the header only
    when the header happens to be near the front.
    """
    if not rows:
        return []
    structure = ((supplier_profile_data or {}).get("advanced") or {}).get("document_structure") or {}
    markers = [
        *marker_terms(structure.get("detail_start_marker")),
        *marker_terms(structure.get("detail_end_marker")),
    ]
    last_page = max(row.page for row in rows)
    selected: list[LayoutRowLine] = []
    seen: set[str] = set()
    for row in rows:
        marker_hit = marker_in_text(markers, row.text)
        if context_pages:
            in_context = row.page in context_pages
        else:
            in_context = row.page <= 5 or row.page >= max(1, last_page - 1)
        if in_context or marker_hit:
            if row.row_id not in seen:
                selected.append(row)
                seen.add(row.row_id)

    max_chars = max(4000, int(runtime_settings.runtime_value("ai_chunk_max_layout_chars")))
    trimmed: list[LayoutRowLine] = []
    chars = 0
    for row in selected:
        row_chars = len(row.text) + 1
        if trimmed and chars + row_chars > max_chars:
            break
        trimmed.append(row)
        chars += row_chars
    return trimmed


def _render_prompt_rows(rows: list[LayoutRowLine]) -> str:
    lines: list[str] = []
    current_page: int | None = None
    for row in rows:
        if row.page != current_page:
            current_page = row.page
            lines.append(f"=== PAGE {row.page} ===")
        lines.append(row.text)
    return "\n".join(lines)


def _split_chunk(chunk: LayoutChunk) -> tuple[LayoutChunk, LayoutChunk]:
    midpoint = max(1, len(chunk.rows) // 2)
    left_rows = chunk.rows[:midpoint]
    right_rows = chunk.rows[midpoint:]
    left_ids = {row.row_id for row in left_rows}
    right_ids = {row.row_id for row in right_rows}
    candidates = chunk.missing_candidates or chunk.candidates
    return (
        LayoutChunk(
            chunk_id=f"{chunk.chunk_id}a",
            rows=left_rows,
            retry_depth=chunk.retry_depth + 1,
            candidates=[candidate for candidate in candidates if candidate.row_id in left_ids],
        ),
        LayoutChunk(
            chunk_id=f"{chunk.chunk_id}b",
            rows=right_rows,
            retry_depth=chunk.retry_depth + 1,
            candidates=[candidate for candidate in candidates if candidate.row_id in right_ids],
        ),
    )


def _strict_object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _doc_aliases(fields: set[str]) -> dict[str, str]:
    return {alias: field for alias, field in DOC_ALIAS_TO_FIELD.items() if field in fields}


def _row_aliases(fields: set[str]) -> dict[str, str]:
    wanted = {"_row_ref", *fields, "charge_type", "source_page"}
    return {alias: field for alias, field in ROW_ALIAS_TO_FIELD.items() if field in wanted}


def _schema_for_doc_field(field: str) -> dict[str, Any]:
    if field in {"subtotal", "tax", "fees", "current_charges", "total_due", "tax_rate"}:
        return {"type": ["number", "null"]}
    return {"type": ["string", "null"]}


def _schema_for_row_field(field: str) -> dict[str, Any]:
    if field == "_row_ref":
        return {"type": "string", "pattern": r"^p\d+\.r\d+(?:\D.*)?$"}
    if field == "source_page":
        return {"type": ["integer", "null"]}
    if field == "charge_type":
        return {
            "type": ["string", "null"],
            "enum": [
                "recurring",
                "one_time",
                "tax",
                "fee",
                "credit",
                "adjustment",
                "surcharge",
                "usage",
                "unknown",
                None,
            ],
        }
    if field in {"amount", "tax_amount", "quantity", "unit_rate", "plan_cost", "equipment_cost"}:
        return {"type": ["number", "null"]}
    return {"type": ["string", "null"]}


def _prepare_artifact_dir(artifact_dir: Path | None) -> Path | None:
    if artifact_dir is None:
        return None
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return artifact_dir


def _write_json(artifact_dir: Path | None, filename: str, payload: dict[str, Any]) -> None:
    if artifact_dir is None:
        return
    (artifact_dir / filename).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _pdf_page_count(pdf_path: Path) -> int:
    from stencil.extraction.layout import pdf_page_count

    return pdf_page_count(pdf_path)


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _decimal_key(value: Any) -> str:
    if _is_empty(value):
        return ""
    try:
        return str(Decimal(str(value)).normalize())
    except Exception:
        return str(value).strip()


_COMPACT_SYSTEM_PROMPT = """\
You are a precise invoice data extractor. Respond only with JSON matching the supplied schema.
The JSON uses compact aliases to minimize output tokens. Do not include explanations.
Use exact values from coordinate-aware rows. Use null when a field is unavailable.
"""
