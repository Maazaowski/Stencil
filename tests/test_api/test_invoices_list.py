"""GET /api/v1/invoices returns latest-job enrichment and status counts."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stencil.db.models import Base, ExtractionJob, IntakeRecord
from stencil.db.session import get_db
from stencil.main import app


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    def override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        # No `with` block: skip lifespan so no directories are created.
        yield TestClient(app), session_factory
    finally:
        app.dependency_overrides.clear()


def _seed(session_factory):
    session = session_factory()
    t0 = datetime(2026, 6, 1, 12, 0, 0)

    completed = IntakeRecord(
        original_filename="lumen_june.pdf", original_pdf_path="/in/lumen_june.pdf",
        status="completed",
    )
    failed = IntakeRecord(
        original_filename="broken.pdf", original_pdf_path="/in/broken.pdf",
        status="failed",
    )
    session.add_all([completed, failed])
    session.commit()

    first = ExtractionJob(
        intake_id=completed.id, extraction_path="model",
        supplier_name="Lumen", output_type="wireless",
        overall_confidence=0.5, status="failed", created_at=t0,
    )
    latest = ExtractionJob(
        intake_id=completed.id, extraction_path="model_fallback_ai",
        supplier_name="Lumen", output_type="wireless",
        overall_confidence=0.93, status="completed",
        started_at=t0 + timedelta(seconds=5),
        completed_at=t0 + timedelta(seconds=45),
        created_at=t0 + timedelta(seconds=10),
    )
    session.add_all([first, latest])
    session.commit()
    intake_id = completed.id
    session.close()
    return intake_id


def test_list_returns_latest_job_fields_and_status_counts(client):
    test_client, session_factory = client
    enriched_id = _seed(session_factory)

    resp = test_client.get("/api/v1/invoices")
    assert resp.status_code == 200
    body = resp.json()

    assert body["total"] == 2
    assert body["status_counts"] == {"completed": 1, "failed": 1}

    by_id = {item["id"]: item for item in body["items"]}
    enriched = by_id[enriched_id]
    assert enriched["supplier_name"] == "Lumen"
    assert enriched["output_type"] == "wireless"
    assert enriched["extraction_path"] == "model_fallback_ai"
    assert enriched["overall_confidence"] == 0.93
    assert enriched["started_at"] is not None
    assert enriched["completed_at"] is not None

    bare = next(item for item in body["items"] if item["id"] != enriched_id)
    assert bare["supplier_name"] is None
    assert bare["extraction_path"] is None


def test_list_filters(client):
    test_client, session_factory = client
    _seed(session_factory)

    resp = test_client.get("/api/v1/invoices", params={"output_type": "wireless"})
    assert resp.json()["total"] == 1

    resp = test_client.get("/api/v1/invoices", params={"supplier": "lum"})
    assert resp.json()["total"] == 1

    resp = test_client.get(
        "/api/v1/invoices", params={"date_from": "2030-01-01T00:00:00"}
    )
    assert resp.json()["total"] == 0

    resp = test_client.get("/api/v1/invoices", params={"status": "failed"})
    body = resp.json()
    assert body["total"] == 1
    # counts still show the full breakdown for the chips
    assert body["status_counts"] == {"completed": 1, "failed": 1}
