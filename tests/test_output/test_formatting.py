"""Tests for deliverable date formatting."""

from datetime import date

from stencil.output.formatting import format_deliverable_date, format_deliverable_invoice_dates
from stencil.output.json_writer import write_canonical_json
from stencil.validation.schema import (
    CanonicalInvoice,
    ExtractionMetadata,
    ExtractionPath,
    InvoiceHeader,
    LineItem,
    OutputType,
)


def _invoice() -> CanonicalInvoice:
    return CanonicalInvoice(
        intake_id="test",
        output_type=OutputType.STANDARD,
        header=InvoiceHeader(
            supplier_name="Test",
            invoice_number="INV-1",
            invoice_date=date(2025, 12, 2),
            due_date=date(2026, 1, 1),
        ),
        line_items=[
            LineItem(
                line_number=1,
                service_id="S1",
                billing_reference="C1",
                description="Svc",
                charge_type="recurring",
                amount="100",
                billing_period_start=date(2025, 11, 1),
                billing_period_end=date(2025, 11, 30),
            ),
        ],
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )


class TestFormatDeliverableDate:
    def test_date_object(self):
        assert format_deliverable_date(date(2025, 12, 2)) == "12/02/2025"

    def test_iso_string(self):
        assert format_deliverable_date("2025-12-02") == "12/02/2025"

    def test_none(self):
        assert format_deliverable_date(None) is None


class TestFormatDeliverableInvoiceDates:
    def test_rewrites_header_and_line_item_dates(self):
        data = _invoice().model_dump(mode="json")
        formatted = format_deliverable_invoice_dates(data)
        assert formatted["fields"]["invoice_date"] == "12/02/2025"
        assert formatted["fields"]["due_date"] == "01/01/2026"
        assert formatted["rows"][0]["billing_period_start"] == "11/01/2025"
        assert formatted["rows"][0]["billing_period_end"] == "11/30/2025"


class TestWriteCanonicalJson:
    def test_writes_iso_dates(self, tmp_path):
        out = tmp_path / "canonical.json"
        write_canonical_json(_invoice(), out)
        text = out.read_text(encoding="utf-8")
        assert '"invoice_date": "2025-12-02"' in text
        assert '"due_date": "2026-01-01"' in text
        assert '"billing_period_start": "2025-11-01"' in text
