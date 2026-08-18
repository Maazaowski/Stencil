"""AI-assisted SupplierProfile authoring.

This is the profile-level analogue of ``stencil.models.authoring`` (which
authors the declarative *ExtractionModel*). Here the AI authors the upstream
*SupplierProfile* config — identity, field label hints, the deterministic
grouped-line-item policy, fingerprint tuning, and notes — from a handful of
sample invoices plus the user's chat guidance.

It makes ONE AI call per invocation and returns a draft profile (the authorable
subset) plus a natural-language assistant message. The interactive loop
(upload → chat → preview → refine) is orchestrated by the Celery task in
``stencil.tasks.worker`` and the API in ``stencil.api.profile_authoring``.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

import structlog

from stencil import runtime_settings
from stencil.ai_debug import traced_chat_completion
from stencil.config import settings
from stencil.fields.schema import FieldSchema
from stencil.llm import get_ai_client
from stencil.output.spec import OutputSpec

logger = structlog.get_logger()

# Allowed enum values for the authorable line-item policy. Kept in lockstep with
# extraction/normalization.py (GROUPING_GRANULARITIES / GROUPING_ID_PREFS) and the
# LineItemHints field docstrings so the model cannot invent unsupported values.
_GRANULARITIES = ["per_charge_row", "per_service_group", "per_total_group"]
_SERVICE_ID_PREFS = [
    "first_identifier", "parent_identifier", "total_row_identifier", "leftmost_identifier",
    "child_identifier",
]
_BILLING_REF_PREFS = ["same_as_service_id", "parent_identifier", "separate_column", "none"]
_AMOUNT_SOURCES = ["group_total", "first_amount", "label_amount", "label_amount_minus_tax", "table_charges_column"]
_TAX_SOURCES = ["none", "label_amount", "group_total_minus_amount", "table_tax_column"]
_TAX_OUTPUT_MODES = ["auto", "extract_exact", "calculate", "none"]
_TAX_RATE_SOURCES = ["auto", "invoice_tax_rate", "invoice_tax_divided_by_subtotal", "consistent_line_tax_rate"]
_COMPUTED_OUTPUT_SOURCES = ["computed.line_tax"]
_OUTPUT_TRANSFORMS = ["digits_only", "trim", "uppercase", "lowercase"]

PROFILE_AUTHORING_SYSTEM_PROMPT = """You are an extraction-onboarding assistant for Stencil.

You configure a SupplierProfile: a small JSON config that tells the system how to
read ONE supplier's invoice layout and deliver the customer's required columns.
You are given, for each sample invoice: the coordinate-aware PDF text and a
baseline AI extraction. The baseline is evidence, not guaranteed ground truth.
You may also be given the user's guidance, an XLS/XLSX blueprint per invoice,
and the diff between the current draft's preview and that blueprint.

Your job each turn:
1. Read the layouts and the user's guidance.
2. Build an internal extraction plan before choosing profile fields. The plan
   must decide supplier/document type, requested output layout, header labels,
   invoice totals, line-item section boundaries, row granularity, service ID source,
   billing reference source, amount source, tax source, page-break
   carry-forward rules, skip rules, reconciliation targets, and unresolved risks.
3. Produce the BEST SupplierProfile draft you can, conforming exactly to the
   provided JSON schema. Only set fields you have real evidence for; leave the
   rest at their defaults (empty string / empty list / null).
4. Write a short, plain-language assistant_message explaining the extraction
   plan decisions you encoded in the draft, what changed, and why. Describe every
   output mapping change by output header, source, fallback, and reason. Ask one
   focused question when mapping evidence is ambiguous.

Guidance on the load-bearing fields:
- identity.canonical_name + aliases: the supplier's name and the variants printed
  on documents.
- field_overrides: how an existing canonical extraction field is recognized on
  THIS supplier's invoice. field_path identifies the exact document or row field;
  label_hint is its printed label. For dates, encode an explicitly stated input
  format such as %d/%m/%Y in date_format; delivery is normalized to %m/%d/%Y.
  Never relabel field.invoice_date as "Billing Period Start" to simulate delivery.
- output_mapping_overrides: exceptions to the selected OutputSpec. They decide
  which already-extracted canonical value populates a delivered output column.
  A field.* source repeats one document value; a row.* source resolves separately
  for every delivered line. Use this for EXT_DATE -> row.billing_period_start.
- transforms are safe delivery normalizations applied after source/fallback.
  Use digits_only for requests such as "remove dashes from account number";
  never claim a requested transform is implemented only by mentioning it in notes.
- Only create an output mapping when the user explicitly requests it or blueprint
  values consistently match the same alternative field across samples. Mixed or
  weak evidence requires an open question and no speculative mapping.
- Return the COMPLETE desired output_mapping_overrides list every turn. Preserve
  existing mappings unless the user requests a reset/change or strong new evidence
  contradicts them. Omitting an existing mapping resets it to the OutputSpec.
- line_item_hints.line_item_granularity: per_charge_row (one row per charge),
  per_service_group, or per_total_group (one row per service/total block).
- service_id_preference / billing_reference_preference: which printed identifier
  becomes the delivered service id and billing reference.
- When customer identifiers, supplier-internal references, account/sub-account
  numbers, or invoice numbers share the same shape, never use an unqualified
  broad regex. Encode the printed label/context that distinguishes the customer
  identifier or ask one focused question.
- *_column_label: the printed column header for the service id, billing reference,
  amount, or tax amount.
- amount_source / tax_source / tax_output_mode / tax_rate_source: executable
  amount and tax policies used when the blueprint shows how amounts and taxes
  should be delivered.
- detail_start_marker: the text where the line-item section begins.
- skip_row_keywords: text on rows that must NOT become line items (e.g. running totals).
- notes: free-text guidance for the AI extractor about this layout.

Use the available extraction toolkit: classify the document, read header fields,
fingerprint layout anchors, learn from XLS/XLSX blueprint rows, plan extraction
strategy, preserve parent-child blocks across pages, resolve grouped rows,
choose tax behavior, reconcile against invoice totals, diff against the
blueprint, explain differences, and route unresolved risks to review.

Use attached XLS/XLSX rows as the delivery contract for columns, row structure,
and mapping semantics. Use the PDF as evidence for printed facts and totals. A
difference is evidence to investigate, not proof that either side is wrong. Do
not drop invoice-backed charges only because they are absent from the blueprint;
explain exclusions or unresolved differences. Use the blueprint to learn what to
deliver and the PDF to prove what was printed. Explain every meaningful difference.

When discovery_candidates is present, select only a supplied candidate_id. Set
selected_plan_id to null when the deterministic evidence is insufficient and
state the exact ambiguity; never invent executable formulas or regexes."""


@dataclass
class InvoiceEvidence:
    """Per-invoice grounding for one authoring call."""

    label: str
    page_texts: list[str]
    target: dict  # _build_target-style baseline extraction evidence
    expected_rows: list[list[str]] | None = None  # parsed expected deliverable, if uploaded
    blueprint_context: dict | None = None  # parsed blueprint headers/rows/totals/warnings, if uploaded
    diff_feedback: str | None = None  # current draft's preview vs expected, if available


@dataclass
class ProfileAuthoringResult:
    draft: dict  # the authorable SupplierProfile subset
    assistant_message: str
    open_questions: list[str] = field(default_factory=list)
    confidence: float = 0.0
    ai_model_name: str = ""
    tokens_input: int = 0
    tokens_output: int = 0
    duration_ms: int = 0
    selected_plan_id: str | None = None


def _field_path(field) -> str:
    return f"{'field' if str(field.scope) == 'document' else 'row'}.{field.name}"


def available_output_source_paths(field_schema: FieldSchema) -> list[str]:
    return [*[_field_path(field) for field in field_schema.fields], *_COMPUTED_OUTPUT_SOURCES]


def _field_override_schema(field_paths: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "field_path": {"type": "string", "enum": field_paths},
            "label_hint": {"type": ["string", "null"]},
            "date_format": {"type": ["string", "null"]},
        },
        "required": ["field_path", "label_hint", "date_format"],
        "additionalProperties": False,
    }


def _output_mapping_override_schema(output_spec: OutputSpec, source_paths: list[str]) -> dict:
    headers = list(dict.fromkeys(column.header for column in output_spec.columns))
    return {
        "type": "object",
        "properties": {
            "output_header": {"type": "string", "enum": headers},
            "source": {"type": "string", "enum": source_paths},
            "fallback": {"type": ["string", "null"], "enum": [*source_paths, None]},
            "transforms": {
                "type": "array",
                "items": {"type": "string", "enum": _OUTPUT_TRANSFORMS},
            },
            "reason": {"type": "string"},
        },
        "required": ["output_header", "source", "fallback", "transforms", "reason"],
        "additionalProperties": False,
    }


def build_profile_authoring_schema(field_schema: FieldSchema, output_spec: OutputSpec) -> dict:
    """Strict JSON schema for the authorable subset of a SupplierProfile.

    Scoped label paths and output mappings are pinned to the merged field schema
    and selected output spec so the model cannot invent fields or columns.
    """
    field_paths = list(dict.fromkeys(_field_path(field) for field in field_schema.fields))
    source_paths = available_output_source_paths(field_schema)
    return {
        "name": "supplier_profile_draft",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "assistant_message": {"type": "string"},
                "open_questions": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number"},
                "selected_plan_id": {
                    "type": ["string", "null"],
                    "enum": ["deterministic.v1", None],
                },
                "profile": {
                    "type": "object",
                    "properties": {
                        "canonical_name": {"type": "string"},
                        "aliases": {"type": "array", "items": {"type": "string"}},
                        "output_type": {"type": "string"},
                        "notes": {"type": ["string", "null"]},
                        "field_overrides": {
                            "type": "array",
                            "items": _field_override_schema(field_paths),
                        },
                        "output_mapping_overrides": {
                            "type": "array",
                            "items": _output_mapping_override_schema(output_spec, source_paths),
                        },
                        "detail_start_marker": {"type": ["string", "null"]},
                        "detail_end_marker": {"type": ["string", "null"]},
                        "line_item_granularity": {"type": ["string", "null"], "enum": [*_GRANULARITIES, None]},
                        "service_id_preference": {"type": ["string", "null"], "enum": [*_SERVICE_ID_PREFS, None]},
                        "billing_reference_preference": {
                            "type": ["string", "null"], "enum": [*_BILLING_REF_PREFS, None]},
                        "service_id_column_label": {"type": ["string", "null"]},
                        "billing_reference_column_label": {"type": ["string", "null"]},
                        "amount_column_label": {"type": ["string", "null"]},
                        "tax_amount_column_label": {"type": ["string", "null"]},
                        "amount_source": {"type": ["string", "null"], "enum": [*_AMOUNT_SOURCES, None]},
                        "tax_source": {"type": ["string", "null"], "enum": [*_TAX_SOURCES, None]},
                        "tax_output_mode": {"type": ["string", "null"], "enum": [*_TAX_OUTPUT_MODES, None]},
                        "tax_rate_source": {"type": ["string", "null"], "enum": [*_TAX_RATE_SOURCES, None]},
                        "service_id_value_pattern": {"type": ["string", "null"]},
                        "billing_reference_value_pattern": {"type": ["string", "null"]},
                        "detail_table_anchors": {"type": "array", "items": {"type": "string"}},
                        "table_column_labels": {"type": "array", "items": {"type": "string"}},
                        "subtotal_keywords": {"type": "array", "items": {"type": "string"}},
                        "tax_keywords": {"type": "array", "items": {"type": "string"}},
                        "skip_row_keywords": {"type": "array", "items": {"type": "string"}},
                        "include_zero_amount_line_items": {"type": "boolean"},
                        "require_line_item_identifier": {"type": "boolean"},
                    },
                    "required": [
                        "canonical_name", "aliases", "output_type", "notes", "field_overrides",
                        "output_mapping_overrides",
                        "detail_start_marker", "detail_end_marker", "line_item_granularity",
                        "service_id_preference", "billing_reference_preference",
                        "service_id_column_label", "billing_reference_column_label",
                        "amount_column_label", "tax_amount_column_label", "amount_source",
                        "tax_source", "tax_output_mode", "tax_rate_source",
                        "service_id_value_pattern", "billing_reference_value_pattern",
                        "detail_table_anchors", "table_column_labels", "subtotal_keywords",
                        "tax_keywords", "skip_row_keywords", "include_zero_amount_line_items",
                        "require_line_item_identifier",
                    ],
                    "additionalProperties": False,
                },
            },
            "required": [
                "assistant_message", "open_questions", "confidence", "selected_plan_id", "profile",
            ],
            "additionalProperties": False,
        },
    }


def draft_to_supplier_profile_dict(
    draft: dict, *, output_spec_id: str, field_schema_id: str,
    field_schema: FieldSchema | None = None, output_spec: OutputSpec | None = None,
) -> dict:
    """Map the flat authored ``draft`` onto the SupplierProfile JSON shape.

    Returns a dict that ``SupplierProfile.model_validate`` accepts. ``profile_id``
    and ``delivery`` are intentionally left for the user to set at finalize time.
    Overrides carry the base field's scope/type/role (the merge is label-only) so
    the resulting ``FieldDef`` validates.
    """
    p = draft.get("profile", draft)
    base_by_name = {}
    base_by_path = {}
    if field_schema:
        for field in field_schema.fields:
            # Legacy name-only drafts default ambiguous names to the first
            # (normally document-scoped) schema field. New drafts use field_path.
            base_by_name.setdefault(field.name, field)
            base_by_path[_field_path(field)] = field
    overrides = []
    for o in (p.get("field_overrides") or []):
        field_path = o.get("field_path")
        base = base_by_path.get(field_path) if field_path else None
        name = base.name if base else o.get("name")
        if not name:
            continue
        if base is None and o.get("scope"):
            prefix = "field" if o.get("scope") == "document" else "row"
            base = base_by_path.get(f"{prefix}.{name}")
        if base is None:
            base = base_by_name.get(name)
        overrides.append({
            "name": name,
            "scope": str(base.scope) if base else "document",
            "type": str(base.type) if base else "string",
            "role": str(base.role) if base else "none",
            "label_hint": o.get("label_hint") or None,
            "date_format": o.get("date_format") or None,
        })
    result = {
        "identity": {
            "canonical_name": p.get("canonical_name") or "",
            "aliases": list(p.get("aliases") or []),
        },
        "classification": {"output_type": p.get("output_type") or "standard"},
        "output_spec_id": output_spec_id,
        "field_schema_id": field_schema_id,
        "field_overrides": overrides,
        "notes": p.get("notes") or None,
        "advanced": {
            "document_structure": {
                "detail_start_marker": p.get("detail_start_marker") or None,
                "detail_end_marker": p.get("detail_end_marker") or None,
            },
            "line_item_hints": {
                "subtotal_keywords": list(p.get("subtotal_keywords") or []) or ["Subtotal", "Sub Total", "Sub-Total"],
                "tax_keywords": list(p.get("tax_keywords") or []) or ["Tax", "Taxes", "VAT"],
                "detail_table_anchors": list(p.get("detail_table_anchors") or []),
                "line_item_granularity": p.get("line_item_granularity") or None,
                "service_id_preference": p.get("service_id_preference") or None,
                "billing_reference_preference": p.get("billing_reference_preference") or None,
                "service_id_column_label": p.get("service_id_column_label") or None,
                "billing_reference_column_label": p.get("billing_reference_column_label") or None,
                "amount_column_label": p.get("amount_column_label") or None,
                "tax_amount_column_label": p.get("tax_amount_column_label") or None,
                "amount_source": p.get("amount_source") or None,
                "tax_source": p.get("tax_source") or None,
                "tax_output_mode": p.get("tax_output_mode") or None,
                "tax_rate_source": p.get("tax_rate_source") or None,
                "service_id_value_pattern": p.get("service_id_value_pattern") or None,
                "billing_reference_value_pattern": p.get("billing_reference_value_pattern") or None,
                "skip_row_keywords": list(p.get("skip_row_keywords") or []),
                "table_column_labels": list(p.get("table_column_labels") or []),
            },
            "include_zero_amount_line_items": bool(p.get("include_zero_amount_line_items")),
            "require_line_item_identifier": bool(p.get("require_line_item_identifier")),
            "extraction_plan": p.get("extraction_plan"),
        },
        "authoring_evidence": p.get("authoring_evidence"),
    }
    # Absence is distinct from an authored empty list for legacy edit sessions:
    # absent preserves source mappings; an empty list intentionally resets them.
    if "output_mapping_overrides" in p:
        mappings = []
        seen_headers: set[str] = set()
        allowed_headers = {column.header for column in output_spec.columns} if output_spec else None
        allowed_sources = set(available_output_source_paths(field_schema)) if field_schema else None
        for mapping in p.get("output_mapping_overrides") or []:
            header = str(mapping.get("output_header") or "").strip()
            source = str(mapping.get("source") or "").strip()
            fallback = mapping.get("fallback") or None
            transforms = list(mapping.get("transforms") or [])
            if not header or not source:
                raise ValueError("output mapping requires output_header and source")
            if header in seen_headers:
                raise ValueError(f"duplicate output mapping for '{header}'")
            if allowed_headers is not None and header not in allowed_headers:
                raise ValueError(f"unknown output mapping column '{header}'")
            if allowed_sources is not None and source not in allowed_sources:
                raise ValueError(f"unknown output mapping source '{source}'")
            if fallback and allowed_sources is not None and fallback not in allowed_sources:
                raise ValueError(f"unknown output mapping fallback '{fallback}'")
            if len(transforms) != len(set(transforms)):
                raise ValueError(f"duplicate transforms for output mapping '{header}'")
            if any(transform not in _OUTPUT_TRANSFORMS for transform in transforms):
                raise ValueError(f"unsupported transform for output mapping '{header}'")
            seen_headers.add(header)
            inherited = next(
                (column for column in (output_spec.columns if output_spec else []) if column.header == header),
                None,
            )
            if (
                inherited is not None
                and inherited.source == source
                and (inherited.fallback or None) == fallback
                and not transforms
            ):
                continue
            mappings.append({
                "output_header": header,
                "source": source,
                "fallback": fallback,
                "transforms": transforms,
            })
        result["output_mapping_overrides"] = mappings
    return result


def supplier_profile_to_draft(profile) -> dict:
    """Inverse of ``draft_to_supplier_profile_dict``: seed an authoring draft from an
    existing profile so the assistant can refine its extraction hints.

    Only the AI-authorable surface is reflected (identity, classification, field-label
    overrides, and the advanced hint block). Delivery, training, fingerprint, and
    lifecycle stay on the source profile and are re-merged at finalize time.
    """
    ds = profile.document_structure
    lih = profile.line_item_hints
    advanced = profile.advanced
    return {
        "profile": {
            "canonical_name": profile.identity.canonical_name,
            "aliases": list(profile.identity.aliases or []),
            "output_type": profile.classification.output_type,
            "field_overrides": [
                {
                    "field_path": f"{'field' if str(o.scope) == 'document' else 'row'}.{o.name}",
                    "label_hint": o.label_hint,
                    "date_format": o.date_format,
                }
                for o in (profile.field_overrides or [])
                if o.label_hint or o.date_format
            ],
            "output_mapping_overrides": [
                {
                    "output_header": mapping.output_header,
                    "source": mapping.source,
                    "fallback": mapping.fallback,
                    "transforms": list(mapping.transforms),
                    "reason": "Existing profile override.",
                }
                for mapping in (profile.output_mapping_overrides or [])
            ],
            "notes": profile.notes,
            "detail_start_marker": ds.detail_start_marker,
            "detail_end_marker": ds.detail_end_marker,
            "subtotal_keywords": list(lih.subtotal_keywords or []),
            "tax_keywords": list(lih.tax_keywords or []),
            "detail_table_anchors": list(lih.detail_table_anchors or []),
            "line_item_granularity": lih.line_item_granularity,
            "service_id_preference": lih.service_id_preference,
            "billing_reference_preference": lih.billing_reference_preference,
            "service_id_column_label": lih.service_id_column_label,
            "billing_reference_column_label": lih.billing_reference_column_label,
            "amount_column_label": lih.amount_column_label,
            "tax_amount_column_label": lih.tax_amount_column_label,
            "amount_source": lih.amount_source,
            "tax_source": lih.tax_source,
            "tax_output_mode": lih.tax_output_mode,
            "tax_rate_source": lih.tax_rate_source,
            "service_id_value_pattern": lih.service_id_value_pattern,
            "billing_reference_value_pattern": lih.billing_reference_value_pattern,
            "skip_row_keywords": list(lih.skip_row_keywords or []),
            "table_column_labels": list(lih.table_column_labels or []),
            "include_zero_amount_line_items": advanced.include_zero_amount_line_items,
            "require_line_item_identifier": advanced.require_line_item_identifier,
        }
    }


def merge_source_profile_config(source, authored: dict) -> dict:
    """Layer authored settings over a source profile without losing non-AI config.

    Output mappings are a complete desired list when present. An authored empty
    list intentionally resets them; legacy payloads that omit the property keep
    the source profile's mappings.
    """
    source_dict = source.model_dump(mode="json")
    merged = {**source_dict, **authored}
    merged["advanced"] = {
        **(source_dict.get("advanced") or {}),
        **(authored.get("advanced") or {}),
    }
    overrides = {
        (item.get("scope"), item.get("name")): item
        for item in source_dict.get("field_overrides") or []
    }
    for item in authored.get("field_overrides") or []:
        key = (item.get("scope"), item.get("name"))
        overrides[key] = {**overrides.get(key, {}), **item}
    merged["field_overrides"] = list(overrides.values())
    if "output_mapping_overrides" not in authored:
        merged["output_mapping_overrides"] = source_dict.get("output_mapping_overrides") or []
    return merged


def _estimate_tokens(text: str) -> int:
    return len(text) // 4  # ~4 chars/token, matches models/authoring.py heuristic


def _invoice_totals(target: dict) -> dict:
    totals = dict(target.get("totals") or {})
    line_items = target.get("line_items") or []
    amount_total = 0.0
    tax_total = 0.0
    for item in line_items:
        try:
            amount_total += float(item.get("amount") or 0)
        except (TypeError, ValueError):
            pass
        try:
            tax_total += float(item.get("tax_amount") or 0)
        except (TypeError, ValueError):
            pass
    totals["sampled_line_amount_total"] = f"{amount_total:.2f}"
    totals["sampled_line_tax_total"] = f"{tax_total:.2f}"
    totals["sampled_line_count"] = len(line_items)
    return totals


def _values_for_source(target: dict, source: str) -> list[object]:
    if source.startswith(("field.", "header.")):
        name = source.split(".", 1)[1]
        value = (target.get("header") or {}).get(name)
        return [] if value in (None, "") else [value]
    if source.startswith(("row.", "line_item.")):
        name = source.split(".", 1)[1]
        return [
            item.get(name)
            for item in (target.get("line_items") or [])
            if item.get(name) not in (None, "")
        ]
    value = (target.get("totals") or {}).get(source)
    return [] if value in (None, "") else [value]


def _normalized_source_values(values: list[object], source: str) -> set[str]:
    is_date = source.endswith(("_date", "_start", "_end"))
    return {
        _date_for_compare(value) if is_date else _clean_compare_text(value)
        for value in values
        if value not in (None, "")
    }


def _blueprint_vs_invoice_summary(
    blueprint_context: dict | None,
    target: dict,
    *,
    output_spec: OutputSpec | None = None,
    field_schema: FieldSchema | None = None,
) -> dict | None:
    if not blueprint_context:
        return None
    invoice_totals = target.get("totals") or {}
    blueprint_totals = blueprint_context.get("totals") or {}
    comparisons = []
    warnings = []
    if blueprint_totals.get("amount") and invoice_totals.get("subtotal"):
        comparisons.append({
            "blueprint": "amount",
            "invoice": "subtotal",
            "blueprint_value": blueprint_totals["amount"],
            "invoice_value": invoice_totals["subtotal"],
        })
        if _decimal_for_compare(blueprint_totals["amount"]) != _decimal_for_compare(invoice_totals["subtotal"]):
            warnings.append(
                "Blueprint amount total differs from the printed invoice subtotal; "
                "explain the delivery semantics or reconcile the difference."
            )
    if blueprint_totals.get("tax") and invoice_totals.get("tax"):
        comparisons.append({
            "blueprint": "tax",
            "invoice": "tax",
            "blueprint_value": blueprint_totals["tax"],
            "invoice_value": invoice_totals["tax"],
        })
        if _decimal_for_compare(blueprint_totals["tax"]) != _decimal_for_compare(invoice_totals["tax"]):
            warnings.append(
                "Blueprint tax total differs from the printed invoice tax total; "
                "explain the delivery semantics or reconcile the difference."
            )
    header = target.get("header") or {}
    invoice_number = _clean_compare_text(header.get("invoice_number"))
    blueprint_invoice_numbers = {
        _clean_compare_text(value)
        for value in _blueprint_column_values(
            blueprint_context,
            {"extinvoicenumber", "invoicenumber"},
        )
    }
    if invoice_number and blueprint_invoice_numbers and invoice_number not in blueprint_invoice_numbers:
        warnings.append(
            "Blueprint invoice number differs from the baseline header extraction; verify the intended output mapping."
        )
    invoice_date = _date_for_compare(header.get("invoice_date"))
    blueprint_dates = {
        _date_for_compare(value)
        for value in _blueprint_column_values(blueprint_context, {"extdate", "invoicedate", "date"})
    }
    blueprint_dates.discard("")
    if invoice_date and blueprint_dates and invoice_date not in blueprint_dates:
        warnings.append(
            "Blueprint date differs from the baseline invoice date; check billing-period "
            "or row-date fields before choosing an output mapping."
        )
    mapping_evidence = []
    if output_spec and field_schema:
        source_paths = available_output_source_paths(field_schema)
        for column in output_spec.columns:
            blueprint_values = _blueprint_column_values(
                blueprint_context, {_normalized_header_token(column.header)})
            if not blueprint_values:
                continue
            blueprint_normalized = _normalized_source_values(blueprint_values, column.source)
            base_values = _values_for_source(target, column.source)
            base_normalized = _normalized_source_values(base_values, column.source)
            alternatives = []
            if blueprint_normalized and blueprint_normalized != base_normalized:
                for source in source_paths:
                    if source == column.source or source.startswith("computed."):
                        continue
                    candidate = _normalized_source_values(_values_for_source(target, source), source)
                    if candidate and candidate == blueprint_normalized:
                        alternatives.append(source)
            mapping_evidence.append({
                "output_header": column.header,
                "base_source": column.source,
                "blueprint_values": blueprint_values,
                "base_values": base_values,
                "base_matches": bool(blueprint_normalized and blueprint_normalized == base_normalized),
                "matching_alternative_sources": alternatives,
            })
    return {
        "blueprint_row_count": blueprint_context.get("row_count"),
        "invoice_line_item_count_in_prompt": len(target.get("line_items") or []),
        "blueprint_totals": blueprint_totals,
        "invoice_totals": invoice_totals,
        "comparisons": comparisons,
        "output_mapping_evidence": mapping_evidence,
        "warnings": warnings,
    }


def _blueprint_column_values(blueprint_context: dict, accepted_headers: set[str]) -> list[str]:
    headers = blueprint_context.get("aligned_headers") or blueprint_context.get("raw_headers") or []
    rows = blueprint_context.get("aligned_rows") or []
    indexes = [
        index
        for index, header in enumerate(headers)
        if _normalized_header_token(header) in accepted_headers
    ]
    values: list[str] = []
    for row in rows:
        for index in indexes:
            if index >= len(row):
                continue
            value = str(row[index] or "").strip()
            if value and value not in values:
                values.append(value)
    return values


def _normalized_header_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _clean_compare_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _decimal_for_compare(value: object) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _date_for_compare(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return _clean_compare_text(text)


def _build_user_prompt(
    *,
    field_schema: FieldSchema,
    output_spec: OutputSpec,
    invoices: list[InvoiceEvidence],
    conversation: list[dict],
    current_draft: dict | None,
    discovery_report: dict | None,
    budget: int,
) -> str:
    """Assemble the authoring prompt, trimming layout text if over the token budget."""
    label_hints = [
        {"name": f.name, "scope": str(f.scope), "type": str(f.type),
         "role": str(f.role), "label_hint": f.label_hint, "date_format": f.date_format}
        for f in field_schema.fields
    ]
    deliverable = [
        {"header": c.header, "source": c.source, "fallback": c.fallback}
        for c in output_spec.columns
    ]
    history = [
        {"role": m.get("role"), "content": m.get("content")}
        for m in conversation
        if m.get("role") in {"user", "assistant"}
    ]

    def assemble(page_text_chars: int | None) -> str:
        invoice_blocks = []
        for inv in invoices:
            texts = inv.page_texts
            if page_text_chars is not None:
                texts = [t[:page_text_chars] for t in texts]
            block = {
                "label": inv.label,
                "baseline_extracted_evidence": inv.target,
                "invoice_totals": _invoice_totals(inv.target),
                "layout_text": texts,
            }
            if inv.blueprint_context is not None:
                block["blueprint_context"] = inv.blueprint_context
                block["blueprint_vs_invoice_summary"] = _blueprint_vs_invoice_summary(
                    inv.blueprint_context, inv.target,
                    output_spec=output_spec, field_schema=field_schema)
            elif inv.expected_rows is not None:
                block["blueprint_context"] = {
                    "aligned_rows": inv.expected_rows,
                    "row_count": len(inv.expected_rows),
                    "sample_rows": inv.expected_rows[:10],
                }
                block["blueprint_vs_invoice_summary"] = _blueprint_vs_invoice_summary(
                    block["blueprint_context"], inv.target,
                    output_spec=output_spec, field_schema=field_schema)
            if inv.diff_feedback:
                block["preview_vs_blueprint_diff"] = inv.diff_feedback
            invoice_blocks.append(block)
        payload = {
            "field_schema": {"schema_id": field_schema.schema_id, "fields": label_hints},
            "deliverable_columns": deliverable,
            "allowed_output_sources": available_output_source_paths(field_schema),
            "conversation": history,
            "current_draft": current_draft,
            "discovery_candidates": (
                {
                    "candidate_id": "deterministic.v1",
                    **discovery_report,
                }
                if discovery_report else None
            ),
            "sample_invoices": invoice_blocks,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    prompt = assemble(None)
    if _estimate_tokens(prompt) <= budget:
        return prompt
    # Progressively trim layout text per page until under budget (extracted evidence +
    # blueprint context + diffs are never trimmed - they are the grounding signal).
    for cap in (6000, 4000, 2500, 1500, 800):
        prompt = assemble(cap)
        if _estimate_tokens(prompt) <= budget:
            logger.info("profile_authoring.prompt_trimmed", page_text_chars=cap)
            return prompt
    raise ValueError(
        f"Profile authoring evidence exceeds the {budget:,}-token safeguard after compaction"
    )


def author_profile(
    *,
    field_schema: FieldSchema,
    output_spec: OutputSpec,
    invoices: list[InvoiceEvidence],
    conversation: list[dict],
    current_draft: dict | None = None,
    discovery_report: dict | None = None,
) -> ProfileAuthoringResult:
    """One AI call: author a SupplierProfile draft grounded in the sample invoices.

    ``conversation`` is the prior chat history (the latest user message included);
    ``current_draft`` is the previous turn's authored profile (the flat draft), so
    refinement turns build on what's there instead of starting over.
    """
    if not invoices:
        raise ValueError("profile authoring requires at least one extracted invoice")

    schema = build_profile_authoring_schema(field_schema, output_spec)
    user_prompt = _build_user_prompt(
        field_schema=field_schema,
        output_spec=output_spec,
        invoices=invoices,
        conversation=conversation,
        current_draft=current_draft,
        discovery_report=discovery_report,
        budget=min(
            settings.model_authoring_max_input_tokens,
            settings.profile_discovery_max_prompt_tokens,
        ),
    )

    client = get_ai_client(purpose="profile_authoring")
    model_name = runtime_settings.openai_model_model_generation()
    started = time.monotonic()
    response = traced_chat_completion(
        client,
        call_type="profile_authoring",
        context=invoices[0].label if invoices else "forge",
        model=model_name,
        messages=[
            {"role": "system", "content": PROFILE_AUTHORING_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_schema", "json_schema": schema},
        max_completion_tokens=runtime_settings.openai_max_output_tokens(),
    )
    duration_ms = int((time.monotonic() - started) * 1000)
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("profile authoring returned empty content")
    raw = json.loads(content)
    usage = response.usage
    return ProfileAuthoringResult(
        draft={"profile": raw["profile"]},
        assistant_message=raw.get("assistant_message", ""),
        open_questions=list(raw.get("open_questions") or []),
        confidence=float(raw.get("confidence") or 0.0),
        ai_model_name=model_name,
        tokens_input=(usage.prompt_tokens if usage else 0),
        tokens_output=(usage.completion_tokens if usage else 0),
        duration_ms=duration_ms,
        selected_plan_id=raw.get("selected_plan_id"),
    )
