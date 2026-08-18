"""GET /api/v1/invoices/{id}/preview renders the deliverable from canonical JSON."""

import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stencil.config import settings
from stencil.db.models import Base, IntakeRecord
from stencil.db.session import get_db
from stencil.main import app
from stencil.validation.schema import (
    CanonicalInvoice,
    ExtractionMetadata,
    ExtractionPath,
    InvoiceHeader,
    LineItem,
    OutputType,
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "completed_dir", tmp_path)
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
        yield TestClient(app), session_factory, tmp_path
    finally:
        app.dependency_overrides.clear()


def _canonical(intake_id: str) -> dict:
    inv = CanonicalInvoice(
        intake_id=intake_id,
        output_type=OutputType.STANDARD,
        header=InvoiceHeader(
            supplier_name="Colt", invoice_number="INV-9", invoice_date=date(2026, 1, 2),
            account_number="A1", currency="GBP",
        ),
        line_items=[
            LineItem(line_number=1, service_id="S1", billing_reference="C1",
                     description="svc", charge_type="recurring", amount=Decimal("100.00")),
        ],
        subtotal=Decimal("100.00"), tax=Decimal("20.00"), total_due=Decimal("120.00"),
        tax_rate=Decimal("0.20"),
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )
    inv.fields["_tax_output_mode"] = "calculate"
    inv.fields["_tax_rate_source"] = "invoice_tax_rate"
    return inv.model_dump(mode="json")


def test_preview_returns_rows_and_header(client):
    tc, session_factory, tmp_path = client
    session = session_factory()
    record = IntakeRecord(
        original_filename="colt.pdf", original_pdf_path="/in/colt.pdf", status="completed",
    )
    session.add(record)
    session.commit()
    intake_id = record.id
    session.close()

    out_dir = tmp_path / intake_id
    out_dir.mkdir(parents=True)
    (out_dir / "canonical_invoice.json").write_text(
        json.dumps(_canonical(intake_id)), encoding="utf-8"
    )

    resp = tc.get(f"/api/v1/invoices/{intake_id}/preview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [c["header"] for c in body["columns"]][:1] == ["EXT_SERVICEID"]
    assert body["row_count"] == 1
    assert body["extraction_path"] == "ai"
    header = {f["label"]: f["value"] for f in body["header_fields"]}
    assert header["Supplier"] == "Colt"
    # EXT_TAX computed from rate 0.20 on amount 100 = 20.
    assert body["rows"][0][-1] == 20.0


def test_preview_404_when_no_output(client):
    tc, session_factory, _ = client
    session = session_factory()
    record = IntakeRecord(
        original_filename="x.pdf", original_pdf_path="/in/x.pdf", status="received",
    )
    session.add(record)
    session.commit()
    intake_id = record.id
    session.close()

    resp = tc.get(f"/api/v1/invoices/{intake_id}/preview")
    assert resp.status_code == 404
