"""Tests for non-invoice toy schemas."""

from decimal import Decimal

from stencil.fields.schema import FieldDef, FieldRole, FieldSchema, FieldScope, FieldType
from stencil.fields.schema_builder import build_extraction_json_schema
from stencil.validation.reconciler import reconcile
from stencil.validation.schema import ExtractedDocument, ExtractionMetadata, ExtractionPath


def _purchase_order_schema() -> FieldSchema:
    return FieldSchema(
        schema_id="purchase_order.standard",
        version=1,
        description="Toy PO schema for schema-driven extraction tests",
        fields=[
            FieldDef(
                name="po_number",
                scope=FieldScope.DOCUMENT,
                type=FieldType.STRING,
                role=FieldRole.IDENTIFIER,
                required=True,
            ),
            FieldDef(
                name="vendor_name",
                scope=FieldScope.DOCUMENT,
                type=FieldType.STRING,
                role=FieldRole.NONE,
                required=True,
            ),
            FieldDef(
                name="line_total",
                scope=FieldScope.ROW,
                type=FieldType.CURRENCY,
                role=FieldRole.AMOUNT,
                required=True,
            ),
            FieldDef(
                name="item_description",
                scope=FieldScope.ROW,
                type=FieldType.STRING,
                role=FieldRole.NONE,
                required=True,
            ),
        ],
    )


def test_toy_schema_builds_strict_json_schema():
    schema = _purchase_order_schema()
    built = build_extraction_json_schema(schema)
    json_schema = built["schema"]
    assert json_schema["type"] == "object"
    assert "vendor_name" in json_schema["properties"]
    assert "po_number" in json_schema["properties"]["header"]["properties"]
    row_props = json_schema["properties"]["line_items"]["items"]["properties"]
    assert "line_total" in row_props
    assert "item_description" in row_props


def test_toy_schema_reconcile_skipped_without_total_role():
    schema = _purchase_order_schema()
    doc = ExtractedDocument(
        intake_id="po-1",
        schema_id=schema.schema_id,
        fields={"po_number": "PO-42", "vendor_name": "Acme"},
        rows=[{"line_total": Decimal("10.00"), "item_description": "Widget"}],
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )
    assert reconcile(doc, schema) is None
