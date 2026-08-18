"""Training must never save a model that fails its own self-checks.

A self-check failure means a totals rule is wrong even when every line item
matches the AI ground truth. Saving such a model poisons production: every
future execution raises, the candidate racks up validation failures forever.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from stencil.db.models import Base
from stencil.extraction.layout import LayoutDocument
from stencil.models import training
from stencil.models.authoring import AuthoringResult
from stencil.models.interpreter import ModelExecutionError
from stencil.models.schema import ExtractionModel
from stencil.profiles.schema import (
    ClassificationSignals,
    SupplierIdentity,
    SupplierProfile,
)
from stencil.validation.schema import (
    CanonicalInvoice,
    ExtractionMetadata,
    ExtractionPath,
    InvoiceHeader,
    LineItem,
    OutputType,
)


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _profile() -> SupplierProfile:
    return SupplierProfile(
        profile_id="colt.standard.v1",
        identity=SupplierIdentity(canonical_name="Colt"),
        classification=ClassificationSignals(output_type="standard"),
    )


def _invoice(intake_id: str = "t1") -> CanonicalInvoice:
    return CanonicalInvoice(
        intake_id=intake_id,
        output_type=OutputType.STANDARD,
        header=InvoiceHeader(
            supplier_name="Colt",
            invoice_number="708205451",
            invoice_date=date(2024, 2, 1),
            account_number="5-HLBHFCGL",
        ),
        line_items=[
            LineItem(
                line_number=1, service_id="442117492", billing_reference="442117492",
                description="Wave", charge_type="recurring", amount=Decimal("7992.68"),
            ),
        ],
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )


def _authoring_result() -> AuthoringResult:
    model = ExtractionModel(
        model_id="colt.standard.v1",
        supplier_profile_id="colt.standard.v1",
        supplier="Colt",
    )
    return AuthoringResult(
        model=model, tokens_input=10, tokens_output=10, duration_ms=5,
        ai_model_name="test-model",
    )


@pytest.fixture()
def patched(monkeypatch, tmp_path):
    """Stub the AI call, review artifacts, and registry around training."""
    calls = {"saved": []}

    monkeypatch.setattr(training, "author_extraction_model", lambda **kw: _authoring_result())
    monkeypatch.setattr(training, "write_model_review_artifacts", lambda *a, **kw: [])
    monkeypatch.setattr(
        training, "build_model_authoring_evidence", lambda *a, **kw: {},
    )

    def fake_save(db, model):
        calls["saved"].append(model.model_id)
        from stencil.models.registry import SaveModelResult
        return SaveModelResult(model_id=model.model_id, action="created", persisted_status="candidate")

    monkeypatch.setattr(training, "save_model", fake_save)
    monkeypatch.setattr(training.settings, "completed_dir", tmp_path)
    return calls


def test_self_check_failure_blocks_save_even_when_rows_match(db, patched, monkeypatch):
    """Execution raises a self-check error; the relaxed re-run matches the AI
    rows exactly — the model must still NOT be saved."""
    ai_invoice = _invoice()

    def fake_execute(model, pdf_path, intake_id, document=None, skip_self_checks=False):
        if not skip_self_checks:
            raise ModelExecutionError("self-check failed: sum of line items 7992.68 != subtotal 23.00")
        return _invoice(intake_id)

    monkeypatch.setattr(training, "execute_model", fake_execute)

    result = training.author_and_save_profile_model(
        db,
        intake_id="t1",
        pdf_path=None,
        ai_invoice=ai_invoice,
        profile=_profile(),
        fingerprint="sha256:abc",
        layout_family_key=None,
        document=LayoutDocument(pages=[]),
    )

    assert result.success is False
    assert patched["saved"] == []
    assert any("self-check" in err for err in result.errors)


def test_clean_execution_with_exact_match_saves(db, patched, monkeypatch):
    ai_invoice = _invoice()
    monkeypatch.setattr(
        training, "execute_model",
        lambda model, pdf_path, intake_id, document=None, skip_self_checks=False: _invoice(intake_id),
    )

    result = training.author_and_save_profile_model(
        db,
        intake_id="t1",
        pdf_path=None,
        ai_invoice=ai_invoice,
        profile=_profile(),
        fingerprint="sha256:abc",
        layout_family_key=None,
        document=LayoutDocument(pages=[]),
    )

    assert result.success is True
    assert patched["saved"] == ["colt.standard.v1"]
