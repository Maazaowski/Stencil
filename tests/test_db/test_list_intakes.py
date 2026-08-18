"""list_intakes filters, status counts, and latest-job enrichment."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from stencil.db import crud
from stencil.db.models import Base, ExtractionJob, IntakeRecord


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _intake(session, *, filename="invoice.pdf", status="completed",
            created_at=None) -> IntakeRecord:
    rec = IntakeRecord(
        original_filename=filename,
        original_pdf_path=f"/in/{filename}",
        status=status,
    )
    if created_at is not None:
        rec.created_at = created_at
    session.add(rec)
    session.commit()
    return rec


def _job(session, intake_id, *, path="ai", supplier=None, output_type="standard",
         confidence=0.9, status="completed", created_at=None) -> ExtractionJob:
    job = ExtractionJob(
        intake_id=intake_id,
        extraction_path=path,
        supplier_name=supplier,
        output_type=output_type,
        overall_confidence=confidence,
        status=status,
    )
    if created_at is not None:
        job.created_at = created_at
    session.add(job)
    session.commit()
    return job


def test_latest_job_wins_with_multiple_jobs(db):
    intake = _intake(db)
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    _job(db, intake.id, path="model", confidence=0.5, created_at=t0)
    _job(db, intake.id, path="model_fallback_ai", confidence=0.95,
         created_at=t0 + timedelta(seconds=5))

    items, total, _, latest_jobs = crud.list_intakes(db)

    assert total == 1
    job = latest_jobs[items[0].id]
    assert job.extraction_path == "model_fallback_ai"
    assert job.overall_confidence == 0.95


def test_intake_without_jobs_has_no_latest_job(db):
    intake = _intake(db)

    items, total, _, latest_jobs = crud.list_intakes(db)

    assert total == 1
    assert items[0].id == intake.id
    assert latest_jobs == {}


def test_output_type_filter(db):
    wireless = _intake(db, filename="wireless.pdf")
    _job(db, wireless.id, output_type="wireless")
    standard = _intake(db, filename="standard.pdf")
    _job(db, standard.id, output_type="standard")

    items, total, _, _ = crud.list_intakes(db, output_type="wireless")

    assert total == 1
    assert items[0].id == wireless.id


def test_extraction_path_filter(db):
    via_model = _intake(db, filename="model.pdf")
    _job(db, via_model.id, path="model")
    via_ai = _intake(db, filename="ai.pdf")
    _job(db, via_ai.id, path="ai")

    items, total, _, _ = crud.list_intakes(db, extraction_path="ai")

    assert total == 1
    assert items[0].id == via_ai.id


def test_supplier_filter_is_partial_match(db):
    lumen = _intake(db, filename="lumen.pdf")
    _job(db, lumen.id, supplier="Lumen Technologies")
    other = _intake(db, filename="other.pdf")
    _job(db, other.id, supplier="Verizon")

    items, total, _, _ = crud.list_intakes(db, supplier="lumen")

    assert total == 1
    assert items[0].id == lumen.id


def test_date_range_filter(db):
    old = _intake(db, filename="old.pdf", created_at=datetime(2026, 1, 1))
    recent = _intake(db, filename="recent.pdf", created_at=datetime(2026, 6, 10))

    items, total, _, _ = crud.list_intakes(db, date_from=datetime(2026, 6, 1))
    assert total == 1
    assert items[0].id == recent.id

    items, total, _, _ = crud.list_intakes(db, date_to=datetime(2026, 6, 1))
    assert total == 1
    assert items[0].id == old.id


def test_status_counts_ignore_status_filter(db):
    _intake(db, status="completed")
    _intake(db, status="completed")
    _intake(db, status="failed")

    items, total, status_counts, _ = crud.list_intakes(db, status="failed")

    assert total == 1
    assert len(items) == 1
    # counts cover all statuses so UI chips can show the full breakdown
    assert status_counts == {"completed": 2, "failed": 1}


def test_status_counts_honor_other_filters(db):
    completed_wireless = _intake(db, status="completed")
    _job(db, completed_wireless.id, output_type="wireless")
    failed_wireless = _intake(db, status="failed")
    _job(db, failed_wireless.id, output_type="wireless")
    completed_standard = _intake(db, status="completed")
    _job(db, completed_standard.id, output_type="standard")

    _, _, status_counts, _ = crud.list_intakes(db, output_type="wireless")

    assert status_counts == {"completed": 1, "failed": 1}
