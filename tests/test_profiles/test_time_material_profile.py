import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from stencil.extraction.instructions import compile_instructions
from stencil.extraction.layout import BBox, LayoutDocument, LayoutPage, VisualRow
from stencil.extraction.normalization import apply_layout_profile_hints
from stencil.fields.loader import _builtin_invoice_standard_schema, merge_field_schema
from stencil.output.spec import OutputSpec
from stencil.output.xlsx_writer import build_output_rows
from stencil.profiles.schema import SupplierProfile
from stencil.specs.loader import validate_spec_against_schema
from stencil.validation.schema import (
    CanonicalInvoice,
    ExtractionMetadata,
    ExtractionPath,
    InvoiceHeader,
    LineItem,
)

ROOT = Path(__file__).resolve().parents[2]


def _profile() -> SupplierProfile:
    raw = json.loads(
        (ROOT / "supplier_profiles_test" / "mindtree.timeandmaterial.json").read_text(encoding="utf-8")
    )
    return SupplierProfile.model_validate(raw)


def _spec() -> OutputSpec:
    return OutputSpec(
        spec_id="temforce.time_and_material",
        columns=[
            {"header": "EXT_SERVICEID", "source": "row.service_id"},
            {"header": "EXT_BILLINGREFERENCE", "source": "row.billing_reference"},
            {"header": "EXT_STARTDATE", "source": "field.billing_period_start"},
            {"header": "EXT_ENDDATE", "source": "field.billing_period_end"},
            {"header": "EXT_AMOUNT", "source": "row.amount"},
            {"header": "EXT_ACCOUNT", "source": "field.account_number"},
        ],
    )


def test_time_material_spec_is_valid_against_merged_profile_schema():
    profile = _profile()
    merged = merge_field_schema(_builtin_invoice_standard_schema(), profile.field_overrides)

    assert validate_spec_against_schema(_spec(), merged) == []


def test_time_material_blueprint_mapping():
    profile = _profile()
    invoice = CanonicalInvoice(
        intake_id="ltm-test",
        header=InvoiceHeader(
            supplier_name="LTM Limited",
            invoice_number="400011523",
            invoice_date=date(2026, 6, 23),
            account_number="000027731",
            po_number="PO-GB20003637",
            currency="USD",
        ),
        line_items=[
            LineItem(
                line_number=1,
                service_id="8065237",
                billing_reference=None,
                description="Brijesh Dekivadiya",
                amount=Decimal("3683.52"),
                quantity=Decimal("144"),
                unit_rate=Decimal("25.58"),
            )
        ],
        tax=Decimal("0"),
        tax_rate=Decimal("0"),
        total_due=Decimal("11459.84"),
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )

    period_row = VisualRow(
        row_id="p1.r1",
        page=1,
        row_index=1,
        text="Billing Period:May 2026",
        bbox=BBox(x0=0, y0=0, x1=200, y1=20),
    )
    layout = LayoutDocument(
        pages=[LayoutPage(page_number=1, width=1000, height=1000, visual_rows=[period_row])]
    )
    apply_layout_profile_hints(invoice, layout, profile)

    assert build_output_rows(invoice, _spec()) == [[
        "8065237",
        None,
        "05/01/2026",
        "05/31/2026",
        3683.52,
        "MindTreeTimeMaterial",
    ]]


def test_time_material_billing_reference_is_employee_name():
    """The Description column is '<7-digit id> - <employee name>'; service_id is the
    id and billing_reference is the name after the hyphen. The AI populates
    billing_reference from the Description column and the value pattern normalizes
    it to the name whether the model returns the full cell or just the name."""
    profile = _profile()
    layout = LayoutDocument(pages=[LayoutPage(page_number=1, width=1000, height=1000, visual_rows=[])])

    def _billing_ref_after_normalize(raw: str | None) -> str | None:
        invoice = CanonicalInvoice(
            intake_id="ltm-br",
            header=InvoiceHeader(
                supplier_name="LTM Limited",
                invoice_number="400011523",
                invoice_date=date(2026, 6, 23),
                currency="USD",
            ),
            line_items=[
                LineItem(
                    line_number=1,
                    service_id="8065237",
                    billing_reference=raw,
                    description="8065237 - Brijesh Dekivadiya",
                    amount=Decimal("3683.52"),
                    quantity=Decimal("144"),
                    unit_rate=Decimal("25.58"),
                )
            ],
            metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
        )
        apply_layout_profile_hints(
            invoice, layout, profile, profile.line_item_hints, preserve_existing_line_items=True
        )
        return invoice.line_items[0].billing_reference

    # AI returns the whole Description cell -> pattern strips the id + hyphen.
    assert _billing_ref_after_normalize("8065237 - Brijesh Dekivadiya") == "Brijesh Dekivadiya"
    # AI returns just the name -> pattern leaves it untouched.
    assert _billing_ref_after_normalize("Brijesh Dekivadiya") == "Brijesh Dekivadiya"


def test_time_material_profile_has_no_note_conflicts():
    assert compile_instructions(_profile().model_dump(mode="json")).conflicts == []
