"""Rules-first must never be the reason a document fails to process.

The path is an optimisation: when it works it saves 123 AI calls, and when it
does not the pipeline must read the document normally. Every failure mode is
therefore required to return ``None`` rather than raise.
"""

import pytest

from stencil.models.sample_authoring import SampleAuthoringOutcome
from stencil.pipeline import processor
from stencil.profiles.schema import (
    ClassificationSignals,
    SupplierIdentity,
    SupplierProfile,
)


def _profile() -> SupplierProfile:
    return SupplierProfile(
        profile_id="p.v1",
        identity=SupplierIdentity(canonical_name="Test"),
        classification=ClassificationSignals(output_type="standard"),
    )


@pytest.fixture()
def db(isolated_db):
    """A throwaway session — the fallback paths still write a processing log."""
    from stencil.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _call(db, tmp_path, candidate=None):
    return processor._try_rules_before_reading(
        db, "intake-1", tmp_path / "x.pdf", _profile(), "fp:abc",
        candidate=candidate, page_count=656,
    )


def test_an_exception_falls_back_instead_of_propagating(db, tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("authoring exploded")

    monkeypatch.setattr(processor, "author_from_sample", boom)
    assert _call(db, tmp_path) is None


def test_a_rejected_outcome_falls_back(db, tmp_path, monkeypatch):
    monkeypatch.setattr(
        processor, "author_from_sample",
        lambda *_a, **_k: SampleAuthoringOutcome(
            status="rejected", reason="off by 4.2%", metrics={"row_count": 12}),
    )
    assert _call(db, tmp_path) is None


def test_a_skipped_outcome_falls_back(db, tmp_path, monkeypatch):
    monkeypatch.setattr(
        processor, "author_from_sample",
        lambda *_a, **_k: SampleAuthoringOutcome(status="skipped", reason="no pages"),
    )
    assert _call(db, tmp_path) is None


def test_an_outcome_with_no_invoice_falls_back(db, tmp_path, monkeypatch):
    """status says authored but the invoice is missing — belt and braces."""
    monkeypatch.setattr(
        processor, "author_from_sample",
        lambda *_a, **_k: SampleAuthoringOutcome(status="authored", invoice=None),
    )
    assert _call(db, tmp_path) is None


@pytest.mark.parametrize("enabled,pages,expected", [
    (False, 656, False),
    (True, 656, True),
    (True, 8, False),
])
def test_the_gate_decides_whether_this_runs_at_all(enabled, pages, expected):
    from stencil.models.sample_authoring import should_author_from_sample

    assert should_author_from_sample(
        pages, _profile(), enabled=enabled, min_pages=100,
    ) is expected
