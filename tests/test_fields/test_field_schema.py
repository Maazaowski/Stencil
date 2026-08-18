"""Tests for field schema registry."""

from stencil.fields.loader import (
    AUTHORING_HEADER_FIELD_NAMES,
    AUTHORING_ITEM_FIELD_NAMES,
    AUTHORING_TOTAL_FIELD_NAMES,
    DEFAULT_FIELD_SCHEMA_ID,
    _builtin_invoice_standard_schema,
    get_field_schema,
    load_all_field_schemas,
    merge_field_schema,
)
from stencil.fields.schema import FieldDef, FieldRole, FieldScope, FieldType


def test_builtin_invoice_schema_id():
    schema = _builtin_invoice_standard_schema()
    assert schema.schema_id == DEFAULT_FIELD_SCHEMA_ID
    assert len(schema.fields) >= 30


def test_invoice_schema_round_trip_json(tmp_path):
    src = _builtin_invoice_standard_schema()
    path = tmp_path / "invoice.standard.json"
    path.write_text(src.model_dump_json(indent=2), encoding="utf-8")
    loaded = load_all_field_schemas(tmp_path)
    assert loaded[DEFAULT_FIELD_SCHEMA_ID].model_dump() == src.model_dump()


def test_authoring_field_parity():
    schema = get_field_schema(DEFAULT_FIELD_SCHEMA_ID)
    doc_names = {f.name for f in schema.document_fields()}
    row_names = {f.name for f in schema.row_fields()}
    for name in AUTHORING_HEADER_FIELD_NAMES:
        assert name in doc_names, name
    for name in AUTHORING_ITEM_FIELD_NAMES:
        assert name in row_names, name
    for name in AUTHORING_TOTAL_FIELD_NAMES:
        assert name in doc_names, name


def test_role_coverage():
    schema = get_field_schema(DEFAULT_FIELD_SCHEMA_ID)
    roles = {f.role for f in schema.fields}
    assert FieldRole.AMOUNT in roles
    assert FieldRole.TOTAL in roles
    assert FieldRole.TAX_RATE in roles
    assert FieldRole.IDENTIFIER in roles


def test_merge_field_schema_overrides_label():
    base = _builtin_invoice_standard_schema()
    override = FieldDef(
        name="invoice_number",
        scope=FieldScope.DOCUMENT,
        type=FieldType.STRING,
        label_hint="Document No.",
        role=FieldRole.IDENTIFIER,
    )
    merged = merge_field_schema(base, [override])
    inv = merged.field_by_name("invoice_number")
    assert inv is not None
    assert inv.label_hint == "Document No."


def test_merge_preserves_same_named_fields_at_both_scopes():
    base = _builtin_invoice_standard_schema()
    override = FieldDef(
        name="billing_period_start",
        scope=FieldScope.DOCUMENT,
        type=FieldType.DATE,
        label_hint="Billing Period",
    )

    merged = merge_field_schema(base, [override])
    matching = [f for f in merged.fields if f.name == "billing_period_start"]

    assert {f.scope for f in matching} == {FieldScope.DOCUMENT, FieldScope.ROW}
    document_field = next(f for f in matching if f.scope == FieldScope.DOCUMENT)
    row_field = next(f for f in matching if f.scope == FieldScope.ROW)
    assert document_field.label_hint == "Billing Period"
    assert row_field.label_hint is None


def test_merge_is_label_only_preserves_structural_contract():
    """A corrupt/migrated override (string/none) must NOT clobber the base
    field's scope/type/role — only the label hint applies. This is the
    regression that broke reconciliation (total_due lost role=total) and
    coercion (currency/date fields became strings)."""
    base = _builtin_invoice_standard_schema()
    corrupt = FieldDef(
        name="total_due",
        scope=FieldScope.DOCUMENT,
        type=FieldType.STRING,   # base is CURRENCY
        role=FieldRole.NONE,     # base is TOTAL
        label_hint="Total Amount Due",
    )
    corrupt_date = FieldDef(
        name="invoice_date",
        scope=FieldScope.DOCUMENT,
        type=FieldType.STRING,   # base is DATE
        role=FieldRole.NONE,
        label_hint="Bill Date",
        date_format="%d/%m/%Y",
    )
    merged = merge_field_schema(base, [corrupt, corrupt_date])

    total = merged.field_by_name("total_due")
    assert total is not None
    assert total.type == FieldType.CURRENCY  # structural contract preserved
    assert total.role == FieldRole.TOTAL     # reconciliation can find it again
    assert total.label_hint == "Total Amount Due"  # hint still applied

    inv_date = merged.field_by_name("invoice_date")
    assert inv_date is not None
    assert inv_date.type == FieldType.DATE   # coercion stays date-aware
    assert inv_date.label_hint == "Bill Date"
    assert inv_date.date_format == "%d/%m/%Y"

    # The role-based lookup the reconciler relies on still resolves.
    assert [f.name for f in merged.fields_with_role(FieldRole.TOTAL)] == ["total_due"]
