"""Load/save ExtractedDocument artifacts (v1 nested invoice + v2 flat)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stencil.fields.loader import default_field_schema
from stencil.validation.schema import ExtractedDocument, _normalize_v1_payload


def v1_dict_to_extracted(data: dict[str, Any]) -> ExtractedDocument:
    """Convert legacy nested canonical invoice JSON to ExtractedDocument v2."""
    normalized = _normalize_v1_payload(dict(data))
    return ExtractedDocument.model_validate(normalized)


def extracted_to_v1_dict(doc: ExtractedDocument) -> dict[str, Any]:
    """Serialize ExtractedDocument as legacy nested shape (for backward compat)."""
    from stencil.validation.schema import _HEADER_KEYS, _TOTAL_KEYS

    header_names = _HEADER_KEYS
    header = {k: doc.fields[k] for k in header_names if k in doc.fields}
    payload: dict[str, Any] = {
        "schema_version": doc.schema_version,
        "schema_id": doc.schema_id,
        "intake_id": doc.intake_id,
        "output_type": doc.output_type.value if hasattr(doc.output_type, "value") else doc.output_type,
        "header": header,
        "line_items": doc.rows,
        "reconciliation": doc.reconciliation.model_dump(mode="json") if doc.reconciliation else None,
        "metadata": doc.metadata.model_dump(mode="json"),
        "warnings": doc.warnings,
        "exceptions": doc.exceptions,
    }
    for key in _TOTAL_KEYS:
        if key in doc.fields and key != "output_type":
            payload[key] = doc.fields[key]
    return payload


def load_extracted_document(path: Path | str) -> ExtractedDocument:
    """Load v2 flat or v1 nested canonical JSON."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "fields" in data and "rows" in data:
        return ExtractedDocument.model_validate(data)
    return v1_dict_to_extracted(data)


def write_canonical_json(doc: ExtractedDocument, path: Path | str) -> Path:
    """Write ExtractedDocument to canonical_invoice.json (v2 flat shape)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
    return out


def build_extracted_from_ai_raw(
    raw_data: dict[str, Any],
    *,
    intake_id: str,
    field_schema=None,
    metadata=None,
    output_type: str = "standard",
    currency_rules=None,
) -> ExtractedDocument:
    """Map legacy AI nested response into ExtractedDocument."""
    from stencil.fields.coercion import coerce_field_value
    from stencil.fields.currency import normalize_currency_code

    schema = field_schema or default_field_schema()
    fields: dict[str, Any] = {"output_type": output_type}
    warnings = list(raw_data.get("warnings") or [])
    header = raw_data.get("header") or {}
    for fdef in schema.document_fields():
        if fdef.name == "output_type":
            continue
        if fdef.name in header:
            value = coerce_field_value(fdef, header[fdef.name], currency_rules=currency_rules)
            if fdef.name == "currency" and value is None:
                if header[fdef.name]:
                    result = normalize_currency_code(header[fdef.name], rules=currency_rules)
                    if result.warning and result.warning not in warnings:
                        warnings.append(result.warning)
                continue
            fields[fdef.name] = value
        elif fdef.name in raw_data:
            value = coerce_field_value(fdef, raw_data[fdef.name], currency_rules=currency_rules)
            if fdef.name == "currency" and value is None:
                if raw_data[fdef.name]:
                    result = normalize_currency_code(raw_data[fdef.name], rules=currency_rules)
                    if result.warning and result.warning not in warnings:
                        warnings.append(result.warning)
                continue
            fields[fdef.name] = value

    rows: list[dict[str, Any]] = []
    for raw_row in raw_data.get("line_items") or []:
        row: dict[str, Any] = {}
        for fdef in schema.row_fields():
            if fdef.name in raw_row:
                value = coerce_field_value(
                    fdef,
                    raw_row[fdef.name],
                    currency_rules=currency_rules,
                    currency_fallback=fields.get("currency"),
                )
                if fdef.name == "currency" and value is None:
                    if raw_row[fdef.name]:
                        result = normalize_currency_code(
                            raw_row[fdef.name],
                            rules=currency_rules,
                            fallback=fields.get("currency"),
                        )
                        if result.warning and result.warning not in warnings:
                            warnings.append(result.warning)
                    continue
                row[fdef.name] = value
        rows.append(row)

    return ExtractedDocument(
        intake_id=intake_id,
        schema_id=schema.schema_id,
        fields=fields,
        rows=rows,
        metadata=metadata,
        warnings=warnings,
        exceptions=list(raw_data.get("exceptions") or []),
    )
