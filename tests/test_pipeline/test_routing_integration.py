"""Integration tests for profile + fingerprint pipeline routing.

Covers the full lifecycle wiring with a real PDF, a real DB (SQLite) and the
real registry, while mocking only the AI boundaries (extraction + authoring):

  training upload -> candidate authored
  second training upload -> candidate validated/credited
  human approval -> production extraction via the model (zero AI)
  unknown fingerprint on an active profile -> layout drift + AI fallback

The extraction ENGINE itself is exercised by the golden corpus suites; these
tests prove the routing/orchestration around it.
"""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from stencil.config import settings
from stencil.db import crud
from stencil.db.models import Base, ExtractionJob, ProcessingLog
from stencil.extraction.extractor import ExtractionResult, build_canonical_invoice
from stencil.fingerprint.fingerprinter import fingerprint_pdf
from stencil.intake.service import process_new_pdf
from stencil.models.authoring import AuthoringResult
from stencil.models.registry import approve_model, find_candidate_model, save_model
from stencil.models.schema import ExtractionModel, ModelStatus
from stencil.pipeline.processor import _generate_output, continue_pipeline
from stencil.profiles import loader as profiles_loader
from stencil.profiles.schema import (
    ClassificationSignals,
    SupplierIdentity,
    SupplierProfile,
)

FIXTURE_PDF = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "corpus" / "colt.standard" / "invoices" / "676198608_20240201.pdf"
)

PROFILE_ID = "testco.standard.v1"

RAW_AI_DATA = {
    "header": {
        "supplier_name": "TestCo",
        "invoice_number": "INV-1001",
        "invoice_date": "2026-01-01",
        "due_date": "2026-01-31",
        "account_number": "ACC-1",
        "currency": "USD",
        "confidence": 0.95,
    },
    "line_items": [
        {"line_number": 1, "service_id": "SVC-1", "billing_reference": "SVC-1",
         "description": "Service A", "charge_type": "recurring", "amount": "100.00",
         "confidence": 0.95},
        {"line_number": 2, "service_id": "SVC-2", "billing_reference": "SVC-2",
         "description": "Service B", "charge_type": "recurring", "amount": "50.00",
         "confidence": 0.95},
    ],
    "subtotal": "150.00",
    "tax": "0",
    "total_due": "150.00",
    "overall_confidence": 0.95,
}


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def work_dirs(tmp_path, monkeypatch):
    """Point every operational directory at a temp folder."""
    for attr in ("inbound_dir", "processing_dir", "completed_dir",
                 "exceptions_dir", "archive_dir", "extraction_models_dir"):
        directory = tmp_path / attr
        directory.mkdir()
        monkeypatch.setattr(settings, attr, directory)
    return tmp_path


@pytest.fixture(autouse=True)
def no_events(monkeypatch):
    """Pipeline events go to Redis — irrelevant here."""
    monkeypatch.setattr("stencil.pipeline.processor.publish_event", lambda *a, **k: None)
    monkeypatch.setattr("stencil.pipeline.processor.publish_completed", lambda *a, **k: None)
    monkeypatch.setattr("stencil.pipeline.processor.publish_failed", lambda *a, **k: None)


def _make_profile(status: str) -> SupplierProfile:
    return SupplierProfile(
        profile_id=PROFILE_ID,
        status=status,
        identity=SupplierIdentity(canonical_name="TestCo"),
        classification=ClassificationSignals(output_type="standard"),
        delivery={"inbound_path": "/data/TestCo/pdf", "output_path": None},
    )


@pytest.fixture()
def register_profile(monkeypatch):
    """Install a synthetic profile as the ONLY registered profile."""

    def _register(status: str) -> SupplierProfile:
        profile = _make_profile(status)
        monkeypatch.setattr(profiles_loader, "_profiles_cache", {PROFILE_ID: profile})
        return profile

    return _register


@pytest.fixture(autouse=True)
def fake_ai(monkeypatch):
    """Mock the AI boundaries: extraction returns canned data, hints are a no-op."""
    calls = {"extract": 0}

    def fake_extract_invoice(pdf_path, supplier_name=None, output_type="standard",
                             supplier_profile_id=None):
        calls["extract"] += 1
        return ExtractionResult(raw_data=RAW_AI_DATA, tokens_input=100,
                                tokens_output=50, duration_ms=10,
                                ai_model_name="fake-ai")

    monkeypatch.setattr("stencil.pipeline.processor.extract_invoice", fake_extract_invoice)
    monkeypatch.setattr(
        "stencil.pipeline.processor.apply_layout_profile_hints",
        lambda *a, **k: None,
    )
    return calls


def _canned_invoice(intake_id: str, fingerprint: str | None = None):
    """The canonical invoice both the AI mock and the model mock produce."""
    result = ExtractionResult(raw_data=RAW_AI_DATA, ai_model_name="fake-ai")
    return build_canonical_invoice(
        raw_data=RAW_AI_DATA, intake_id=intake_id, extraction_result=result,
        supplier_profile_id=PROFILE_ID, layout_fingerprint=fingerprint, page_count=1,
    )


@pytest.fixture()
def fake_engine(monkeypatch):
    """Mock authoring (AI) and model execution (interpreter) at the boundaries."""

    def fake_author(**kwargs):
        model = ExtractionModel(
            model_id=PROFILE_ID,
            supplier_profile_id=PROFILE_ID,
            supplier="TestCo",
            output_type="standard",
            layout_fingerprint=kwargs["fingerprint"],
            layout_family_key=kwargs.get("layout_family_key"),
            confidence=0.9,
            created_from_intake=kwargs.get("intake_id"),
        )
        return AuthoringResult(model=model, tokens_input=10, tokens_output=10,
                               duration_ms=5, ai_model_name="fake-authoring")

    def fake_execute(model, pdf_path, intake_id, document=None):
        return _canned_invoice(intake_id, model.layout_fingerprint)

    monkeypatch.setattr("stencil.models.training.author_extraction_model", fake_author)
    monkeypatch.setattr("stencil.models.training.execute_model", fake_execute)
    monkeypatch.setattr("stencil.pipeline.processor.execute_model", fake_execute)


def _ingest(db, tmp_path) -> str:
    inbound_pdf = settings.inbound_dir / FIXTURE_PDF.name
    shutil.copy2(FIXTURE_PDF, inbound_pdf)
    return process_new_pdf(db, inbound_pdf)


def _seed_candidate(db, fingerprint: str):
    model = ExtractionModel(
        model_id=PROFILE_ID,
        supplier_profile_id=PROFILE_ID,
        supplier="TestCo",
        output_type="standard",
        status=ModelStatus.CANDIDATE,
        layout_fingerprint=fingerprint,
        confidence=0.9,
    )
    return save_model(db, model)


def _log_steps(db, intake_id: str) -> list[tuple[str, str]]:
    rows = (
        db.query(ProcessingLog)
        .filter(ProcessingLog.intake_id == intake_id)
        .order_by(ProcessingLog.timestamp)
        .all()
    )
    return [(row.step, row.status) for row in rows]


# ── Tests ─────────────────────────────────────────────────────


def test_training_upload_authors_candidate_model(db, tmp_path, register_profile, fake_engine):
    register_profile("training")
    intake_id = _ingest(db, tmp_path)

    continue_pipeline(db, intake_id, profile_id=PROFILE_ID, mode="training")

    fingerprint, _ = fingerprint_pdf(settings.processing_dir / intake_id / "original.pdf")
    candidate = find_candidate_model(db, PROFILE_ID)
    assert candidate is not None
    assert candidate.layout_fingerprint == fingerprint
    record = crud_record(db)
    assert record.status == "candidate"
    assert record.supplier_profile_id == PROFILE_ID
    assert record.validation_success_count == 1
    # Model JSON persisted to filesystem for review
    assert (settings.extraction_models_dir / f"{PROFILE_ID}.json").exists()


def test_second_training_upload_validates_candidate(db, tmp_path, register_profile, fake_engine):
    register_profile("training")
    first = _ingest(db, tmp_path)
    continue_pipeline(db, first, profile_id=PROFILE_ID, mode="training")
    assert crud_record(db).validation_success_count == 1

    second = _ingest(db, tmp_path)
    continue_pipeline(db, second, profile_id=PROFILE_ID, mode="training")

    record = crud_record(db)
    assert record.status == "candidate"
    assert record.validation_success_count == 2
    assert sorted(record.validation_intake_ids) == sorted([first, second])


def test_approved_model_extracts_in_production_with_zero_ai(
    db, tmp_path, register_profile, fake_engine, fake_ai
):
    register_profile("active")
    intake_id = _ingest(db, tmp_path)
    fingerprint, _ = fingerprint_pdf(settings.processing_dir / intake_id / "original.pdf")
    _seed_candidate(db, fingerprint)
    assert approve_model(db, PROFILE_ID, approved_by="tester")

    continue_pipeline(db, intake_id, profile_id=PROFILE_ID, mode="production")

    assert fake_ai["extract"] == 0, "approved model path must not call AI extraction"
    job = db.query(ExtractionJob).filter(ExtractionJob.intake_id == intake_id).one()
    assert job.extraction_path == "model"
    assert job.status == "completed"
    assert ("model_extraction", "completed") in _log_steps(db, intake_id)


def test_candidate_model_validates_alongside_ai_in_production(
    db, tmp_path, register_profile, fake_engine, fake_ai
):
    register_profile("active")
    intake_id = _ingest(db, tmp_path)
    fingerprint, _ = fingerprint_pdf(settings.processing_dir / intake_id / "original.pdf")
    _seed_candidate(db, fingerprint)

    continue_pipeline(db, intake_id, profile_id=PROFILE_ID, mode="production")

    assert fake_ai["extract"] == 1, "candidate path keeps AI authoritative"
    record = crud_record(db)
    assert record.status == "candidate"
    assert record.validation_success_count == 1
    assert ("model_validation", "passed") in _log_steps(db, intake_id)


def test_layout_drift_falls_back_to_ai_and_raises_exception(
    db, tmp_path, register_profile, fake_engine, fake_ai
):
    register_profile("active")
    _seed_candidate(db, "sha256:" + "0" * 64)  # model trained on a DIFFERENT layout
    assert approve_model(db, PROFILE_ID, approved_by="tester")

    intake_id = _ingest(db, tmp_path)
    continue_pipeline(db, intake_id, profile_id=PROFILE_ID, mode="production")

    assert fake_ai["extract"] == 1, "drift must fall back to AI extraction"
    steps = _log_steps(db, intake_id)
    assert ("layout_drift", "detected") in steps
    job = db.query(ExtractionJob).filter(ExtractionJob.intake_id == intake_id).one()
    assert job.extraction_path == "ai"

    error_log = settings.exceptions_dir / intake_id / "error_log.json"
    assert error_log.exists()
    assert json.loads(error_log.read_text(encoding="utf-8"))["reason"] == "layout_drift"


def test_training_mode_requires_training_status(db, tmp_path, register_profile, fake_engine):
    register_profile("active")
    intake_id = _ingest(db, tmp_path)

    with pytest.raises(Exception, match="not in training status"):
        continue_pipeline(db, intake_id, profile_id=PROFILE_ID, mode="training")


def test_production_without_profile_uses_ai_only(db, tmp_path, monkeypatch, fake_engine, fake_ai):
    monkeypatch.setattr(profiles_loader, "_profiles_cache", {"other.v1": _make_profile("active")})
    monkeypatch.setattr(
        profiles_loader._profiles_cache["other.v1"].identity, "canonical_name", "OtherCo"
    )

    from stencil.classification.classifier import ClassificationResult

    monkeypatch.setattr(
        "stencil.pipeline.processor.classify_invoice",
        lambda pdf_path: ClassificationResult(
            supplier_name="TestCo", output_type="standard", confidence=0.9,
        ),
    )

    intake_id = _ingest(db, tmp_path)
    continue_pipeline(db, intake_id, mode="production")

    assert fake_ai["extract"] == 1
    job = db.query(ExtractionJob).filter(ExtractionJob.intake_id == intake_id).one()
    assert job.extraction_path == "ai"


# ── Helpers ───────────────────────────────────────────────────


def crud_record(db):
    from stencil.db import crud

    return crud.find_model_by_profile(db, PROFILE_ID)


def test_full_lifecycle_training_to_production(db, tmp_path, register_profile, fake_engine, fake_ai):
    """training upload -> candidate -> validation -> approve -> production model extraction."""
    profile = register_profile("training")

    # 1. First training upload authors the candidate
    first = _ingest(db, tmp_path)
    continue_pipeline(db, first, profile_id=PROFILE_ID, mode="training")
    assert crud_record(db).status == "candidate"

    # 2. Further training uploads credit validations
    second = _ingest(db, tmp_path)
    continue_pipeline(db, second, profile_id=PROFILE_ID, mode="training")
    assert crud_record(db).validation_success_count == 2

    # 3. Human approves; profile goes active
    assert approve_model(db, PROFILE_ID, approved_by="reviewer")
    profile.status = "active"

    # 4. Production invoice with the same layout extracts via the model, zero AI
    ai_calls_before = fake_ai["extract"]
    production = _ingest(db, tmp_path)
    continue_pipeline(db, production, profile_id=PROFILE_ID, mode="production")

    assert fake_ai["extract"] == ai_calls_before, "production must use the approved model"
    job = (
        db.query(ExtractionJob)
        .filter(ExtractionJob.intake_id == production)
        .one()
    )
    assert job.extraction_path == "model"
    assert job.status == "completed"

    # The delivered output exists in the completed package
    completed_xlsx = list((settings.completed_dir / production).glob("*.xlsx"))
    assert completed_xlsx, "production run must deliver an XLSX package"


def test_output_amounts_survive_the_model_path(db, tmp_path, register_profile, fake_engine):
    """Sanity: the model-path invoice carries the exact amounts end to end."""
    register_profile("training")
    intake_id = _ingest(db, tmp_path)
    continue_pipeline(db, intake_id, profile_id=PROFILE_ID, mode="training")

    invoice = _canned_invoice(intake_id)
    assert sum((item.amount for item in invoice.line_items), Decimal("0")) == Decimal("150.00")


def test_supplier_output_is_named_after_the_pdf_even_with_an_account_label(db, tmp_path):
    """The delivered file is named from the source PDF, not the account.

    Naming by account_label meant every invoice for an account wrote the same
    filename into the same folder over a bare copy: account 82824706 had 10
    distinct invoices all delivered as 82824706.xlsx, and only the last one
    processed survived. README.md documents the contract as
    ``{original_pdf_name}.xlsx``.
    """
    supplier_xls = tmp_path / "supplier" / "xls"
    profile = SupplierProfile(
        profile_id=PROFILE_ID,
        status="active",
        identity=SupplierIdentity(canonical_name="TestCo"),
        classification=ClassificationSignals(output_type="standard"),
        delivery={"inbound_path": str(tmp_path / "supplier" / "pdf"), "output_path": str(supplier_xls)},
    )
    record = crud.create_intake(
        db,
        original_filename="U0143096.pdf",
        original_pdf_path=str(tmp_path / "processing" / "original.pdf"),
        supplier_profile_id=PROFILE_ID,
        account_label="7012506101",
    )
    invoice = _canned_invoice(record.id)

    xlsx_path, json_path, log_path, manifest_path = _generate_output(
        db,
        record.id,
        invoice,
        custom_output_dir=supplier_xls,
        profile=profile,
        account_label=record.account_label,
    )

    assert xlsx_path.name == "U0143096.xlsx"
    assert not (supplier_xls / "7012506101.xlsx").exists()
    assert json_path.exists()
    assert log_path.exists()
    assert manifest_path.exists()
    assert (supplier_xls / "U0143096.xlsx").exists()
    assert not (supplier_xls / "canonical_invoice.json").exists()
    assert not (supplier_xls / "extraction_log.json").exists()
    assert not (supplier_xls / "manifest.json").exists()


def test_two_invoices_for_one_account_deliver_two_files(db, tmp_path):
    """The actual data-loss regression: one account, many invoices, one folder."""
    supplier_xls = tmp_path / "supplier" / "xls"
    profile = SupplierProfile(
        profile_id=PROFILE_ID,
        status="active",
        identity=SupplierIdentity(canonical_name="TestCo"),
        classification=ClassificationSignals(output_type="standard"),
        delivery={"inbound_path": str(tmp_path / "supplier" / "pdf"), "output_path": str(supplier_xls)},
    )
    for filename in ("82824706_january.pdf", "82824706_february.pdf"):
        record = crud.create_intake(
            db,
            original_filename=filename,
            original_pdf_path=str(tmp_path / "processing" / filename),
            supplier_profile_id=PROFILE_ID,
            account_label="82824706",
        )
        _generate_output(
            db, record.id, _canned_invoice(record.id),
            custom_output_dir=supplier_xls, profile=profile,
            account_label=record.account_label,
        )

    delivered = sorted(p.name for p in supplier_xls.glob("*.xlsx"))
    assert delivered == ["82824706_february.xlsx", "82824706_january.xlsx"]


def test_supplier_output_copy_falls_back_to_pdf_named_xlsx_without_account_label(db, tmp_path):
    supplier_xls = tmp_path / "supplier" / "xls"
    profile = SupplierProfile(
        profile_id=PROFILE_ID,
        status="active",
        identity=SupplierIdentity(canonical_name="TestCo"),
        classification=ClassificationSignals(output_type="standard"),
        delivery={"inbound_path": str(tmp_path / "supplier" / "pdf"), "output_path": str(supplier_xls)},
    )
    record = crud.create_intake(
        db,
        original_filename="U0143096.pdf",
        original_pdf_path=str(tmp_path / "processing" / "original.pdf"),
        supplier_profile_id=PROFILE_ID,
    )
    invoice = _canned_invoice(record.id)

    xlsx_path, *_ = _generate_output(
        db,
        record.id,
        invoice,
        custom_output_dir=supplier_xls,
        profile=profile,
    )

    assert xlsx_path.name == "U0143096.xlsx"
    assert (supplier_xls / "U0143096.xlsx").exists()
