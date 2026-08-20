"""No caller may silently truncate a document's pages.

``extract_layout_document`` used to default to ``max_pages=30``.  Eight
production call sites took that default, including ``interpreter.py`` -- which
``pipeline/processor.py`` invokes with ``document=None`` on every routing path,
so a model could never read past page 30 of a long document.  Nothing in the
suite caught it because every corpus PDF is 8 pages or fewer.
"""

from decimal import Decimal

import fitz
import pytest

from stencil.extraction.layout import extract_layout_document, pdf_page_count
from stencil.models.interpreter import execute_model
from stencil.models.schema import (
    ExtractionModel,
    FieldSource,
    FieldTransform,
    GroupingRule,
    ItemFieldRule,
    RegionRule,
    RowClassifier,
    RowMatch,
)

PAGES = 40


@pytest.fixture(scope="module")
def long_pdf(tmp_path_factory):
    """A PDF longer than the old cap, one identifiable charge row per page."""
    path = tmp_path_factory.mktemp("longdoc") / "long.pdf"
    doc = fitz.open()
    for n in range(1, PAGES + 1):
        page = doc.new_page(width=612, height=792)
        page.insert_text((60, 100), "Service ID", fontsize=8)
        page.insert_text((520, 100), "Amount", fontsize=8)
        page.insert_text((60, 130), f"SVC{n:05d}", fontsize=8)
        page.insert_text((520, 130), f"{n}.00", fontsize=8)
    doc.save(str(path))
    doc.close()
    return path


def _model() -> ExtractionModel:
    return ExtractionModel(
        model_id="m", supplier_profile_id="m", supplier="Test",
        region=RegionRule(columns=[
            {"name": "service", "x0": 0, "x1": 400},
            {"name": "net_value", "x0": 700, "x1": 1000},
        ]),
        row_classifiers=[
            # Unmatched rows default to "detail", so the column header must be
            # discarded explicitly or it becomes a candidate item.
            RowClassifier(role="skip", where=RowMatch(row_text=r"Service ID")),
            RowClassifier(
                role="detail",
                where=RowMatch(column="service", pattern=r"^SVC\d{5}$"),
            ),
        ],
        grouping=GroupingRule(mode="single_row", item_role="detail"),
        item_fields=[
            ItemFieldRule(
                name="service_id",
                source=FieldSource(rows="first", column="service"),
            ),
            ItemFieldRule(
                name="amount", required=True,
                source=FieldSource(rows="first", column="net_value"),
                transform=FieldTransform(type="currency"),
            ),
        ],
    )


def test_pdf_page_count_reports_the_truth(long_pdf):
    assert pdf_page_count(long_pdf) == PAGES


def test_layout_reads_every_page_by_default(long_pdf):
    document = extract_layout_document(long_pdf, include_markdown=False)
    assert len(document.pages) == PAGES
    assert [p.page_number for p in document.pages][-1] == PAGES
    assert document.warnings == []


def test_an_explicit_bound_truncates_and_says_so(long_pdf):
    document = extract_layout_document(long_pdf, max_pages=10, include_markdown=False)
    assert len(document.pages) == 10
    assert any("truncated to 10 of 40 pages" in w for w in document.warnings), document.warnings


def test_the_model_path_reads_past_page_thirty(long_pdf):
    """The regression that mattered: no ``document`` supplied, as in production.

    ``processor.py`` calls ``execute_model(model, pdf_path, intake_id)`` on all
    three routing paths, so the interpreter builds its own layout.  Under the
    old default that layout stopped at page 30 and pages 31-40 were invisible.
    """
    invoice = execute_model(_model(), pdf_path=long_pdf, intake_id="t")

    service_ids = [row["service_id"] for row in invoice.rows]
    assert len(service_ids) == PAGES, f"expected one row per page, got {len(service_ids)}"
    assert f"SVC{PAGES:05d}" in service_ids, "last page was not read"
    assert "SVC00031" in service_ids, "nothing beyond the old 30-page cap was read"

    total = sum(Decimal(str(row["amount"])) for row in invoice.rows)
    assert total == Decimal(sum(range(1, PAGES + 1)))
