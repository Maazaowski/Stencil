"""Tests for output file naming."""

import pytest

from stencil.output.naming import derive_output_xlsx_name


class TestDeriveOutputXlsxName:
    def test_pdf_suffix(self):
        assert derive_output_xlsx_name("Colt_Invoice_12345.pdf") == "Colt_Invoice_12345.xlsx"

    def test_pdf_suffix_case_insensitive_stem(self):
        assert derive_output_xlsx_name("invoice.PDF") == "invoice.xlsx"

    def test_strips_directory(self):
        assert derive_output_xlsx_name("path/to/invoice.pdf") == "invoice.xlsx"

    def test_no_pdf_suffix_appends_xlsx(self):
        assert derive_output_xlsx_name("invoice") == "invoice.xlsx"

    def test_sanitizes_invalid_chars(self):
        assert derive_output_xlsx_name('bad<>name.pdf') == "bad__name.xlsx"

    def test_empty_fallback(self):
        assert derive_output_xlsx_name("") == "invoice_output.xlsx"
        assert derive_output_xlsx_name("   ") == "invoice_output.xlsx"

    @pytest.mark.parametrize("name", ["../evil.pdf", "..\\evil.pdf"])
    def test_path_traversal_stripped(self, name):
        result = derive_output_xlsx_name(name)
        assert "/" not in result
        assert "\\" not in result
        assert result.endswith(".xlsx")
