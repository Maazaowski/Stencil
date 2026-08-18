"""Cell-aware label lookup, percent-skipping totals, and region end semantics."""

from datetime import date
from decimal import Decimal

from stencil.extraction.layout import BBox, LayoutDocument, LayoutPage, VisualRow
from stencil.models.interpreter import (
    _locate_region_rows,
    find_header_value,
    find_total_value,
)
from stencil.models.schema import ExtractionModel, HeaderFieldRule, RegionRule


def _row(row_id: str, *cells: tuple[str, int, int]) -> str:
    parts = [
        f"[{row_id}.c{i} c{i} text x={x0}-{x1}] {text}"
        for i, (text, x0, x1) in enumerate(cells)
    ]
    return f"{row_id} role=detail " + " | ".join(parts)


class TestSameLinePairs:
    """Label and value side by side on one row must not be hijacked by the next row."""

    PAGE = "\n".join([
        "LAYOUT PAGE 1 (595x842) id=p1 (cell x spans normalized to 0-1000 of page width)",
        _row("p1.r0", ("Invoice Date", 30, 150), ("01/02/2024", 700, 800)),
        _row("p1.r1", ("Customer Number", 30, 160), ("5-HLBHFCGL", 700, 800)),
    ])

    def test_same_line_date(self):
        rule = HeaderFieldRule(label="Invoice Date", date_format="%d/%m/%Y")
        raw, value = find_header_value("invoice_date", rule, [self.PAGE])
        assert raw == "01/02/2024"
        assert value == date(2024, 2, 1)

    def test_same_line_dotted_month_date(self):
        page = "\n".join([
            "LAYOUT PAGE 1 (595x842) id=p1 (cell x spans normalized to 0-1000 of page width)",
            _row("p1.r0", ("Payment Due Date", 30, 170), ("Jun. 30, 2026", 700, 830)),
        ])
        rule = HeaderFieldRule(label="Payment Due Date")
        raw, value = find_header_value("due_date", rule, [page])
        assert raw == "Jun. 30, 2026"
        assert value == date(2026, 6, 30)

    def test_same_line_string(self):
        rule = HeaderFieldRule(label="Customer Number")
        raw, value = find_header_value("account_number", rule, [self.PAGE])
        assert value == "5-HLBHFCGL"


class TestTableStyleHeaders:
    """Labels row above values row: alignment by x-span, gated by value type."""

    PAGE = "\n".join([
        "LAYOUT PAGE 1 (595x842) id=p1 (cell x spans normalized to 0-1000 of page width)",
        _row(
            "p1.r0",
            ("Document No.", 30, 140), ("Invoice Date", 250, 360),
            ("Due Date", 470, 560), ("Customer ID", 680, 780),
        ),
        _row(
            "p1.r1",
            ("IE-SI53564", 30, 140), ("02/12/2025", 250, 360),
            ("01/01/2026", 470, 560), ("A837737", 680, 780),
        ),
    ])

    def test_date_gate_skips_neighbour_label(self):
        # The cell right of 'Invoice Date' is 'Due Date' — the date validator
        # must reject it and fall through to the x-aligned value below.
        rule = HeaderFieldRule(label="Invoice Date", date_format="%d/%m/%Y")
        raw, value = find_header_value("invoice_date", rule, [self.PAGE])
        assert value == date(2025, 12, 2)

    def test_rightmost_label_uses_aligned_below(self):
        rule = HeaderFieldRule(label="Customer ID")
        _raw, value = find_header_value("account_number", rule, [self.PAGE])
        assert value == "A837737"

    def test_known_labels_reject_neighbour_for_string_field(self):
        rule = HeaderFieldRule(label="Document No.")
        known = frozenset({"Document No.", "Invoice Date", "Due Date", "Customer ID"})
        _raw, value = find_header_value("invoice_number", rule, [self.PAGE], known_labels=known)
        assert value == "IE-SI53564"


class TestTotalsPercentSkip:
    """A subtotal label near a VAT percentage must never yield the rate (the Colt bug)."""

    PAGE = "\n".join([
        "LAYOUT PAGE 1 (595x842) id=p1 (cell x spans normalized to 0-1000 of page width)",
        _row("p1.r0", ("Subtotal", 30, 120), ("23.00 %", 400, 470)),
        _row("p1.r1", ("7,992.68", 30, 120)),
        _row("p1.r2", ("VAT", 500, 540), ("23.00 %", 600, 660)),
        _row("p1.r3", ("Total Amount Due", 30, 170), ("9,831.00", 700, 790)),
    ])

    def test_subtotal_skips_percent_token(self):
        rule = HeaderFieldRule(label="Subtotal")
        _raw, amount = find_total_value("subtotal", rule, [self.PAGE])
        assert amount == Decimal("7992.68")

    def test_total_due(self):
        rule = HeaderFieldRule(label="Total Amount Due")
        _raw, amount = find_total_value("total_due", rule, [self.PAGE])
        assert amount == Decimal("9831.00")

    def test_tax_rate_keeps_percent(self):
        rule = HeaderFieldRule(label="VAT")
        _raw, amount = find_total_value("tax_rate", rule, [self.PAGE])
        assert amount == Decimal("0.23")

    def test_tax_rate_uses_percent_embedded_in_total_vat_label(self):
        page = "\n".join([
            "LAYOUT PAGE 1 (595x842) id=p1",
            _row("p1.r0", ("Total VAT 25.00%", 30, 180), ("2,995.82", 700, 790)),
        ])
        rule = HeaderFieldRule(label="Total VAT")
        raw, amount = find_total_value("tax_rate", rule, [page])
        assert raw == "25.00%"
        assert amount == Decimal("0.25")

    def test_tax_rate_alias_finds_embedded_vat_rate_when_authored_as_vat_rate(self):
        page = "\n".join([
            "LAYOUT PAGE 1 (595x842) id=p1",
            _row("p1.r0", ("Total VAT 25.00%", 30, 180), ("2,995.82", 700, 790)),
        ])
        rule = HeaderFieldRule(label="VAT Rate")
        raw, amount = find_total_value("tax_rate", rule, [page])
        assert raw == "25.00%"
        assert amount == Decimal("0.25")

    def test_tax_rate_alias_finds_embedded_moms_rate(self):
        page = "\n".join([
            "LAYOUT PAGE 1 (595x842) id=p1",
            _row("p1.r0", ("Total MOMS 25.00%", 30, 180), ("2,995.81", 700, 790)),
        ])
        rule = HeaderFieldRule(label="VAT Rate")
        raw, amount = find_total_value("tax_rate", rule, [page])
        assert raw == "25.00%"
        assert amount == Decimal("0.25")


def _visual_row(page: int, index: int, text: str) -> VisualRow:
    return VisualRow(
        row_id=f"p{page}.r{index}",
        page=page,
        row_index=index,
        text=text,
        bbox=BBox(x0=0, y0=index * 10, x1=500, y1=index * 10 + 8),
    )


def _document() -> LayoutDocument:
    page1 = LayoutPage(
        page_id="p1", page_number=1, width=595, height=842,
        visual_rows=[
            _visual_row(1, 0, "Service Level Activity"),
            _visual_row(1, 1, "ITEM A 100.00"),
            _visual_row(1, 2, "Total excl. VAT 100.00"),
        ],
    )
    page2 = LayoutPage(
        page_id="p2", page_number=2, width=595, height=842,
        visual_rows=[
            _visual_row(2, 0, "Bank Details"),
            _visual_row(2, 1, "IBAN GB00 0000"),
        ],
    )
    return LayoutDocument(pages=[page1, page2])


def _model(region: RegionRule) -> ExtractionModel:
    return ExtractionModel(
        model_id="colt.standard.v1",
        supplier_profile_id="colt.standard.v1",
        supplier="Colt",
        region=region,
    )


class TestRegionEndScope:
    def test_document_scope_stays_closed_on_later_pages(self):
        region = RegionRule(
            start_anchors=["Service Level Activity"],
            end_anchors=["Total excl. VAT"],
            end_scope="document",
        )
        rows = _locate_region_rows(_document(), _model(region))
        assert [row.text for row in rows] == ["ITEM A 100.00"]

    def test_page_scope_resumes_on_next_page(self):
        region = RegionRule(
            start_anchors=["Service Level Activity"],
            end_anchors=["Total excl. VAT"],
            end_scope="page",
        )
        rows = _locate_region_rows(_document(), _model(region))
        assert [row.text for row in rows] == ["ITEM A 100.00", "Bank Details", "IBAN GB00 0000"]

    def test_page_break_never_closes_open_region(self):
        region = RegionRule(
            start_anchors=["Service Level Activity"],
            end_anchors=["No Such Anchor"],
            end_scope="document",
        )
        rows = _locate_region_rows(_document(), _model(region))
        assert [row.text for row in rows] == [
            "ITEM A 100.00", "Total excl. VAT 100.00", "Bank Details", "IBAN GB00 0000",
        ]
