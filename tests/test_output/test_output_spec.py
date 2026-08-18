"""Customer-configurable OutputSpec: column set + computed fields + diff keying."""

from datetime import date
from decimal import Decimal

from stencil.fields.loader import default_field_schema
from stencil.models.diff import diff_invoices
from stencil.output.spec import OutputColumn, OutputSpec
from stencil.output.xlsx_writer import build_output_rows
from stencil.profiles.schema import AdvancedHints, LineItemHints, SupplierIdentity, SupplierProfile
from stencil.specs.loader import (
    resolve_output_spec,
    validate_output_spec,
    validate_profile_output_mapping,
)
from stencil.validation.schema import (
    CanonicalInvoice,
    ExtractionMetadata,
    ExtractionPath,
    InvoiceHeader,
    LineItem,
)


def _invoice(account: str = "A100", **kw) -> CanonicalInvoice:
    defaults = dict(
        intake_id="t1",
        header=InvoiceHeader(
            supplier_name="Acme",
            invoice_number="INV-1",
            invoice_date=date(2026, 1, 2),
            account_number=account,
        ),
        line_items=[
            LineItem(line_number=1, service_id="S1", billing_reference="B1",
                     description="svc", amount=Decimal("100.00")),
        ],
        tax_rate=Decimal("0.25"),
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )
    defaults.update(kw)
    inv = CanonicalInvoice(**defaults)
    inv.fields["_tax_output_mode"] = "calculate"
    inv.fields["_tax_rate_source"] = "invoice_tax_rate"
    return inv


def _three_col_spec() -> OutputSpec:
    return OutputSpec(
        spec_id="custom.three",
        name="Custom 3-column",
        columns=[
            OutputColumn(header="ID", source="line_item.service_id"),
            OutputColumn(header="AMT", source="line_item.amount", number_format="#,##0.00"),
            OutputColumn(header="TAX", source="computed.line_tax"),
        ],
    )


def test_custom_spec_yields_exactly_its_columns_in_order():
    rows = build_output_rows(_invoice(), _three_col_spec())
    assert len(rows) == 1
    # Exactly 3 columns, in declared order: service_id, amount, computed tax.
    assert rows[0] == ["S1", 100.0, 25.0]  # tax = 100 * 0.25


def test_time_material_optional_cost_fields_are_mappable():
    invoice = _invoice(line_items=[
        LineItem(
            line_number=1,
            service_id="8065237",
            description="Brijesh Dekivadiya",
            amount=Decimal("3683.52"),
            quantity=Decimal("144"),
            unit_rate=Decimal("25.58"),
            plan_cost=Decimal("10.00"),
            equipment_cost=Decimal("20.00"),
        )
    ])
    spec = OutputSpec(
        spec_id="temforce.timeandmaterial",
        columns=[
            OutputColumn(header="EXT_PLANCOST", source="row.plan_cost"),
            OutputColumn(header="EXT_EQUIPMENTCOST", source="row.equipment_cost"),
            OutputColumn(header="EXT_USAGECOST", source="row.unit_rate"),
            OutputColumn(header="EXT_USAGE", source="row.quantity"),
        ],
    )

    assert build_output_rows(invoice, spec) == [[10.0, 20.0, 25.58, 144.0]]


def test_default_spec_still_produces_temforce_eight_columns():
    rows = build_output_rows(_invoice())  # no spec -> temforce default
    assert len(rows[0]) == 8


def test_diff_keys_off_the_passed_spec():
    # Two invoices differ ONLY by account_number.
    a = _invoice(account="A100")
    b = _invoice(account="B999")

    # The default (TemForce) spec includes EXT_ACCOUNT -> they differ.
    assert diff_invoices(a, b).is_match is False

    # A custom spec that omits account -> the model is validated only on the
    # columns it actually delivers, so the same two invoices now match.
    assert diff_invoices(a, b, _three_col_spec()).is_match is True


def test_billing_reference_none_disables_service_id_fallback(monkeypatch):
    spec = OutputSpec(
        spec_id="temforce.standard",
        name="TemForce Standard",
        columns=[
            OutputColumn(header="EXT_SERVICEID", source="row.service_id", fallback="row.billing_reference"),
            OutputColumn(
                header="EXT_BILLINGREFERENCE",
                source="row.billing_reference",
                fallback="row.service_id",
            ),
        ],
    )
    monkeypatch.setattr("stencil.specs.loader.get_output_spec", lambda _spec_id: spec)

    profile = SupplierProfile(
        profile_id="granite.test",
        identity=SupplierIdentity(canonical_name="Granite"),
        advanced=AdvancedHints(
            line_item_hints=LineItemHints(billing_reference_preference="none")
        ),
    )

    resolved = resolve_output_spec(profile)

    assert resolved.columns[0].fallback == "row.billing_reference"
    assert resolved.columns[1].fallback is None


def test_billing_reference_column_label_disables_service_id_fallback(monkeypatch):
    spec = OutputSpec(
        spec_id="temforce.standard",
        name="TemForce Standard",
        columns=[
            OutputColumn(header="EXT_SERVICEID", source="row.service_id", fallback="row.billing_reference"),
            OutputColumn(
                header="EXT_BILLINGREFERENCE",
                source="row.billing_reference",
                fallback="row.service_id",
            ),
        ],
    )
    monkeypatch.setattr("stencil.specs.loader.get_output_spec", lambda _spec_id: spec)

    profile = SupplierProfile(
        profile_id="granite.test",
        identity=SupplierIdentity(canonical_name="Granite"),
        advanced=AdvancedHints(
            line_item_hints=LineItemHints(
                billing_reference_column_label="NAME",
                billing_reference_preference=None,
            )
        ),
    )

    resolved = resolve_output_spec(profile)

    assert resolved.columns[0].fallback == "row.billing_reference"
    assert resolved.columns[1].fallback is None


def test_profile_output_mapping_can_replace_document_date_with_row_date(monkeypatch):
    spec = OutputSpec(
        spec_id="temforce.standard",
        columns=[OutputColumn(header="EXT_DATE", source="field.invoice_date")],
    )
    monkeypatch.setattr("stencil.specs.loader.get_output_spec", lambda _spec_id: spec)
    profile = SupplierProfile(
        profile_id="acme.billing-date",
        identity=SupplierIdentity(canonical_name="Acme"),
        output_mapping_overrides=[{
            "output_header": "EXT_DATE",
            "source": "row.billing_period_start",
            "fallback": "field.invoice_date",
        }],
    )
    invoice = _invoice(line_items=[
        LineItem(
            line_number=1,
            service_id="S1",
            description="service",
            amount=Decimal("100"),
            billing_period_start=date(2026, 2, 1),
        )
    ])

    resolved = resolve_output_spec(profile)

    assert resolved.columns[0].source == "row.billing_period_start"
    assert resolved.columns[0].fallback == "field.invoice_date"
    assert build_output_rows(invoice, resolved) == [["02/01/2026"]]


def test_profile_output_mapping_applies_safe_transform_after_resolution(monkeypatch):
    spec = OutputSpec(
        spec_id="temforce.standard",
        columns=[OutputColumn(header="EXT_ACCOUNT", source="field.account_number")],
    )
    monkeypatch.setattr("stencil.specs.loader.get_output_spec", lambda _spec_id: spec)
    profile = SupplierProfile(
        profile_id="orange.account",
        identity=SupplierIdentity(canonical_name="Orange"),
        output_mapping_overrides=[{
            "output_header": "EXT_ACCOUNT",
            "source": "field.account_number",
            "transforms": ["digits_only"],
        }],
    )

    resolved = resolve_output_spec(profile)

    assert resolved.columns[0].transforms == ["digits_only"]
    assert build_output_rows(_invoice(account="49858-00"), resolved) == [["4985800"]]


def test_profile_output_mapping_can_clear_fallback_and_wins_compatibility(monkeypatch):
    spec = OutputSpec(
        spec_id="temforce.standard",
        columns=[OutputColumn(
            header="EXT_BILLINGREFERENCE",
            source="row.billing_reference",
            fallback="row.service_id",
        )],
    )
    monkeypatch.setattr("stencil.specs.loader.get_output_spec", lambda _spec_id: spec)
    profile = SupplierProfile(
        profile_id="acme.billing-reference",
        identity=SupplierIdentity(canonical_name="Acme"),
        advanced=AdvancedHints(
            line_item_hints=LineItemHints(billing_reference_preference="same_as_service_id")
        ),
        output_mapping_overrides=[{
            "output_header": "EXT_BILLINGREFERENCE",
            "source": "row.service_id",
            "fallback": None,
        }],
    )

    resolved = resolve_output_spec(profile)

    assert resolved.columns[0].source == "row.service_id"
    assert resolved.columns[0].fallback is None


def test_profile_output_mapping_validation_rejects_unknown_duplicate_and_ambiguous(monkeypatch):
    spec = OutputSpec(
        spec_id="duplicate.headers",
        columns=[
            OutputColumn(header="DATE", source="field.invoice_date"),
            OutputColumn(header="DATE", source="field.due_date"),
        ],
    )
    monkeypatch.setattr("stencil.specs.loader.get_output_spec", lambda _spec_id: spec)
    profile = SupplierProfile(
        profile_id="acme.invalid",
        identity=SupplierIdentity(canonical_name="Acme"),
        output_mapping_overrides=[
            {"output_header": "DATE", "source": "row.billing_period_start"},
            {"output_header": "DATE", "source": "row.billing_period_end"},
            {"output_header": "MISSING", "source": "field.not_a_field"},
        ],
    )

    issues = validate_profile_output_mapping(profile, default_field_schema())

    assert any("duplicate overrides" in issue for issue in issues)
    assert any("ambiguous" in issue for issue in issues)
    assert any("unknown column 'MISSING'" in issue for issue in issues)


def test_output_spec_validation_requires_unique_non_empty_headers():
    spec = OutputSpec(
        spec_id="invalid.headers",
        columns=[
            OutputColumn(header="DATE", source="field.invoice_date"),
            OutputColumn(header="DATE", source="field.due_date"),
            OutputColumn(header=" ", source="field.invoice_number"),
        ],
    )

    issues = validate_output_spec(spec)

    assert "every column requires a non-empty header" in issues
    assert "column headers must be unique: DATE" in issues
