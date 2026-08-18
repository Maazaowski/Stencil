import fitz
import pytest

from stencil.extraction.layout import (
    BBox,
    LayoutCell,
    LayoutDocument,
    LayoutPage,
    LayoutWord,
    VisualRow,
    _cluster_words_by_y,
    _infer_row_role,
    _infer_tables_for_page,
    _split_row_into_cells,
    _visual_rows_for_page,
    mark_anchor_rows,
)


def _word(text: str, x0: float, y0: float, x1: float, y1: float) -> LayoutWord:
    return LayoutWord(text=text, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1))


def test_cluster_words_by_y_preserves_visual_row_order():
    words = [
        _word("B", 50, 20.5, 60, 28),
        _word("A", 10, 20, 20, 28),
        _word("C", 10, 40, 20, 48),
    ]

    rows = _cluster_words_by_y(words, tolerance=3)

    assert [[word.text for word in row] for row in rows] == [["A", "B"], ["C"]]


def test_split_row_into_cells_preserves_x_order_and_roles():
    words = [
        _word("442117492", 10, 20, 60, 28),
        _word("Wavelengths", 120, 20, 180, 28),
        _word("3,996.34", 760, 20, 800, 28),
    ]

    cells = _split_row_into_cells(words, row_index=0)

    assert [cell.text for cell in cells] == ["442117492", "Wavelengths", "3,996.34"]
    assert [cell.role for cell in cells] == ["identifier", "text", "amount"]


def test_decimal_amount_without_thousands_separator_is_not_identifier():
    cells = _split_row_into_cells(
        [
            _word("DIA", 10, 20, 35, 28),
            _word("COMMIT", 40, 20, 85, 28),
            _word("1", 300, 20, 310, 28),
            _word("348.56", 760, 20, 800, 28),
        ],
        row_index=0,
    )

    assert [cell.text for cell in cells] == ["DIA COMMIT", "1", "348.56"]
    assert [cell.role for cell in cells] == ["text", "amount", "amount"]


def test_split_row_assigns_stable_cell_ids_and_normalized_boxes():
    cells = _split_row_into_cells(
        [
            _word("442117492", 10, 20, 60, 28),
            _word("3,996.34", 760, 20, 800, 28),
        ],
        row_index=4,
        row_id="p5.r4",
        page_width=800,
        page_height=1000,
    )

    assert [cell.cell_id for cell in cells] == ["p5.r4.c0", "p5.r4.c1"]
    assert cells[0].normalized_bbox.x0 == 12.5
    assert cells[1].normalized_bbox.x1 == 1000


def test_infer_row_roles_for_grouped_service_layout():
    service_start = _split_row_into_cells([_word("442117492", 10, 20, 60, 28)], row_index=1)
    group_total = _split_row_into_cells(
        [_word("Total", 600, 20, 630, 28), _word("442117492", 635, 20, 690, 28),
         _word("3,996.34", 760, 20, 800, 28)],
        row_index=2,
    )

    assert _infer_row_role("442117492", service_start) == "service_start"
    assert _infer_row_role("Total 442117492 3,996.34", group_total) == "group_total"


def test_table_inference_keeps_header_column_bands():
    header = VisualRow(
        row_id="p1.r1",
        page=1,
        row_index=1,
        row_role="header",
        text="Service ID Description Billing Period Units Total",
        bbox=BBox(x0=0, y0=10, x1=500, y1=20),
        cells=[
            LayoutCell(text="Service ID", bbox=BBox(x0=0, y0=10, x1=60, y1=20), row_index=1, column_index=0),
            LayoutCell(text="Description", bbox=BBox(x0=100, y0=10, x1=180, y1=20), row_index=1, column_index=1),
            LayoutCell(text="Total", bbox=BBox(x0=450, y0=10, x1=500, y1=20), row_index=1, column_index=2),
        ],
    )
    total = VisualRow(
        row_id="p1.r2",
        page=1,
        row_index=2,
        row_role="group_total",
        text="Total 442117492 3,996.34",
        bbox=BBox(x0=0, y0=30, x1=500, y1=40),
        cells=[
            LayoutCell(
                text="Total 442117492",
                bbox=BBox(x0=300, y0=30, x1=420, y1=40),
                row_index=2,
                column_index=0,
                role="summary",
            ),
            LayoutCell(
                text="3,996.34",
                bbox=BBox(x0=450, y0=30, x1=500, y1=40),
                row_index=2,
                column_index=1,
                role="amount",
            ),
        ],
    )
    page = LayoutPage(page_id="p1", page_number=1, width=500, height=700, visual_rows=[header, total])

    tables = _infer_tables_for_page(page)

    assert tables[0].header_rows[0].row_id == "p1.r1"
    assert [band.role for band in tables[0].column_bands] == ["service_id", "description", "amount"]


def _rotated_service_table_page(*, set_page_rotation: bool):
    """A real one-page PDF whose service table is rotated 90°, as Lumen prints it.

    Words are drawn bottom-to-top (``dir == (0, -1)``), so PyMuPDF reports each
    one as a tall, narrow box. Two variants occur in the wild and both must be
    derotated: a page carrying ``/Rotate 90``, and a portrait page whose *text*
    is rotated (``/Rotate 0``, so ``page.rotation_matrix`` is the identity).

    Raw y grows leftwards once derotated, so the Service ID column is drawn at
    the largest y and the amounts at the smallest.
    """
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    # (raw_x -> derotated row,  raw_y -> derotated column, right-to-left)
    cells = [
        (100, 740, "Service ID"), (100, 620, "Description"), (100, 500, "Amount"),
        (130, 740, "441992692"), (130, 620, "Access - Off Net"), (130, 500, "16,335.69"),
        (160, 740, "Total 441992692"), (160, 500, "26,275.35"),
        # the NEXT service's identifier: must land on its own row, never glued
        # onto the total row above it.
        (190, 740, "445715812"),
    ]
    for x, y, text in cells:
        page.insert_text((x, y), text, rotate=90, fontsize=8)
    if set_page_rotation:
        page.set_rotation(90)
    return page


@pytest.mark.parametrize("set_page_rotation", [True, False], ids=["page_rotate_90", "internal_rotated_text"])
def test_rotated_service_table_is_derotated_into_visual_rows(set_page_rotation):
    page = _rotated_service_table_page(set_page_rotation=set_page_rotation)

    rows = _visual_rows_for_page(page, page_number=5)
    texts = [row.text for row in rows]

    # One visual row per printed table row, in reading order.
    assert texts == [
        "Service ID Description Amount",
        "441992692 Access - Off Net 16,335.69",
        "Total 441992692 26,275.35",
        "445715812",
    ]
    assert rows[0].row_role == "header"
    assert rows[2].row_role == "group_total"

    # The regression this replaces: the next service's identifier used to be
    # glued onto the previous group's total row.
    assert "445715812" not in rows[2].text
    assert rows[3].text == "445715812"

    # Derotated words read horizontally: a 9-digit id is wider than it is tall.
    id_cell = rows[1].cells[0]
    assert id_cell.text == "441992692"
    assert (id_cell.bbox.x1 - id_cell.bbox.x0) > (id_cell.bbox.y1 - id_cell.bbox.y0)

    # Columns are real: the Service ID column sits left of the Amount column.
    assert rows[1].cells[0].bbox.x0 < rows[1].cells[-1].bbox.x0


def test_derotated_normalized_boxes_stay_within_the_page():
    """/Rotate 90 pages report an already-rotated ``page.rect``; the upright page
    size must come from the transformed mediabox, or normalized x overflows the
    0..1000 scale (a 612-wide page used against 792pt of derotated content)."""
    page = _rotated_service_table_page(set_page_rotation=True)

    rows = _visual_rows_for_page(page, page_number=1)

    for row in rows:
        for cell in row.cells:
            assert 0 <= cell.normalized_bbox.x0 <= 1000
            assert 0 <= cell.normalized_bbox.x1 <= 1000
            assert 0 <= cell.normalized_bbox.y1 <= 1000


def test_upright_page_is_untouched_by_derotation():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Service ID", fontsize=8)
    page.insert_text((300, 100), "16,335.69", fontsize=8)

    rows = _visual_rows_for_page(page, page_number=1)

    assert rows[0].text == "Service ID 16,335.69"
    assert rows[0].cells[0].bbox.x0 < rows[0].cells[-1].bbox.x0


def test_mark_anchor_rows_applies_configured_anchors():
    row = VisualRow(
        row_id="p1.r0",
        page=1,
        row_index=0,
        row_role="detail",
        text="SERVICE LEVEL ACTIVITY",
        bbox=BBox(x0=0, y0=10, x1=200, y1=20),
        cells=[],
    )
    other = VisualRow(
        row_id="p1.r1",
        page=1,
        row_index=1,
        row_role="detail",
        text="Access - Off Net",
        bbox=BBox(x0=0, y0=30, x1=200, y1=40),
        cells=[],
    )
    page = LayoutPage(page_id="p1", page_number=1, width=500, height=700, visual_rows=[row, other])
    document = LayoutDocument(pages=[page])

    mark_anchor_rows(document, ["service level activity"])

    assert row.row_role == "anchor"
    assert other.row_role == "detail"


def _two_column_pdf():
    """A page with independent left/right block stacks separated by a gutter."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    # Left column blocks (x~60), right column blocks (x~360); rows at distinct y
    # so each visual row lives in one column (the newspaper trait).
    y = 100
    for i in range(12):
        page.insert_text((60, y), f"Circuit #: L{i:04d} amount {i + 1}00.00", fontsize=8)
        y += 22
    y = 111
    for i in range(12):
        page.insert_text((360, y), f"Circuit #: R{i:04d} amount {i + 1}50.00", fontsize=8)
        y += 22
    return page


def _single_column_pdf():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    y = 100
    for i in range(18):
        # Each row spans full width (description left, amount right) — a table.
        page.insert_text((60, y), f"Service item number {i} description text", fontsize=8)
        page.insert_text((470, y), f"{i + 1}00.00", fontsize=8)
        y += 22
    return page


def test_no_split_leaves_single_reading_column():
    page = _two_column_pdf()
    rows = _visual_rows_for_page(page, page_number=1)
    assert rows, "expected rows"
    assert all(r.reading_column == 0 for r in rows)


def test_column_split_orders_rows_per_reading_column():
    page = _two_column_pdf()
    # Gutter between the columns (x~305 abs -> ~498 normalized of 612).
    rows = _visual_rows_for_page(page, page_number=1, column_split_x=[498.0])
    cols = {r.reading_column for r in rows}
    assert cols == {0, 1}, cols
    # All column-0 rows are emitted before any column-1 row (reading order).
    seq = [r.reading_column for r in rows]
    assert seq == sorted(seq), "reading columns not contiguous in order"
    left = [r for r in rows if r.reading_column == 0]
    right = [r for r in rows if r.reading_column == 1]
    assert all("L" in r.text for r in left)
    assert all("R" in r.text for r in right)


def test_suggest_gutter_fires_on_two_column_and_not_on_table(tmp_path):
    from stencil.extraction.layout import extract_layout_document, suggest_gutter_for_page

    tc = tmp_path / "two.pdf"
    _two_column_pdf().parent.save(str(tc))
    doc = extract_layout_document(str(tc), include_markdown=False)
    gutter = suggest_gutter_for_page(doc.pages[0])
    assert gutter is not None, "expected a gutter on a two-column page"
    # The suggestion must cleanly separate the two block stacks when applied.
    split = extract_layout_document(str(tc), include_markdown=False, column_split_x=[gutter])
    rows = split.pages[0].visual_rows
    left = [r for r in rows if r.reading_column == 0]
    right = [r for r in rows if r.reading_column == 1]
    assert left and right
    assert all("L" in r.text for r in left)
    assert all("R" in r.text for r in right)

    sc = tmp_path / "single.pdf"
    _single_column_pdf().parent.save(str(sc))
    sdoc = extract_layout_document(str(sc), include_markdown=False)
    assert suggest_gutter_for_page(sdoc.pages[0]) is None
