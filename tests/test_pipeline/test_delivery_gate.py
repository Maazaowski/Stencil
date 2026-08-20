"""An unchecked large document must not reach the customer.

A six-page invoice that does not add up gets eyeballed by whoever opens it. A
656-page one does not, so shipping it unverified is worse than shipping nothing.
The completed/ package is written either way; this gates only the copy into the
supplier's delivery folder.
"""

from decimal import Decimal

import pytest

from stencil.config import settings
from stencil.pipeline.processor import _unverified_large_document_blockers
from stencil.validation.schema import (
    ExtractedDocument,
    ExtractionMetadata,
    ExtractionPath,
    ReconciliationResult,
)


def _invoice(pages: int) -> ExtractedDocument:
    return ExtractedDocument(
        intake_id="t",
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI, total_pages=pages),
    )


def _recon(*, reconciled: bool) -> ReconciliationResult:
    return ReconciliationResult(
        line_items_sum=Decimal("100.00"),
        computed_total=Decimal("100.00"),
        variance=Decimal("0.00") if reconciled else Decimal("180.76"),
        variance_pct=0.0 if reconciled else 0.064,
        is_reconciled=reconciled,
        verification_status="reconciled" if reconciled else "mismatch",
    )


@pytest.fixture(autouse=True)
def _threshold(monkeypatch):
    monkeypatch.setattr(settings, "blocking_check_page_threshold", 100)


def test_a_large_document_that_fails_its_check_is_held_back():
    blockers = _unverified_large_document_blockers(_invoice(656), _recon(reconciled=False))
    assert len(blockers) == 1
    assert "656-page" in blockers[0]
    assert "6.40%" in blockers[0]


def test_a_large_document_that_reconciles_is_delivered():
    assert _unverified_large_document_blockers(_invoice(656), _recon(reconciled=True)) == []


def test_a_small_document_is_delivered_even_when_it_fails():
    """Short enough to check by hand — the warning is the right response, not a block."""
    assert _unverified_large_document_blockers(_invoice(8), _recon(reconciled=False)) == []


def test_the_threshold_is_inclusive_at_its_boundary():
    assert _unverified_large_document_blockers(_invoice(99), _recon(reconciled=False)) == []
    assert _unverified_large_document_blockers(_invoice(100), _recon(reconciled=False)) != []


def test_an_unverifiable_document_is_not_blocked():
    """No reconcilable totals is the normal state for a document with no arithmetic.

    Blocking it would make the gate unusable the moment Stencil handles
    something other than invoices.
    """
    assert _unverified_large_document_blockers(_invoice(656), None) == []


def test_zero_disables_the_gate(monkeypatch):
    monkeypatch.setattr(settings, "blocking_check_page_threshold", 0)
    assert _unverified_large_document_blockers(_invoice(656), _recon(reconciled=False)) == []
