"""Tests for model review artifact writing."""

from datetime import date
from decimal import Decimal

from stencil.output.review_artifacts import write_model_review_artifacts
from stencil.validation.schema import (
    CanonicalInvoice,
    ExtractionMetadata,
    ExtractionPath,
    InvoiceHeader,
    LineItem,
    OutputType,
)


def _invoice(**kw) -> CanonicalInvoice:
    defaults = dict(
        intake_id="review-test",
        output_type=OutputType.STANDARD,
        header=InvoiceHeader(
            supplier_name="TestCo",
            invoice_number="INV-1",
            invoice_date=date(2025, 1, 1),
            account_number="ACC-1",
        ),
        line_items=[
            LineItem(
                line_number=1,
                service_id="S1",
                description="Service",
                charge_type="recurring",
                amount=Decimal("100.00"),
                tax_amount=Decimal("23.00"),
            ),
        ],
        tax_rate=Decimal("0.23"),
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )
    defaults.update(kw)
    return CanonicalInvoice(**defaults)


def test_writes_ai_and_model_xlsx(tmp_path, monkeypatch):
    from stencil.config import settings

    monkeypatch.setattr(settings, "completed_dir", tmp_path)

    ai = _invoice()
    model = _invoice(line_items=[
        LineItem(
            line_number=1,
            service_id="S1",
            description="Service",
            charge_type="recurring",
            amount=Decimal("100.00"),
            tax_amount=Decimal("99.99"),
        ),
    ])

    written = write_model_review_artifacts("intake-123", ai, model)

    review_dir = tmp_path / "intake-123" / "model_review"
    assert set(written) == {"ai_output.xlsx", "model_output.xlsx", "model_output.json"}
    assert (review_dir / "ai_output.xlsx").exists()
    assert (review_dir / "model_output.xlsx").exists()
    assert (review_dir / "model_output.json").exists()


def test_writes_ai_only_when_model_missing(tmp_path, monkeypatch):
    from stencil.config import settings

    monkeypatch.setattr(settings, "completed_dir", tmp_path)

    written = write_model_review_artifacts("intake-456", _invoice(), None)

    assert written == ["ai_output.xlsx"]
    assert (tmp_path / "intake-456" / "model_review" / "ai_output.xlsx").exists()
