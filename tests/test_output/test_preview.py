"""Unit tests for the in-app output preview payload."""

import json
from datetime import date
from decimal import Decimal

from stencil.output.preview import build_preview
from stencil.validation.schema import (
    CanonicalInvoice,
    ExtractionMetadata,
    ExtractionPath,
    InvoiceHeader,
    LineItem,
    OutputType,
)


def _invoice() -> CanonicalInvoice:
    inv = CanonicalInvoice(
        intake_id="prev-001",
        output_type=OutputType.STANDARD,
        header=InvoiceHeader(
            supplier_name="TestSupplier",
            invoice_number="INV-001",
            invoice_date=date(2025, 12, 2),
            due_date=date(2026, 1, 1),
            account_number="A837737",
            currency="EUR",
        ),
        line_items=[
            LineItem(line_number=1, service_id="S1", billing_reference="C1",
                     description="svc", charge_type="recurring", amount=Decimal("100.00")),
            LineItem(line_number=2, service_id="S2", billing_reference="C2",
                     description="svc", charge_type="recurring", amount=Decimal("200.00")),
        ],
        subtotal=Decimal("300.00"),
        tax=Decimal("60.00"),
        total_due=Decimal("360.00"),
        tax_rate=Decimal("0.20"),
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )
    inv.fields["_tax_output_mode"] = "calculate"
    inv.fields["_tax_rate_source"] = "invoice_tax_rate"
    return inv


def test_preview_default_columns_and_rows():
    payload = build_preview(_invoice())
    headers = [c["header"] for c in payload["columns"]]
    assert headers == [
        "EXT_SERVICEID", "EXT_BILLINGREFERENCE", "EXT_DATE", "formula",
        "EXT_AMOUNT", "EXT_ACCOUNT", "EXT_INVOICENUMBER", "EXT_TAX",
    ]
    assert payload["row_count"] == 2
    # Each row is parallel to columns.
    assert all(len(row) == len(payload["columns"]) for row in payload["rows"])


def test_preview_computes_line_tax_from_rate():
    payload = build_preview(_invoice())
    # EXT_TAX is the last column = amount * tax_rate (0.20): 100→20, 200→40.
    tax_values = [row[-1] for row in payload["rows"]]
    assert tax_values == [20.0, 40.0]
    # EXT_AMOUNT (index 4) is numeric.
    assert [row[4] for row in payload["rows"]] == [100.0, 200.0]


def test_preview_header_and_totals_are_json_safe():
    payload = build_preview(_invoice())
    header = {f["label"]: f["value"] for f in payload["header_fields"]}
    assert header["Supplier"] == "TestSupplier"
    assert header["Invoice Date"] == "12/02/2025"  # formatted, not a date object
    totals = {t["label"]: t["value"] for t in payload["totals"]}
    assert totals["Total Due"] == 360.0
    # The whole payload must serialize cleanly (no Decimal/date leaks).
    json.dumps(payload)
