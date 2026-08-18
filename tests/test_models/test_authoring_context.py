"""Representative row sampling for bounded authoring prompts."""

from datetime import date
from decimal import Decimal

from stencil.models.authoring_context import _row_signature, sample_representative_rows
from stencil.validation.schema import (
    CanonicalInvoice,
    ExtractionMetadata,
    ExtractionPath,
    InvoiceHeader,
    LineItem,
    OutputType,
)


def _li(n: int, **kw) -> LineItem:
    defaults = dict(line_number=n, service_id=f"S{n}", billing_reference=None,
                    description="svc", charge_type="recurring", amount=Decimal("10.00"))
    defaults.update(kw)
    return LineItem(**defaults)


def _invoice(items: list[LineItem]) -> CanonicalInvoice:
    return CanonicalInvoice(
        intake_id="t1", output_type=OutputType.STANDARD,
        header=InvoiceHeader(supplier_name="X", invoice_number="INV-1",
                             invoice_date=date(2026, 1, 1), account_number="A1"),
        line_items=items,
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )


def test_returns_all_when_under_cap():
    inv = _invoice([_li(i) for i in range(1, 6)])
    assert sample_representative_rows(inv, 25) == [0, 1, 2, 3, 4]


def test_zero_cap_returns_all():
    inv = _invoice([_li(i) for i in range(1, 10)])
    assert sample_representative_rows(inv, 0) == list(range(9))


def test_caps_and_covers_every_distinct_shape():
    # 40 plain recurring rows, 5 tax rows (no service_id), 5 per-line-tax rows.
    items = [_li(i) for i in range(1, 41)]
    items += [_li(40 + i, charge_type="tax", service_id=None) for i in range(1, 6)]
    items += [_li(45 + i, tax_amount=Decimal("2.10")) for i in range(1, 6)]
    inv = _invoice(items)

    idx = sample_representative_rows(inv, 25)
    assert len(idx) == 25  # capped (50 rows > 25)
    assert idx == sorted(idx)
    # Every distinct row shape is represented in the sample.
    line_items = inv.line_items
    covered = {_row_signature(line_items[i]) for i in idx}
    all_shapes = {_row_signature(it) for it in line_items}
    assert covered == all_shapes
