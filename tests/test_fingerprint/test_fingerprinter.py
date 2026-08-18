"""Unit tests for the PDF layout fingerprinter module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from stencil.fingerprint.fingerprinter import (
    LayoutFeatures,
    _is_label,
    _is_table_header,
    _legacy_structural_tokens,
    compute_fingerprint,
    compute_fingerprint_legacy,
    extract_layout_features,
    fingerprint_pdf,
)

# ── Helper: build a mock fitz document ──────────────────────


def _make_span(text: str, size: float = 10.0) -> dict:
    return {"text": text, "size": size}


def _make_line(spans: list[dict]) -> dict:
    return {"spans": spans}


def _make_text_block(bbox: tuple, lines: list[dict]) -> dict:
    return {"type": 0, "bbox": bbox, "lines": lines}


def _make_image_block(bbox: tuple) -> dict:
    return {"type": 1, "bbox": bbox}


def _mock_page(blocks: list[dict]) -> MagicMock:
    page = MagicMock()
    page.get_text.return_value = {"blocks": blocks}
    page.rect.width = 595.0
    page.rect.height = 842.0
    return page


def _mock_doc(pages: list[MagicMock]) -> MagicMock:
    doc = MagicMock()
    doc.__len__ = lambda self: len(pages)
    doc.__getitem__ = lambda self, idx: pages[idx]
    doc.close = MagicMock()
    return doc


# ── _is_label tests ─────────────────────────────────────────


class TestIsLabel:
    def test_recognises_common_labels(self):
        assert _is_label("Invoice Number:") is True
        assert _is_label("Account") is True
        assert _is_label("Total Due") is True
        assert _is_label("Date:") is True

    def test_rejects_empty_or_short(self):
        assert _is_label("") is False
        assert _is_label("ab") is False

    def test_rejects_long_text(self):
        assert _is_label("x" * 61) is False

    def test_rejects_unrelated(self):
        assert _is_label("Hello World") is False
        assert _is_label("Lorem ipsum dolor sit amet") is False


# ── _is_table_header tests ──────────────────────────────────


class TestIsTableHeader:
    def test_recognises_table_headers(self):
        assert _is_table_header("Description") is True
        assert _is_table_header("Amount") is True
        assert _is_table_header("Service") is True
        assert _is_table_header("Circuit ID") is True

    def test_rejects_empty(self):
        assert _is_table_header("") is False
        assert _is_table_header("x") is False

    def test_rejects_long(self):
        assert _is_table_header("y" * 41) is False

    def test_rejects_unrelated(self):
        assert _is_table_header("February") is False


# ── compute_fingerprint tests ────────────────────────────────


class TestComputeFingerprint:
    def test_same_features_same_hash(self):
        f1 = LayoutFeatures(
            page_count=3,
            first_page_labels=["Invoice Number:", "Date:"],
            table_header_texts=["Description", "Amount"],
            text_block_positions=[(10, 20), (30, 40)],
            font_sizes_used=[8.0, 10.0, 12.0],
        )
        f2 = LayoutFeatures(
            page_count=3,
            first_page_labels=["Invoice Number:", "Date:"],
            table_header_texts=["Description", "Amount"],
            text_block_positions=[(10, 20), (30, 40)],
            font_sizes_used=[8.0, 10.0, 12.0],
        )
        assert compute_fingerprint(f1) == compute_fingerprint(f2)

    def test_page_count_does_not_change_hash(self):
        f1 = LayoutFeatures(page_count=6, first_page_labels=["Invoice Number:"])
        f2 = LayoutFeatures(page_count=8, first_page_labels=["Invoice Number:"])
        assert compute_fingerprint_legacy(f1) == compute_fingerprint_legacy(f2)

    def test_font_sizes_do_not_change_hash(self):
        # A shorter bill that omits a fine-print section simply uses fewer font
        # sizes; that must NOT flip the layout fingerprint (the AT&T 205 bill
        # regression: same account/layout, 20-page vs 12-page).
        f1 = LayoutFeatures(first_page_labels=["Invoice Number:"],
                            font_sizes_used=[1.5, 6.3, 6.5, 8.2, 9.1, 9.9, 12.2])
        f2 = LayoutFeatures(first_page_labels=["Invoice Number:"],
                            font_sizes_used=[1.5, 8.2, 9.1, 9.9, 12.2])
        assert compute_fingerprint(f1) == compute_fingerprint(f2)
        assert compute_fingerprint_legacy(f1) == compute_fingerprint_legacy(f2)

    def test_font_sizes_do_not_change_profile_hash(self):
        from stencil.fingerprint.fingerprinter import (
            LayoutSpan,
            ResolvedFingerprintConfig,
            compute_fingerprint_profile,
        )

        cfg = ResolvedFingerprintConfig(
            header_anchors=["Invoice Number"], table_anchors=[], summary_anchors=[],
            tax_role_keywords=[], currency_codes=[], ignore_label_patterns=[],
            optional_column_patterns=[], exclude_span_patterns=[],
        )
        spans = [LayoutSpan(text="Invoice Number", page=1, x0=0.0, y0=0.0, font_size=10.0)]
        f1 = LayoutFeatures(font_sizes_used=[6.3, 6.5, 10.0])
        f2 = LayoutFeatures(font_sizes_used=[10.0])
        assert (compute_fingerprint_profile(f1, spans, cfg)
                == compute_fingerprint_profile(f2, spans, cfg))

    def test_different_labels_different_hash(self):
        f1 = LayoutFeatures(first_page_labels=["Invoice Number:"])
        f2 = LayoutFeatures(first_page_labels=["Account:"])
        assert compute_fingerprint(f1) != compute_fingerprint(f2)

    def test_digit_bearing_labels_do_not_change_hash(self):
        f1 = LayoutFeatures(first_page_labels=["Invoice Number:", "Account No.: 55837020"])
        f2 = LayoutFeatures(first_page_labels=["Invoice Number:", "Account No.: 52170670"])
        f3 = LayoutFeatures(first_page_labels=["Invoice Number:"])
        assert compute_fingerprint(f1) == compute_fingerprint(f2) == compute_fingerprint(f3)

    def test_block_positions_do_not_change_hash(self):
        f1 = LayoutFeatures(first_page_labels=["Invoice Number:"], text_block_positions=[(30, 430)])
        f2 = LayoutFeatures(first_page_labels=["Invoice Number:"], text_block_positions=[(30, 440), (250, 430)])
        assert compute_fingerprint(f1) == compute_fingerprint(f2)

    def test_hash_starts_with_sha256(self):
        fp = compute_fingerprint(LayoutFeatures())
        assert fp.startswith("sha256:")
        assert len(fp) == 7 + 64

    def test_legacy_tokens_drop_digit_bearing_text(self):
        tokens = _legacy_structural_tokens(["Invoice Number:", "Account No.: 55837020"])
        assert tokens == ["invoice number"]


# ── extract_layout_features tests ────────────────────────────


class TestExtractLayoutFeatures:
    @patch("stencil.fingerprint.fingerprinter.fitz")
    def test_extracts_features_from_pdf(self, mock_fitz):
        blocks = [
            _make_text_block(
                bbox=(50, 100, 200, 120),
                lines=[_make_line([_make_span("Invoice Number:", 12.0)])],
            ),
            _make_text_block(
                bbox=(50, 200, 400, 220),
                lines=[_make_line([_make_span("Description", 10.0)])],
            ),
            _make_image_block(bbox=(0, 0, 100, 100)),
        ]
        page = _mock_page(blocks)
        doc = _mock_doc([page])
        mock_fitz.open.return_value = doc
        mock_fitz.TEXT_PRESERVE_WHITESPACE = 1

        features = extract_layout_features(Path("dummy.pdf"))

        assert features.page_count == 1
        assert "Invoice Number:" in features.first_page_labels
        assert "Description" in features.table_header_texts
        assert (50, 100) in features.text_block_positions
        assert 12.0 in features.font_sizes_used
        assert 10.0 in features.font_sizes_used
        doc.close.assert_called_once()

    @patch("stencil.fingerprint.fingerprinter.fitz")
    def test_max_pages_respected(self, mock_fitz):
        pages = [_mock_page([]) for _ in range(5)]
        doc = _mock_doc(pages)
        mock_fitz.open.return_value = doc
        mock_fitz.TEXT_PRESERVE_WHITESPACE = 1

        features = extract_layout_features(Path("dummy.pdf"), max_pages=2)

        assert features.page_count == 5
        assert pages[0].get_text.called
        assert pages[1].get_text.called
        assert not pages[2].get_text.called


class TestFingerprintPdf:
    @patch("stencil.fingerprint.fingerprinter.fitz")
    def test_returns_fingerprint_and_features(self, mock_fitz):
        blocks = [
            _make_text_block(
                bbox=(50, 50, 200, 70),
                lines=[_make_line([_make_span("Invoice Date:", 10.0)])],
            ),
        ]
        doc = _mock_doc([_mock_page(blocks)])
        mock_fitz.open.return_value = doc
        mock_fitz.TEXT_PRESERVE_WHITESPACE = 1

        fp, features = fingerprint_pdf(Path("dummy.pdf"))

        assert fp.startswith("sha256:")
        assert isinstance(features, LayoutFeatures)
        assert features.page_count == 1
