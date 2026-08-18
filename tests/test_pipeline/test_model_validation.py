"""Tests for model-vs-AI exact output matching (candidate validation gate)."""

from datetime import date
from decimal import Decimal

from stencil.pipeline.processor import _model_matches_ai
from stencil.validation.schema import (
    CanonicalInvoice,
    ExtractionMetadata,
    ExtractionPath,
    InvoiceHeader,
    LineItem,
    OutputType,
)


def _invoice(*, invoice_date=date(2024, 2, 1), amounts=("3996.34", "3996.34"),
             tax_amounts=("919.16", "919.16"), invoice_number="INV-1",
             account="5-HLBHFCGL") -> CanonicalInvoice:
    items = []
    for i, (amt, tax) in enumerate(zip(amounts, tax_amounts), start=1):
        items.append(LineItem(
            line_number=i,
            service_id=f"S{i}",
            billing_reference=f"S{i}",
            description="Wavelengths",
            charge_type="recurring",
            amount=Decimal(amt),
            tax_amount=Decimal(tax) if tax is not None else None,
        ))
    return CanonicalInvoice(
        intake_id="t1",
        output_type=OutputType.STANDARD,
        header=InvoiceHeader(
            supplier_name="Colt",
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            account_number=account,
        ),
        line_items=items,
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )


# Per-line tax is read from each invoice automatically.


def test_identical_output_matches():
    ai = _invoice()
    model = _invoice()
    ok, reason = _model_matches_ai(model, ai)
    assert ok is True
    assert reason == "exact match"


def test_tax_mismatch_blocks():
    ai = _invoice(tax_amounts=("919.16", "919.16"))
    model = _invoice(tax_amounts=("11.50", "11.50"))  # the Colt bug
    ok, reason = _model_matches_ai(model, ai)
    assert ok is False
    assert "differ" in reason


def test_date_mismatch_blocks():
    ai = _invoice(invoice_date=date(2024, 2, 1))
    model = _invoice(invoice_date=date(2026, 6, 9))  # today() fallback bug
    ok, reason = _model_matches_ai(model, ai)
    assert ok is False


def test_row_count_mismatch_blocks():
    ai = _invoice(amounts=("3996.34", "3996.34"), tax_amounts=("919.16", "919.16"))
    model = _invoice(amounts=("3996.34",), tax_amounts=("919.16",))
    ok, reason = _model_matches_ai(model, ai)
    assert ok is False
    assert "expected 2 row(s), got 1" in reason


def test_row_order_does_not_matter():
    ai = _invoice(amounts=("100.00", "200.00"), tax_amounts=("23.00", "46.00"))
    model = _invoice(amounts=("200.00", "100.00"), tax_amounts=("46.00", "23.00"))
    # service_id S1/S2 are tied to position, so swap them to keep rows identical as a set
    model.rows[0]["service_id"] = "S2"
    model.rows[0]["billing_reference"] = "S2"
    model.rows[1]["service_id"] = "S1"
    model.rows[1]["billing_reference"] = "S1"
    ok, _ = _model_matches_ai(model, ai)
    assert ok is True
