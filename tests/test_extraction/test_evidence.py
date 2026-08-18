from datetime import date
from decimal import Decimal

from stencil.extraction.evidence import align_invoice_to_layout
from stencil.extraction.layout import BBox, LayoutCell, LayoutDocument, LayoutPage, VisualRow
from stencil.validation.schema import (
    CanonicalInvoice,
    ExtractionMetadata,
    ExtractionPath,
    InvoiceHeader,
    LineItem,
)


def test_line_item_alignment_prefers_group_total_amount_cell():
    total_row = VisualRow(
        row_id="p1.r3",
        page=1,
        row_index=3,
        row_role="group_total",
        text="Total 442117492 3,996.34",
        bbox=BBox(x0=0, y0=0, x1=100, y1=10),
        cells=[
            LayoutCell(
                cell_id="p1.r3.c0",
                text="Total 442117492",
                bbox=BBox(x0=0, y0=0, x1=70, y1=10),
                row_index=3,
                column_index=0,
                role="summary",
            ),
            LayoutCell(
                cell_id="p1.r3.c1",
                text="3,996.34",
                bbox=BBox(x0=80, y0=0, x1=100, y1=10),
                row_index=3,
                column_index=1,
                role="amount",
            ),
        ],
    )
    document = LayoutDocument(
        pages=[LayoutPage(page_id="p1", page_number=1, width=100, height=100, visual_rows=[total_row])]
    )
    invoice = CanonicalInvoice(
        intake_id="i1",
        header=InvoiceHeader(supplier_name="Any", invoice_number="1", invoice_date=date(2024, 1, 1)),
        line_items=[
            LineItem(
                line_number=1,
                service_id="442117492",
                billing_reference="442117492",
                description="Service",
                amount=Decimal("3996.34"),
            )
        ],
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )

    alignment = align_invoice_to_layout(document, invoice)

    item = alignment["line_items"][0]
    assert item["service_id"]["row_id"] == "p1.r3"
    assert item["amount"]["cell_id"] == "p1.r3.c1"
