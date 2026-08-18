"""Build OpenAI strict JSON schemas from FieldSchema definitions."""

from __future__ import annotations

from stencil.fields.schema import FieldDef, FieldSchema, FieldScope, FieldType


def _nullable(schema: dict) -> dict:
    t = schema["type"]
    if isinstance(t, list):
        return schema
    schema = dict(schema)
    schema["type"] = [t, "null"]
    return schema


def _json_type_for_field(field: FieldDef) -> dict:
    if field.name == "supplier_aliases":
        return {"type": "array", "items": {"type": "string"}}
    if field.type == FieldType.STRING:
        return {"type": "string"}
    if field.type == FieldType.DATE:
        return {"type": "string", "description": "YYYY-MM-DD"}
    if field.type == FieldType.INTEGER:
        return _nullable({"type": "integer"})
    if field.type in (FieldType.NUMBER, FieldType.CURRENCY):
        if field.scope == FieldScope.ROW and field.name in ("amount", "line_number", "confidence"):
            return {"type": "number"} if field.name == "amount" else (
                {"type": "integer"} if field.name == "line_number" else {"type": "number"}
            )
        return _nullable({"type": "number"})
    if field.type == FieldType.ENUM and field.enum_values:
        base = {"type": "string", "enum": field.enum_values}
        if field.name == "charge_type":
            return base
        return _nullable(base)
    return {"type": "string"}


def _object_schema(fields: list[FieldDef], *, required_all: bool = True) -> dict:
    properties = {f.name: _json_type_for_field(f) for f in fields}
    required = [f.name for f in fields] if required_all else [f.name for f in fields if f.required]
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def build_extraction_json_schema(field_schema: FieldSchema) -> dict:
    """Build OpenAI strict JSON schema for AI extraction from a FieldSchema."""
    doc_fields = field_schema.document_fields()
    row_fields = field_schema.row_fields()

    # AI response uses legacy nested shape: header + line_items + document totals
    header_names = {
        "supplier_name", "supplier_aliases", "invoice_number", "invoice_date",
        "due_date", "account_number", "ban", "billing_period_start", "billing_period_end",
        "currency", "po_number", "payment_terms", "source_page", "confidence",
    }
    header_fields = [f for f in doc_fields if f.name in header_names]
    total_fields = [f for f in doc_fields if f.name not in header_names and f.name != "output_type"]

    properties: dict = {
        "header": _object_schema(header_fields),
        "line_items": {
            "type": "array",
            "items": _object_schema(row_fields),
        },
        "overall_confidence": {"type": "number"},
    }
    required = ["header", "line_items", "overall_confidence"]

    for f in total_fields:
        properties[f.name] = _json_type_for_field(f)
        required.append(f.name)

    return {
        "name": "document_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }
