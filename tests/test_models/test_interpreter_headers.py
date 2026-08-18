"""Tests for label-relative header field extraction in the model interpreter."""

from pathlib import Path

import pytest

from stencil.extraction.layout import extract_layout_document, render_layout_text
from stencil.models.interpreter import _find_value_by_label
from stencil.models.schema import HeaderFieldRule

EUNETWORKS_PDF = Path(
    r"C:\Stencil\Sample Data\EUNetworks\A837737\pdf\A837737 - IE-SI53564.pdf"
)


@pytest.fixture(scope="module")
def eunetworks_page_texts():
    if not EUNETWORKS_PDF.exists():
        pytest.skip("euNetworks sample PDF not available")
    document = extract_layout_document(EUNETWORKS_PDF)
    return render_layout_text(document)


class TestTableStyleHeaders:
    def test_invoice_number_with_restrictive_pattern(self, eunetworks_page_texts):
        rule = HeaderFieldRule(
            label="Document No.",
            value_position="right",
            value_pattern=r"(IE-[A-Z0-9]+)",
            required=True,
        )
        assert _find_value_by_label(rule, eunetworks_page_texts) == "IE-SI53564"

    def test_account_number_from_customer_id_column(self, eunetworks_page_texts):
        rule = HeaderFieldRule(label="Customer ID", required=True)
        assert _find_value_by_label(rule, eunetworks_page_texts) == "A837737"

    def test_invoice_date_from_table_header(self, eunetworks_page_texts):
        rule = HeaderFieldRule(label="Invoice Date", date_format="%d/%m/%Y", required=True)
        raw = _find_value_by_label(rule, eunetworks_page_texts)
        assert raw == "02/12/2025"

    def test_due_date_from_table_header(self, eunetworks_page_texts):
        rule = HeaderFieldRule(label="Due Date", date_format="%d/%m/%Y", required=True)
        assert _find_value_by_label(rule, eunetworks_page_texts) == "01/01/2026"
