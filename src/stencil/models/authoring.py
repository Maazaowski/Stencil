"""AI-authored declarative extraction models.

The AI has just read and understood an invoice during extraction, so it is
asked to ALSO author a reusable, declarative rule document (an ExtractionModel)
for the supplier layout. The generic interpreter then replays that document on
future invoices with zero AI cost.

This module makes ONE AI call per invocation. The author -> execute -> diff ->
refine loop is orchestrated by `stencil.models.training`, which feeds the
structured diff back through the `feedback` parameter.
"""

import json
import re
import time
from dataclasses import dataclass

import structlog

from stencil import runtime_settings
from stencil.ai_debug import traced_chat_completion
from stencil.config import settings
from stencil.extraction.prompts import (
    MODEL_AUTHORING_SYSTEM_PROMPT,
    build_authoring_user_prompt,
)
from stencil.fields.loader import (
    AUTHORING_HEADER_FIELD_NAMES,
    AUTHORING_ITEM_FIELD_NAMES,
    AUTHORING_TOTAL_FIELD_NAMES,
    default_field_schema,
)
from stencil.fields.schema import FieldSchema
from stencil.llm import get_ai_client
from stencil.models.interpreter import (
    _find_value_by_label,
    _line_cells,
    _model_known_labels,
    find_header_value,
    find_total_value,
)
from stencil.models.schema import ExtractionModel, HeaderFieldRule, ModelStatus, SelfCheck
from stencil.output.xlsx_writer import resolve_tax_rate, service_items_have_per_line_tax
from stencil.profiles.schema import SupplierProfile
from stencil.validation.schema import CanonicalInvoice

logger = structlog.get_logger()

_HEADER_FIELDS = AUTHORING_HEADER_FIELD_NAMES
_ITEM_FIELDS = AUTHORING_ITEM_FIELD_NAMES
_TOTAL_NAMES = AUTHORING_TOTAL_FIELD_NAMES
_POSITIONS = ["right", "left", "below", "above"]
_OCCURRENCE = ["first", "last"]
_TRANSFORM_TYPES = ["string", "currency", "integer", "decimal", "date"]
_ROW_SELECTORS = ["role", "emit_row", "first", "last", "all_in_group"]
_GROUPING_MODES = ["single_row", "span", "role_transition"]
_EMIT_MODES = ["one_item_per_group", "one_item_per_role_row"]
_SELF_CHECK_FIELDS = ["subtotal", "total_due", "current_charges"]


def _nullable(schema: dict) -> dict:
    out = dict(schema)
    out["type"] = [schema["type"], "null"] if isinstance(schema["type"], str) else schema["type"]
    return out


def _locator_schema(name_enum: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string", "enum": name_enum},
            "label": {"type": "string"},
            "value_position": {"type": "string", "enum": _POSITIONS},
            "value_pattern": {"type": ["string", "null"]},
            "date_format": {"type": ["string", "null"]},
            "ignore_percent": {"type": "boolean"},
            "occurrence": {"type": "string", "enum": _OCCURRENCE},
            "page": {"type": "integer"},
            "required": {"type": "boolean"},
        },
        "required": ["name", "label", "value_position", "value_pattern", "date_format",
                     "ignore_percent", "occurrence", "page", "required"],
        "additionalProperties": False,
    }


MODEL_RULES_JSON_SCHEMA = {
    "name": "extraction_rule_document",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "header_fields": {
                "type": "array",
                "items": _locator_schema(_HEADER_FIELDS),
            },
            "region": {
                "type": "object",
                "properties": {
                    "start_anchors": {"type": "array", "items": {"type": "string"}},
                    "end_anchors": {"type": "array", "items": {"type": "string"}},
                    "end_scope": {"type": "string", "enum": ["document", "page"]},
                    "columns": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "x0": {"type": "number"},
                                "x1": {"type": "number"},
                                "header_text": {"type": ["string", "null"]},
                            },
                            "required": ["name", "x0", "x1", "header_text"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["start_anchors", "end_anchors", "end_scope", "columns"],
                "additionalProperties": False,
            },
            "row_classifiers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "where": {
                            "type": "object",
                            "properties": {
                                "row_text": {"type": ["string", "null"]},
                                "column": {"type": ["string", "null"]},
                                "pattern": {"type": ["string", "null"]},
                                "min_cells": {"type": ["integer", "null"]},
                                "has_amount_in_column": {"type": ["string", "null"]},
                            },
                            "required": ["row_text", "column", "pattern", "min_cells",
                                         "has_amount_in_column"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["role", "where"],
                    "additionalProperties": False,
                },
            },
            "grouping": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": _GROUPING_MODES},
                    "item_role": {"type": ["string", "null"]},
                    "start_role": {"type": ["string", "null"]},
                    "end_role": {"type": ["string", "null"]},
                    "include_end_row": {"type": "boolean"},
                    "emit": {"type": "string", "enum": _EMIT_MODES},
                    "emit_role": {"type": ["string", "null"]},
                },
                "required": ["mode", "item_role", "start_role", "end_role",
                             "include_end_row", "emit", "emit_role"],
                "additionalProperties": False,
            },
            "item_fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": _ITEM_FIELDS},
                        "source": {
                            "type": ["object", "null"],
                            "properties": {
                                "rows": {"type": "string", "enum": _ROW_SELECTORS},
                                "row_role": {"type": ["string", "null"]},
                                "column": {"type": ["string", "null"]},
                                "pattern": {"type": ["string", "null"]},
                                "join": {"type": ["string", "null"]},
                                "occurrence": {"type": "string", "enum": _OCCURRENCE},
                            },
                            "required": ["rows", "row_role", "column", "pattern", "join", "occurrence"],
                            "additionalProperties": False,
                        },
                        "transform": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": _TRANSFORM_TYPES},
                                "date_format": {"type": ["string", "null"]},
                                "value_map": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "from": {"type": "string"},
                                            "to": {"type": "string"},
                                        },
                                        "required": ["from", "to"],
                                        "additionalProperties": False,
                                    },
                                },
                                "default": {"type": ["string", "null"]},
                            },
                            "required": ["type", "date_format", "value_map", "default"],
                            "additionalProperties": False,
                        },
                        "literal": {"type": ["string", "null"]},
                        "same_as": {"type": ["string", "null"]},
                        "required": {"type": "boolean"},
                    },
                    "required": ["name", "source", "transform", "literal", "same_as", "required"],
                    "additionalProperties": False,
                },
            },
            "totals": {
                "type": "array",
                "items": _locator_schema(_TOTAL_NAMES),
            },
            "self_checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "check": {"type": "string", "enum": ["sum_of_items_equals"]},
                        "field": {"type": "string", "enum": _SELF_CHECK_FIELDS},
                        "tolerance": {"type": "number"},
                    },
                    "required": ["check", "field", "tolerance"],
                    "additionalProperties": False,
                },
            },
            "confidence": {"type": "number"},
            "notes": {"type": ["string", "null"]},
        },
        "required": ["header_fields", "region", "row_classifiers", "grouping",
                     "item_fields", "totals", "self_checks", "confidence", "notes"],
        "additionalProperties": False,
    },
}


def build_model_rules_json_schema(field_schema: FieldSchema | None = None) -> dict:
    """Build OpenAI strict schema for model authoring from a FieldSchema."""
    import copy

    from stencil.fields.schema import FieldRole

    fs = field_schema or default_field_schema()
    header_names = [f.name for f in fs.document_fields() if f.name in set(_HEADER_FIELDS)] or list(_HEADER_FIELDS)
    item_names = [f.name for f in fs.row_fields() if f.name in set(_ITEM_FIELDS)] or list(_ITEM_FIELDS)
    total_names = [f.name for f in fs.document_fields() if f.name in set(_TOTAL_NAMES)] or list(_TOTAL_NAMES)
    self_check_fields = [
        f.name for f in fs.document_fields()
        if f.role in (FieldRole.TOTAL, FieldRole.SUBTOTAL)
    ] or list(_SELF_CHECK_FIELDS)

    out = copy.deepcopy(MODEL_RULES_JSON_SCHEMA)
    props = out["schema"]["properties"]
    props["header_fields"]["items"] = _locator_schema(header_names)
    props["totals"]["items"] = _locator_schema(total_names)
    props["item_fields"]["items"]["properties"]["name"]["enum"] = item_names
    props["self_checks"]["items"]["properties"]["field"]["enum"] = self_check_fields
    return out


@dataclass
class AuthoringExample:
    """One invoice in a training set: its layout + AI ground truth."""

    page_texts: list[str]
    layout_evidence: dict
    ai_invoice: CanonicalInvoice
    intake_id: str


@dataclass
class AuthoringResult:
    model: ExtractionModel
    tokens_input: int
    tokens_output: int
    duration_ms: int
    ai_model_name: str


def _estimate_tokens(text: str) -> int:
    return len(text) // 4  # ~4 chars/token, good enough for a budget guard


def _strip_layout_summary(evidence: dict | None) -> dict:
    """Keep the per-value ``target_alignment`` (the grounding the AI needs), drop
    the bulky ``layout`` structure dump — it is redundant with the coordinate-aware
    page text the same invoice already carries."""
    return {"target_alignment": (evidence or {}).get("target_alignment")}


def _build_and_fit_prompt(
    *, budget: int, page_texts: list[str], additional: list[dict], layout_evidence: dict,
    page_priority: list[int] | None = None, **kwargs,
) -> str:
    """Build the authoring prompt; if over ``budget`` tokens, trim in order of
    least value until it fits. Normal-size layouts are well under budget and are
    therefore sent UNCHANGED (corpus parity preserved); only pathologically large
    documents get trimmed.

    Trim order: (1) extras' redundant layout summary, (2) extras' page text,
    (3) primary's redundant layout summary, (4) drop primary pages.

    Step 4 used to keep a *prefix* of pages, which is only the right answer when
    the interesting part of a document is at its front. ``page_priority`` (1-based
    page numbers, most informative first, from deterministic page classification)
    makes it keep the pages that carry the document's shape instead -- the header,
    the boundaries of each table run, a few continuation pages -- emitted in
    document order so the model still reads them front to back.
    """
    def build(primary_pages: list[str], primary_ev: dict, extras: list[dict]) -> str:
        return build_authoring_user_prompt(
            page_texts=primary_pages, layout_evidence=primary_ev,
            additional_examples=extras, **kwargs,
        )

    prompt = build(page_texts, layout_evidence, additional)
    if not budget or _estimate_tokens(prompt) <= budget:
        return prompt

    extras = [{**ex, "layout_evidence": _strip_layout_summary(ex.get("layout_evidence"))} for ex in additional]
    prompt = build(page_texts, layout_evidence, extras)
    if _estimate_tokens(prompt) <= budget:
        return prompt

    extras = [{**ex, "page_texts": []} for ex in extras]
    prompt = build(page_texts, layout_evidence, extras)
    if _estimate_tokens(prompt) <= budget:
        return prompt

    lean_primary = _strip_layout_summary(layout_evidence)
    prompt = build(page_texts, lean_primary, extras)
    if _estimate_tokens(prompt) <= budget:
        return prompt

    overhead = len(prompt) - sum(len(p) for p in page_texts)
    keep_chars = max(budget * 4 - overhead, 4000)

    order = [n for n in (page_priority or []) if 1 <= n <= len(page_texts)]
    order += [n for n in range(1, len(page_texts) + 1) if n not in set(order)]

    kept: set[int] = set()
    used = 0
    for number in order:
        size = len(page_texts[number - 1])
        if kept and used + size > keep_chars:
            continue
        kept.add(number)
        used += size
    trimmed = [page_texts[n - 1] for n in sorted(kept)]
    return build(trimmed or page_texts[:1], lean_primary, extras)


_SECTION_KEYS = {
    "scalars": ["header_fields", "totals", "self_checks"],
    "line_items": ["region", "row_classifiers", "grouping", "item_fields"],
}
_SECTION_FOCUS = {
    "scalars": ("FOCUS: author ONLY the document-level locators — header_fields and totals "
                "(plus any self_checks). Do NOT author line-item rules."),
    "line_items": ("FOCUS: author ONLY the line-item rules — region, row_classifiers, grouping, "
                   "and item_fields. Do NOT author header_fields or totals."),
}


def _section_schema(field_schema, section: str) -> dict:
    """A strict response schema restricted to one section's properties."""
    import copy

    keep = set(_SECTION_KEYS[section]) | {"confidence", "notes"}
    out = copy.deepcopy(build_model_rules_json_schema(field_schema))
    schema = out["schema"]
    schema["properties"] = {k: v for k, v in schema["properties"].items() if k in keep}
    schema["required"] = [k for k in schema["required"] if k in keep]
    out["name"] = f"extraction_rules_{section}"
    return out


def _slice_target(target: dict, section: str) -> dict:
    """Keep only the section's ground truth (the focused call ignores the rest)."""
    if section == "scalars":
        return {"header": target.get("header") or {}, "totals": target.get("totals") or {}, "line_items": []}
    return {"header": {}, "totals": {}, "line_items": target.get("line_items") or []}


def _slice_evidence(evidence: dict | None, section: str) -> dict:
    """Keep only the section's value alignment; drop the bulky redundant layout summary."""
    alignment = (evidence or {}).get("target_alignment") or {}
    if section == "scalars":
        sliced = {k: alignment.get(k) for k in ("header", "totals")}
    else:
        sliced = {"line_items": alignment.get("line_items")}
    return {"target_alignment": sliced}


def _author_section(
    *, client, section: str, field_schema, page_texts: list[str], prompt_target: dict,
    layout_evidence: dict, additional: list[dict], profile: SupplierProfile,
    line_amounts_are_net: bool, feedback: str | None,
    page_priority: list[int] | None = None,
) -> tuple[dict, int, int, int]:
    """One focused AI authoring call for a single section of the rule document.

    Returns (raw_rules, tokens_in, tokens_out, duration_ms).
    """
    schema = _section_schema(field_schema, section)
    target = _slice_target(prompt_target, section)
    evidence = _slice_evidence(layout_evidence, section)
    extras = [
        {**ex,
         "target": _slice_target(ex["target"], section),
         "layout_evidence": _slice_evidence(ex.get("layout_evidence"), section)}
        for ex in additional
    ]
    user_prompt = _build_and_fit_prompt(
        budget=settings.model_authoring_max_input_tokens,
        page_texts=page_texts,
        page_priority=page_priority,
        additional=extras,
        layout_evidence=evidence,
        target=target,
        supplier_profile=profile.model_dump(),
        field_schema=field_schema,
        line_amounts_are_net=line_amounts_are_net,
        feedback=feedback,
        focus=_SECTION_FOCUS[section],
    )
    started = time.monotonic()
    response = traced_chat_completion(
        client,
        call_type="model_authoring",
        context=f"{profile.identity.canonical_name}:{section}",
        model=runtime_settings.openai_model_model_generation(),
        messages=[
            {"role": "system", "content": MODEL_AUTHORING_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_schema", "json_schema": schema},
        max_completion_tokens=runtime_settings.openai_max_output_tokens(),
    )
    duration_ms = int((time.monotonic() - started) * 1000)
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError(f"model authoring ({section}) returned empty content")
    raw = json.loads(content)
    usage = response.usage
    return raw, (usage.prompt_tokens if usage else 0), (usage.completion_tokens if usage else 0), duration_ms


def author_extraction_model(
    *,
    page_texts: list[str],
    layout_evidence: dict,
    ai_invoice: CanonicalInvoice,
    profile: SupplierProfile | None,
    fingerprint: str,
    layout_family_key: str | None,
    intake_id: str,
    feedback: str | None = None,
    extra_examples: list[AuthoringExample] | None = None,
    page_priority: list[int] | None = None,
) -> AuthoringResult:
    """One AI authoring call: a single rule document grounded in evidence.

    The named ``page_texts``/``layout_evidence``/``ai_invoice``/``intake_id``
    describe the PRIMARY example (the caller passes the richest invoice here —
    it drives the deterministic header/total repair and the layout shown in
    full). ``extra_examples`` are additional same-layout invoices whose ground
    truth the one rule document must ALSO reproduce, so the rules generalize
    instead of overfitting the first invoice's row count/fields.
    """
    if profile is None:
        raise ValueError("model authoring requires a supplier profile")

    from stencil.extraction.normalization import has_grouping_policy
    from stencil.fields.loader import resolve_merged_field_schema

    field_schema = resolve_merged_field_schema(profile)
    # FULL target drives the deterministic header/total repair + self-checks.
    target = _build_target(ai_invoice)
    # SAMPLED target/evidence keep each call bounded on large bills; the model is
    # still validated against the full invoice downstream.
    sample = settings.model_authoring_sample_rows
    prompt_target = _build_target(ai_invoice, max_rows=sample)
    extra_examples = extra_examples or []
    additional = [
        {
            "label": ex.intake_id,
            "target": _build_target(ex.ai_invoice, max_rows=sample),
            "page_texts": ex.page_texts,
            "layout_evidence": ex.layout_evidence,
        }
        for ex in extra_examples
    ]
    line_amounts_are_net = not service_items_have_per_line_tax(ai_invoice)

    # Decomposed authoring: a focused SCALARS call (header + totals + self-checks)
    # always, and a LINE-ITEM recipe call only when the layout has NO declared
    # grouping policy. Grouped layouts (Colt, euNetworks, Lumen, …) get their line
    # items deterministically from `_apply_grouping_strategy` below, so the AI's
    # line-item recipe would be discarded — we skip that call entirely.
    grouped = has_grouping_policy(profile.advanced.line_item_hints)
    sections = ["scalars"] if grouped else ["scalars", "line_items"]

    client = get_ai_client(purpose="model_authoring")
    raw: dict = {}
    tokens_in = tokens_out = duration_ms = 0
    for section in sections:
        section_raw, ti, to, dur = _author_section(
            client=client, section=section, field_schema=field_schema,
            page_texts=page_texts, prompt_target=prompt_target, layout_evidence=layout_evidence,
            additional=additional, profile=profile, page_priority=page_priority,
            line_amounts_are_net=line_amounts_are_net, feedback=feedback,
        )
        raw.update(section_raw)
        tokens_in += ti
        tokens_out += to
        duration_ms += dur

    model = build_model_from_rules(
        raw,
        profile=profile,
        fingerprint=fingerprint,
        layout_family_key=layout_family_key,
        intake_id=intake_id,
    )
    model = _apply_profile_authoring_hints(
        model, profile, page_texts=page_texts, target=target,
    )
    # Deterministic safety net: every header/total rule must reproduce its
    # ground-truth value through the runtime lookup; rules that don't are
    # re-synthesized from the layout itself (label cell next to the value).
    _verify_and_repair_rules(model, target, page_texts)
    _ensure_tax_rate_for_net_lines(model, profile, target=target, page_texts=page_texts)

    # Deterministic line items for declared-policy (grouped) layouts: the model
    # replays the SAME layout grouping as the ground truth instead of an
    # AI-authored recipe, so grouped suppliers (Colt, euNetworks, Lumen, …) are
    # reproduced exactly with no AI variance.
    _apply_grouping_strategy(model, profile)

    # Record the full training set this model was authored to reproduce.
    model.training_intake_ids = [intake_id] + [ex.intake_id for ex in extra_examples]

    return AuthoringResult(
        model=model,
        tokens_input=tokens_in,
        tokens_output=tokens_out,
        duration_ms=duration_ms,
        ai_model_name=runtime_settings.openai_model_model_generation(),
    )


def build_model_from_rules(
    raw: dict,
    *,
    profile: SupplierProfile,
    fingerprint: str,
    layout_family_key: str | None,
    intake_id: str,
) -> ExtractionModel:
    """Convert the AI's rule JSON into a persisted ExtractionModel."""
    header_fields = {
        entry["name"]: {k: v for k, v in entry.items() if k != "name"}
        for entry in raw.get("header_fields", [])
    }
    totals = {
        entry["name"]: {k: v for k, v in entry.items() if k != "name"}
        for entry in raw.get("totals", [])
    }

    item_fields = []
    for entry in raw.get("item_fields", []):
        entry = dict(entry)
        transform = dict(entry.get("transform") or {})
        # The strict schema encodes value_map as a list of {from, to} pairs.
        pairs = transform.get("value_map") or []
        if isinstance(pairs, list):
            transform["value_map"] = {pair["from"]: pair["to"] for pair in pairs}
        entry["transform"] = transform
        # Single-invoice induction trap: the AI marks fields required because
        # every item on the TRAINING invoice had them (e.g. billing periods),
        # then future one-off items lacking the field get silently dropped.
        # Only 'amount' defines a line item's existence.
        if entry.get("required") and entry.get("name") != "amount":
            entry["required"] = False
        item_fields.append(entry)

    return ExtractionModel(
        model_id=profile.profile_id,
        supplier_profile_id=profile.profile_id,
        supplier=profile.identity.canonical_name,
        output_type=profile.classification.output_type,
        status=ModelStatus.CANDIDATE,
        layout_fingerprint=fingerprint,
        layout_family_key=layout_family_key,
        header_fields=header_fields,
        region=raw.get("region") or {},
        row_classifiers=raw.get("row_classifiers") or [],
        grouping=raw.get("grouping") or {},
        item_fields=item_fields,
        totals=totals,
        self_checks=raw.get("self_checks") or [],
        confidence=float(raw.get("confidence") or 0.0),
        created_by="ai_authoring",
        created_from_intake=intake_id,
        notes=raw.get("notes"),
    )


_PROFILE_HEADER_FIELDS = (
    "invoice_number",
    "invoice_date",
    "due_date",
    "account_number",
    "ban",
)

_PROFILE_TOTAL_FIELDS = (
    "total_due",
    "subtotal",
)


def _profile_field_label_hint(profile: SupplierProfile, field_name: str) -> str | None:
    from stencil.fields.loader import resolve_merged_field_schema

    field = resolve_merged_field_schema(profile).field_by_name(field_name)
    if field is None or not field.label_hint:
        return None
    return field.label_hint


def _apply_profile_authoring_hints(
    model: ExtractionModel,
    profile: SupplierProfile,
    *,
    page_texts: list[str],
    target: dict,
) -> ExtractionModel:
    """Correct common AI authoring mistakes using profile hints and layout evidence."""
    page_blob = "\n".join(page_texts).lower()

    _reconcile_profile_header_fields(
        model, profile, page_texts=page_texts, target=target,
    )

    for field_name in _PROFILE_TOTAL_FIELDS:
        label = _profile_field_label_hint(profile, field_name)
        if label and field_name in model.totals:
            model.totals[field_name].label = label

    # Net Total / subtotal from line_item_hints when AI omitted subtotal locator
    subtotal_val = (target.get("totals") or {}).get("subtotal")
    if subtotal_val and "subtotal" not in model.totals:
        for keyword in profile.line_item_hints.subtotal_keywords or []:
            if keyword.lower() in page_blob:
                model.totals["subtotal"] = HeaderFieldRule(label=keyword, required=False)
                break

    _reconcile_tax_rate_total(model, profile, page_texts=page_texts, target=target)
    _reconcile_currency_header(model, profile, page_texts=page_texts, target=target)

    ds = profile.document_structure
    # Seed the line-item region anchors from the profile when the AI left them
    # empty (a common cause of "model produced no line items"). Prefer the
    # explicit detail_start_marker, then fall back to the table anchors hint.
    if not model.region.start_anchors:
        seeds: list[str] = []
        if ds.detail_start_marker:
            seeds.append(ds.detail_start_marker)
        for anchor in (profile.line_item_hints.detail_table_anchors or []):
            if anchor and anchor not in seeds:
                seeds.append(anchor)
        if seeds:
            model.region.start_anchors = seeds

    # Net line amounts (no per-line tax): reconcile against subtotal/current_charges
    service_items = [
        li for li in (target.get("line_items") or [])
        if li.get("charge_type") not in ("tax", "fee", "surcharge")
    ]
    has_per_line_tax = any(li.get("tax_amount") is not None for li in service_items)
    _reconcile_self_checks(model, target, has_per_line_tax=has_per_line_tax)

    return model


def _reconcile_self_checks(
    model: ExtractionModel,
    target: dict,
    *,
    has_per_line_tax: bool,
) -> None:
    """Point sum_of_items_equals at net totals when line amounts exclude per-line tax."""
    if has_per_line_tax:
        return
    preferred_field = None
    if (target.get("totals") or {}).get("subtotal") is not None:
        preferred_field = "subtotal"
    elif (target.get("totals") or {}).get("current_charges") is not None:
        preferred_field = "current_charges"
    if not preferred_field:
        return

    for check in model.self_checks:
        if check.check == "sum_of_items_equals":
            check.field = preferred_field
    if not any(c.check == "sum_of_items_equals" for c in model.self_checks):
        model.self_checks.append(
            SelfCheck(check="sum_of_items_equals", field=preferred_field),
        )


def _reconcile_profile_header_fields(
    model: ExtractionModel,
    profile: SupplierProfile,
    *,
    page_texts: list[str],
    target: dict,
) -> None:
    """Prefer profile-mapped labels for header rules — but only when the
    profile-labelled rule actually reproduces the ground-truth value."""
    header_target = target.get("header") or {}

    for field_name in _PROFILE_HEADER_FIELDS:
        label = _profile_field_label_hint(profile, field_name)
        expected = header_target.get(field_name)
        if not label or expected is None:
            continue

        existing = model.header_fields.get(field_name)
        if existing and _header_rule_matches_target(existing, field_name, page_texts, expected):
            if existing.label.lower() != label.lower():
                # Swap to the stable profile label only if the swap still verifies.
                swapped = existing.model_copy(update={"label": label})
                if _header_rule_matches_target(swapped, field_name, page_texts, expected):
                    model.header_fields[field_name] = swapped
            continue

        candidate = _profile_header_rule(label, field_name, page_texts)
        if _header_rule_matches_target(candidate, field_name, page_texts, expected):
            model.header_fields[field_name] = candidate
        # Otherwise leave the AI rule in place; _verify_and_repair_rules will
        # synthesize a working rule from the layout itself.


def _profile_header_rule(label: str, field_name: str, page_texts: list[str]) -> HeaderFieldRule:
    date_format = _infer_page_date_format(label, page_texts) if "date" in field_name else None
    return HeaderFieldRule(label=label, date_format=date_format, required=False)


def _infer_page_date_format(label: str, page_texts: list[str]) -> str | None:
    raw = _find_value_by_label(HeaderFieldRule(label=label), page_texts)
    if raw and _looks_like_dmy_date(raw):
        return "%d/%m/%Y"
    return None


def _header_rule_matches_target(
    rule: HeaderFieldRule,
    field_name: str,
    page_texts: list[str],
    expected,
) -> bool:
    """Verify a header rule through the SAME lookup the runtime uses."""
    _raw, actual = find_header_value(field_name, rule, page_texts)
    return _header_value_matches(field_name, actual, expected)


def _header_value_matches(field_name: str, actual, expected) -> bool:
    from datetime import date

    if expected is None:
        return actual is None
    if "date" in field_name:
        try:
            expected_date = date.fromisoformat(str(expected))
        except ValueError:
            return False
        return isinstance(actual, date) and actual == expected_date
    return actual is not None and str(actual).strip() == str(expected).strip()


def _reconcile_currency_header(
    model: ExtractionModel,
    profile: SupplierProfile,
    *,
    page_texts: list[str],
    target: dict,
) -> None:
    """Ensure currency resolves from the profile label or a literal fallback.

    Colt and similar layouts often print currency on amount lines rather than
    beside a dedicated 'Currency' label. The AI sometimes anchors currency on
    'Total Amount Due' instead — that must not survive authoring.
    """
    currency = (target.get("header") or {}).get("currency")
    if not currency:
        return

    currency_label = _profile_field_label_hint(profile, "currency")
    if currency_label:
        candidate = HeaderFieldRule(label=currency_label, required=False)
        if _header_rule_matches_target(candidate, "currency", page_texts, currency):
            model.header_fields["currency"] = candidate
            return

    model.header_fields["currency"] = HeaderFieldRule(
        label=currency_label or "Currency",
        literal=str(currency),
        required=False,
    )


def _format_tax_rate(rate) -> str:
    """Stable string for a tax rate (avoid float dust from tax/subtotal division)."""
    from decimal import Decimal

    q = Decimal(str(rate)).quantize(Decimal("0.0001"))
    return str(q.normalize())


def _reconcile_tax_rate_total(
    model: ExtractionModel,
    profile: SupplierProfile,
    *,
    page_texts: list[str],
    target: dict,
) -> None:
    """Ensure tax_rate total rule exists when ground truth carries a rate."""
    expected = _target_tax_rate(target, profile)
    if not expected:
        return

    existing = model.totals.get("tax_rate")
    if existing and _total_rule_matches_target("tax_rate", existing, page_texts, expected):
        return

    page_blob = "\n".join(page_texts).lower()
    candidate_labels = [
        "Total VAT",
        "Total Tax",
        "VAT",
        "Tax",
        "Tax Rate",
        "VAT Rate",
        "Tax rate",
    ]
    for keyword in profile.line_item_hints.tax_keywords or []:
        if keyword not in candidate_labels:
            candidate_labels.append(keyword)

    for label in candidate_labels:
        if label.lower() not in page_blob:
            continue
        candidate = HeaderFieldRule(label=label, ignore_percent=False, required=False)
        if _total_rule_matches_target("tax_rate", candidate, page_texts, expected):
            model.totals["tax_rate"] = candidate
            return

    # Colt-style net-tax layouts: the rate may only appear as "VAT 23.00 %" near
    # totals with no dedicated label cell the synthesizer can anchor. A literal
    # rule still lets the runtime populate EXT_TAX (amount × rate).
    model.totals["tax_rate"] = HeaderFieldRule(
        label="Tax Rate", literal=str(expected), required=False,
    )


def _target_has_per_line_tax(target: dict) -> bool:
    service_items = [
        li for li in (target.get("line_items") or [])
        if li.get("charge_type") not in ("tax", "fee", "surcharge")
    ]
    return any(li.get("tax_amount") not in (None, "") for li in service_items)


def _target_tax_rate(target: dict, profile: SupplierProfile | None = None) -> str | None:
    """Rate the delivered output uses for EXT_TAX when line amounts are net."""
    explicit = (target.get("totals") or {}).get("tax_rate")
    if explicit not in (None, ""):
        return str(explicit)
    return None


def _ensure_tax_rate_for_net_lines(
    model: ExtractionModel,
    profile: SupplierProfile,
    *,
    target: dict,
    page_texts: list[str],
) -> None:
    """Last-chance guarantee that net-tax layouts get a working tax_rate rule.

    EXT_TAX is computed at delivery time from invoice tax_rate (or tax/subtotal).
    Deterministic grouped layouts do not carry source per-line tax, so even
    ground truth with row-level EXT_TAX still needs a resolvable invoice rate.
    """
    expected = _target_tax_rate(target, profile)
    if not expected:
        return
    rule = model.totals.get("tax_rate")
    if rule is not None and _total_rule_matches_target("tax_rate", rule, page_texts, expected):
        return
    model.totals["tax_rate"] = HeaderFieldRule(
        label="Tax Rate", literal=str(expected), required=False,
    )


def _total_rule_matches_target(
    name: str,
    rule: HeaderFieldRule,
    page_texts: list[str],
    expected: str,
) -> bool:
    """Verify a totals rule through the SAME lookup the runtime uses."""
    from decimal import Decimal

    _raw, amount = find_total_value(name, rule, page_texts)
    if amount is None:
        return False
    try:
        return amount == Decimal(str(expected))
    except Exception:
        return False


def _looks_like_dmy_date(value: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", value.strip()))


# ---------------------------------------------------------------------------
# Deterministic verify-and-repair: every header/total rule must reproduce its
# ground-truth value via the runtime lookup; failing rules are re-synthesized
# from the layout (find the value cell, anchor on the label cell next to it).
# ---------------------------------------------------------------------------

_REPAIR_DATE_FORMATS = (
    "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%b. %d, %Y",
    "%B %d, %Y", "%Y-%m-%d", "%d.%m.%Y", "%d-%b-%y", "%d %b %y", "%d/%m/%y",
)

_REPAIRABLE_HEADER_FIELDS = (
    "invoice_number", "invoice_date", "due_date", "account_number", "ban",
    "billing_period_start", "billing_period_end", "po_number",
)
_REPAIRABLE_TOTALS = ("subtotal", "tax", "fees", "total_due", "current_charges", "tax_rate")


def _verify_and_repair_rules(model: ExtractionModel, target: dict, page_texts: list[str]) -> list[str]:
    """Verify every header/total rule against ground truth; repair what fails.

    Returns the list of repaired rule names (for logging/tests). Rules that
    cannot be repaired are left as authored — training validation will catch
    them and surface the diff for human review.
    """
    repaired: list[str] = []
    known_labels = _model_known_labels(model)

    header_target = target.get("header") or {}
    for name in _REPAIRABLE_HEADER_FIELDS:
        expected = header_target.get(name)
        if expected in (None, ""):
            continue
        rule = model.header_fields.get(name)
        if rule is not None:
            _raw, value = find_header_value(name, rule, page_texts, known_labels=known_labels)
            if _header_value_matches(name, value, expected):
                continue
        new_rule = _synthesize_header_rule(name, str(expected), page_texts, known_labels, prior=rule)
        if new_rule is not None:
            model.header_fields[name] = new_rule
            known_labels = _model_known_labels(model)
            repaired.append(name)
        elif rule is not None and not rule.required:
            # A wrong optional rule is worse than no rule: it fills the field
            # with a wrong value silently. Drop it.
            del model.header_fields[name]
            repaired.append(f"{name} (dropped)")

    expected_currency = header_target.get("currency")
    if expected_currency:
        rule = model.header_fields.get("currency")
        if rule is None or not _header_rule_matches_target(
            rule, "currency", page_texts, expected_currency,
        ):
            model.header_fields["currency"] = HeaderFieldRule(
                label="Currency",
                literal=str(expected_currency),
                required=False,
            )
            repaired.append("currency")
        elif rule.required:
            model.header_fields["currency"] = rule.model_copy(update={"required": False})
            repaired.append("currency (optional)")

    totals_target = target.get("totals") or {}
    for name in _REPAIRABLE_TOTALS:
        expected = totals_target.get(name)
        if expected in (None, ""):
            continue
        rule = model.totals.get(name)
        if rule is not None and _total_rule_matches_target(name, rule, page_texts, expected):
            continue
        new_rule = _synthesize_total_rule(name, str(expected), page_texts, known_labels, prior=rule)
        if new_rule is not None:
            model.totals[name] = new_rule
            known_labels = _model_known_labels(model)
            repaired.append(f"totals.{name}")
        elif rule is not None:
            del model.totals[name]
            repaired.append(f"totals.{name} (dropped)")

    if repaired:
        logger.info("authoring.rules_repaired", fields=repaired)
    return repaired


def _synthesize_header_rule(
    name: str,
    expected: str,
    page_texts: list[str],
    known_labels: frozenset[str],
    prior: HeaderFieldRule | None,
) -> HeaderFieldRule | None:
    is_date = "date" in name
    needles = _printed_needles(expected, is_date=is_date)

    def verify(rule: HeaderFieldRule) -> bool:
        _raw, value = find_header_value(name, rule, page_texts, known_labels=known_labels)
        return _header_value_matches(name, value, expected)

    return _synthesize_rule(needles, page_texts, prior, verify)


def _synthesize_total_rule(
    name: str,
    expected: str,
    page_texts: list[str],
    known_labels: frozenset[str],
    prior: HeaderFieldRule | None,
) -> HeaderFieldRule | None:
    needles = _printed_amount_needles(expected, is_rate=(name == "tax_rate"))

    def verify(rule: HeaderFieldRule) -> bool:
        return _total_rule_matches_target(name, rule, page_texts, expected)

    return _synthesize_rule(needles, page_texts, prior, verify)


def _synthesize_rule(
    needles: dict[str, str | None],
    page_texts: list[str],
    prior: HeaderFieldRule | None,
    verify,
) -> HeaderFieldRule | None:
    """Find the value on the page, anchor a rule on the label cell next to it,
    and keep the first candidate that verifies through the runtime lookup."""
    for page_idx, page_text in enumerate(page_texts):
        lines = page_text.splitlines()
        for line_idx, line in enumerate(lines):
            cells = _line_cells(line)
            for cell_idx, (text, _x0, _x1) in enumerate(cells):
                date_format = _needle_in_text(text, needles)
                if date_format is _NO_MATCH:
                    continue
                for candidate in _candidate_rules_for_value(
                    lines, line_idx, cells, cell_idx, page_idx,
                    date_format=date_format, prior=prior,
                ):
                    if verify(candidate):
                        return candidate
    return None


_NO_MATCH = object()


def _needle_in_text(text: str, needles: dict[str, str | None]):
    """Return the date_format of the matching needle (None for non-dates),
    or _NO_MATCH when the cell doesn't contain the value."""
    normalized = _norm_text(text)
    for printed, date_format in needles.items():
        if _norm_text(printed) in normalized:
            return date_format
    return _NO_MATCH


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace(",", "")).strip().lower()


def _candidate_rules_for_value(
    lines: list[str],
    line_idx: int,
    cells: list[tuple[str, float | None, float | None]],
    cell_idx: int,
    page_idx: int,
    *,
    date_format: str | None,
    prior: HeaderFieldRule | None,
):
    """Yield label-anchored rule candidates for a located value cell."""
    required = prior.required if prior is not None else False
    page = page_idx + 1
    value_text, value_x0, value_x1 = cells[cell_idx]

    def make(label: str, position: str) -> HeaderFieldRule | None:
        label = _clean_label(label)
        if not label or _norm_text(label) in _norm_text(value_text):
            return None
        return HeaderFieldRule(
            label=label, value_position=position, date_format=date_format,
            page=page, required=required,
        )

    # 1. Label to the left on the same row.
    for text, _x0, _x1 in reversed(cells[:cell_idx]):
        if _is_label_like(text):
            candidate = make(text, "right")
            if candidate:
                yield candidate
            break

    # 2. Label in the x-aligned cell of the previous non-empty line (table style).
    prev_line = _prev_non_empty_line(lines, line_idx)
    if prev_line and value_x0 is not None and value_x1 is not None:
        above = _aligned_label(_line_cells(prev_line), value_x0, value_x1)
        if above and _is_label_like(above):
            candidate = make(above, "below")
            if candidate:
                yield candidate

    # 3. Label prefix inside the same cell ("Invoice No: 12345") — take the
    # text before the first digit as a crude in-cell label.
    if value_text:
        match = re.match(r"^([^\d]{3,60}?)[:#\s]*\d", value_text)
        if match and _is_label_like(match.group(1)):
            candidate = make(match.group(1), "right")
            if candidate:
                yield candidate


def _aligned_label(
    cells: list[tuple[str, float | None, float | None]],
    x0: float,
    x1: float,
) -> str | None:
    best_text, best_overlap = None, 0.0
    for text, cx0, cx1 in cells:
        if not text or cx0 is None or cx1 is None:
            continue
        overlap = min(x1, cx1) - max(x0, cx0)
        if overlap > best_overlap:
            best_text, best_overlap = text, overlap
    return best_text


def _prev_non_empty_line(lines: list[str], idx: int) -> str | None:
    for line in reversed(lines[:idx]):
        if line.strip():
            return line
    return None


def _is_label_like(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 3 or len(stripped) > 60:
        return False
    if not re.search(r"[A-Za-z]", stripped):
        return False
    # A label should not itself be an amount or a date.
    if re.fullmatch(r"[\d.,\s%$€£()-]+", stripped):
        return False
    return True


def _clean_label(label: str) -> str:
    cleaned = re.sub(r"\s+", " ", label).strip().strip(":#").strip()
    return cleaned[:60]


def _printed_needles(expected: str, *, is_date: bool) -> dict[str, str | None]:
    """Printed-form needles for a ground-truth value -> date_format used."""
    if not is_date:
        return {expected: None}
    from datetime import date

    try:
        value = date.fromisoformat(expected)
    except ValueError:
        return {expected: None}
    needles: dict[str, str | None] = {}
    for fmt in _REPAIR_DATE_FORMATS:
        printed = value.strftime(fmt)
        needles.setdefault(printed, fmt)
        # Non-zero-padded variants (e.g. 1/2/2024).
        unpadded = printed.lstrip("0").replace("/0", "/").replace("-0", "-").replace(" 0", " ")
        needles.setdefault(unpadded, fmt)
    needles.setdefault(expected, "%Y-%m-%d")
    return needles


def _printed_amount_needles(expected: str, *, is_rate: bool) -> dict[str, str | None]:
    from decimal import Decimal, InvalidOperation

    try:
        value = Decimal(expected)
    except (InvalidOperation, ValueError):
        return {expected: None}
    if is_rate:
        rate = value * 100 if value <= 1 else value
        return {
            f"{rate:.2f}": None,
            f"{rate:.1f}": None,
            f"{rate:.0f}": None,
        }
    return {
        f"{value:,.2f}": None,
        f"{value:.2f}": None,
    }


def _apply_grouping_strategy(model: ExtractionModel, profile: SupplierProfile) -> None:
    """Attach a deterministic grouping strategy when the profile declares one."""
    from stencil.extraction.normalization import has_grouping_policy
    from stencil.models.schema import LineItemStrategy

    hints = profile.line_item_hints
    if not has_grouping_policy(hints):
        return
    anchors = list(hints.detail_table_anchors or [])
    if profile.document_structure.detail_start_marker:
        anchors.append(profile.document_structure.detail_start_marker)
    model.line_item_strategy = LineItemStrategy(
        granularity=hints.line_item_granularity,
        service_id_preference=hints.service_id_preference,
        billing_reference_preference=hints.billing_reference_preference,
        anchors=anchors,
        skip_keywords=list(hints.skip_row_keywords or []),
    )


def _build_target(invoice: CanonicalInvoice, *, max_rows: int | None = None) -> dict:
    """Compact ground truth the rule document must reproduce.

    ``max_rows`` caps the line items to a representative sample for the authoring
    PROMPT only (keeps the prompt bounded on large bills); the deterministic
    repair/self-check passes use the full target so nothing downstream changes.
    """
    derived_rate = resolve_tax_rate(invoice)
    items = invoice.line_items
    if max_rows is not None:
        from stencil.models.authoring_context import sample_representative_rows
        items = [invoice.line_items[i] for i in sample_representative_rows(invoice, max_rows)]
    return {
        "header": {
            "invoice_number": invoice.header.invoice_number,
            "invoice_date": invoice.header.invoice_date.isoformat() if invoice.header.invoice_date else None,
            "due_date": invoice.header.due_date.isoformat() if invoice.header.due_date else None,
            "account_number": invoice.header.account_number,
            "ban": invoice.header.ban,
            "currency": invoice.header.currency,
        },
        "totals": {
            "subtotal": str(invoice.subtotal) if invoice.subtotal is not None else None,
            "tax": str(invoice.tax) if invoice.tax is not None else None,
            "fees": str(invoice.fees) if invoice.fees is not None else None,
            "total_due": str(invoice.total_due) if invoice.total_due is not None else None,
            "tax_rate": (
                str(invoice.tax_rate) if invoice.tax_rate is not None
                else (_format_tax_rate(derived_rate) if derived_rate is not None else None)
            ),
            "current_charges": str(invoice.current_charges) if invoice.current_charges is not None else None,
        },
        "line_items": [
            {
                "service_id": item.service_id,
                "billing_reference": item.billing_reference,
                "description": item.description,
                "charge_type": str(item.charge_type),
                "amount": str(item.amount),
                "tax_amount": str(item.tax_amount) if item.tax_amount is not None else None,
                "currency": item.currency,
                "billing_period_start": item.billing_period_start.isoformat() if item.billing_period_start else None,
                "billing_period_end": item.billing_period_end.isoformat() if item.billing_period_end else None,
                "quantity": str(item.quantity) if item.quantity is not None else None,
                "unit_rate": str(item.unit_rate) if item.unit_rate is not None else None,
                "source_page": item.source_page,
            }
            for item in items
        ],
    }
