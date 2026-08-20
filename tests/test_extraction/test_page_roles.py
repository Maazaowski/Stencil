"""Page classification is the fact that replaces "first five and last two".

Everything here is synthetic: ``eval_cases`` is gitignored client data, so a
test that depended on it would silently skip in CI, which is exactly how the
30-page cap survived.
"""

from stencil.extraction.layout import (
    BBox,
    LayoutCell,
    LayoutDocument,
    LayoutPage,
    VisualRow,
)
from stencil.extraction.page_roles import classify_pages


def _cell(text: str, x0: float, index: int, row_index: int) -> LayoutCell:
    return LayoutCell(
        text=text,
        bbox=BBox(x0=x0, y0=0, x1=x0 + 40, y1=10),
        normalized_bbox=BBox(x0=x0, y0=0, x1=x0 + 40, y1=10),
        row_index=row_index,
        column_index=index,
    )


def _row(page: int, index: int, columns: list[float], *, role: str = "detail",
         money: bool = True) -> VisualRow:
    texts = [f"c{i}" for i in range(len(columns))]
    if money:
        texts[-1] = "120.50"
    cells = [_cell(t, x, i, index) for i, (t, x) in enumerate(zip(texts, columns, strict=True))]
    return VisualRow(
        row_id=f"p{page}.r{index}", page=page, row_index=index,
        text=" ".join(texts),
        bbox=BBox(x0=0, y0=index * 10, x1=1000, y1=index * 10 + 8),
        row_role=role, cells=cells,
    )


def _table_page(number: int, columns: list[float], rows: int = 6, role: str = "detail") -> LayoutPage:
    return LayoutPage(
        page_id=f"p{number}", page_number=number, width=595, height=842,
        visual_rows=[_row(number, i, columns, role=role) for i in range(rows)],
    )


def _prose_page(number: int, lines: int = 4) -> LayoutPage:
    """A page of label/value lines — no table."""
    rows = []
    for i in range(lines):
        rows.append(VisualRow(
            row_id=f"p{number}.r{i}", page=number, row_index=i,
            text=f"Account Number : 0167746{i}",
            bbox=BBox(x0=0, y0=i * 10, x1=500, y1=i * 10 + 8),
            row_role="detail",
            cells=[_cell("Account Number :", 30, 0, i), _cell(f"0167746{i}", 300, 1, i)],
        ))
    return LayoutPage(page_id=f"p{number}", page_number=number, width=595, height=842,
                      visual_rows=rows)


def _blank_page(number: int) -> LayoutPage:
    return LayoutPage(page_id=f"p{number}", page_number=number, width=595, height=842)


COLS = [30.0, 200.0, 500.0, 800.0]
OTHER = [60.0, 300.0, 600.0, 900.0]


def test_a_single_table_page_is_one_run():
    page_map = classify_pages(LayoutDocument(pages=[_table_page(1, COLS)]))
    assert [p.role for p in page_map.pages] == ["detail"]
    assert len(page_map.runs) == 1
    assert page_map.runs[0].pages == (1,)


def test_pages_sharing_a_column_signature_are_one_continuing_table():
    doc = LayoutDocument(pages=[_table_page(n, COLS) for n in (1, 2, 3)])
    page_map = classify_pages(doc)

    assert [p.role for p in page_map.pages] == [
        "detail", "detail_continuation", "detail_continuation",
    ]
    assert len(page_map.runs) == 1
    assert page_map.runs[0].pages == (1, 2, 3)


def test_a_different_column_signature_starts_a_new_table():
    doc = LayoutDocument(pages=[_table_page(1, COLS), _table_page(2, OTHER)])
    page_map = classify_pages(doc)

    assert [p.role for p in page_map.pages] == ["detail", "detail"]
    assert [run.pages for run in page_map.runs] == [(1,), (2,)]


def test_a_gap_breaks_a_run_even_when_the_signature_matches():
    doc = LayoutDocument(pages=[_table_page(1, COLS), _prose_page(2), _table_page(3, COLS)])
    page_map = classify_pages(doc)

    assert [run.pages for run in page_map.runs] == [(1,), (3,)]


def test_a_continuation_page_may_drop_a_column():
    """A column that happens to be blank throughout a page must not split the run."""
    doc = LayoutDocument(pages=[_table_page(1, COLS), _table_page(2, COLS[:3])])
    page_map = classify_pages(doc)

    assert len(page_map.runs) == 1, "a dropped column split the table"
    assert page_map.pages[1].role == "detail_continuation"


def test_leading_prose_is_a_header_and_trailing_prose_is_a_summary():
    doc = LayoutDocument(pages=[
        _prose_page(1), _prose_page(2),
        _table_page(3, COLS), _table_page(4, COLS),
        _prose_page(5),
    ])
    page_map = classify_pages(doc)

    assert [p.role for p in page_map.pages] == [
        "header", "header", "detail", "detail_continuation", "summary",
    ]


def test_an_empty_page_is_noise():
    doc = LayoutDocument(pages=[_prose_page(1), _blank_page(2), _table_page(3, COLS)])
    page_map = classify_pages(doc)
    assert page_map.pages[1].role == "noise"


def test_largest_run_is_the_one_holding_the_most_records():
    """The deliverable table is not necessarily the first or the longest run."""
    doc = LayoutDocument(pages=[
        _table_page(1, OTHER, rows=3),
        _prose_page(2),
        _table_page(3, COLS, rows=40),
        _table_page(4, COLS, rows=40),
    ])
    page_map = classify_pages(doc)

    assert page_map.largest_run.pages == (3, 4)


class TestSampling:
    def _doc(self):
        # Cover, then a 10-page main table, then a small unrelated table.
        pages = [_prose_page(1), _prose_page(2)]
        pages += [_table_page(n, COLS, rows=20) for n in range(3, 13)]
        pages += [_table_page(n, OTHER, rows=3) for n in (13, 14)]
        return LayoutDocument(pages=pages)

    def test_the_sample_leads_with_the_pages_before_the_main_table(self):
        sample = classify_pages(self._doc()).sample_pages(max_pages=8)
        assert 1 in sample and 2 in sample, sample

    def test_the_sample_carries_both_ends_of_the_main_table(self):
        sample = classify_pages(self._doc()).sample_pages(max_pages=8)
        assert 3 in sample, "first page of the main table missing — it holds the column headers"
        assert 12 in sample, "last page of the main table missing — it holds the totals"

    def test_the_sample_includes_interior_continuation_pages(self):
        sample = classify_pages(self._doc()).sample_pages(max_pages=10)
        assert any(3 < page < 12 for page in sample), sample

    def test_the_sample_is_bounded_and_ordered(self):
        for limit in (4, 8, 12):
            sample = classify_pages(self._doc()).sample_pages(max_pages=limit)
            assert len(sample) <= limit
            assert sample == sorted(sample)

    def test_a_tiny_document_samples_everything_it_has(self):
        doc = LayoutDocument(pages=[_prose_page(1), _table_page(2, COLS)])
        assert classify_pages(doc).sample_pages(max_pages=12) == [1, 2]

    def test_classification_is_deterministic(self):
        doc = self._doc()
        runs = [
            [(p.page_number, p.role, p.run_id) for p in classify_pages(doc).pages]
            for _ in range(3)
        ]
        assert runs[0] == runs[1] == runs[2]


def test_an_empty_document_does_not_explode():
    page_map = classify_pages(LayoutDocument(pages=[]))
    assert page_map.pages == []
    assert page_map.runs == []
    assert page_map.largest_run is None
    assert page_map.sample_pages() == []


def test_end_to_end_through_real_pdf_parsing(tmp_path):
    """The hand-built cases above bypass row/cell inference; this one does not.

    Two cover pages, an eight-page charge table, then a closing page.
    """
    import fitz

    from stencil.extraction.layout import extract_layout_document

    path = tmp_path / "structured.pdf"
    doc = fitz.open()

    for n in (1, 2):
        page = doc.new_page(width=612, height=792)
        page.insert_text((60, 100), "Invoice Number:", fontsize=9)
        page.insert_text((300, 100), f"INV-000{n}", fontsize=9)
        page.insert_text((60, 120), "Account Number:", fontsize=9)
        page.insert_text((300, 120), "82824706", fontsize=9)

    for n in range(3, 11):
        page = doc.new_page(width=612, height=792)
        page.insert_text((60, 90), "SERVICE", fontsize=8)
        page.insert_text((240, 90), "LOCATION", fontsize=8)
        page.insert_text((430, 90), "TAX", fontsize=8)
        page.insert_text((520, 90), "AMOUNT", fontsize=8)
        for i in range(12):
            y = 110 + i * 16
            page.insert_text((60, y), f"SVC{n:02d}{i:03d}", fontsize=8)
            page.insert_text((240, y), "Portsmouth, VA", fontsize=8)
            page.insert_text((430, y), "14.43", fontsize=8)
            page.insert_text((520, y), "70.80", fontsize=8)

    closing = doc.new_page(width=612, height=792)
    closing.insert_text((60, 100), "Please remit payment within 30 days.", fontsize=9)
    closing.insert_text((60, 120), "Questions? Call support.", fontsize=9)

    doc.save(str(path))
    doc.close()

    page_map = classify_pages(extract_layout_document(path, include_markdown=False))
    roles = {p.page_number: p.role for p in page_map.pages}

    assert roles[1] == "header", roles
    assert roles[2] == "header", roles
    assert roles[3] == "detail", roles
    assert all(roles[n] == "detail_continuation" for n in range(4, 11)), roles
    assert roles[11] == "summary", roles

    run = page_map.largest_run
    assert run.pages == tuple(range(3, 11))

    sample = page_map.sample_pages(max_pages=6)
    assert 1 in sample and 3 in sample and 10 in sample, sample
    assert len(sample) <= 6


class TestContextPages:
    """Where document-level fields live — a different question from sampling."""

    def _doc(self):
        # Two cover pages, a 10-page table, then two closing pages.
        pages = [_prose_page(1), _prose_page(2)]
        pages += [_table_page(n, COLS, rows=20) for n in range(3, 13)]
        pages += [_prose_page(13), _prose_page(14)]
        return LayoutDocument(pages=pages)

    def test_it_takes_the_pages_before_the_main_table(self):
        context = classify_pages(self._doc()).context_pages()
        assert 1 in context and 2 in context, context

    def test_it_takes_the_end_of_the_table_and_the_page_after_it(self):
        """A subtotal for a table ending on page 12 is printed on page 13."""
        context = classify_pages(self._doc()).context_pages()
        assert 12 in context, context
        assert 13 in context, context

    def test_it_does_not_drag_in_the_body_of_the_table(self):
        context = classify_pages(self._doc()).context_pages()
        assert not any(3 <= page <= 11 for page in context), context

    def test_a_header_beyond_the_first_five_pages_is_still_found(self):
        """The case the old ``page <= 5`` rule could not express."""
        pages = [_prose_page(n) for n in range(1, 9)]
        pages += [_table_page(n, COLS, rows=20) for n in range(9, 20)]
        context = classify_pages(LayoutDocument(pages=pages)).context_pages(max_pages=10)
        assert 7 in context and 8 in context, context

    def test_it_is_bounded_and_ordered(self):
        for limit in (3, 5, 8):
            context = classify_pages(self._doc()).context_pages(max_pages=limit)
            assert len(context) <= limit
            assert context == sorted(context)

    def test_a_document_with_no_table_still_returns_pages(self):
        doc = LayoutDocument(pages=[_prose_page(n) for n in (1, 2, 3)])
        assert classify_pages(doc).context_pages() == [1, 2, 3]
