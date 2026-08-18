"""Tests for output spec CRUD API."""

import pytest
from fastapi.testclient import TestClient

from stencil.config import settings
from stencil.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    specs_dir = tmp_path / "output_specs"
    profiles_dir = tmp_path / "supplier_profiles"
    specs_dir.mkdir()
    profiles_dir.mkdir()
    monkeypatch.setattr(settings, "output_specs_dir", specs_dir)
    monkeypatch.setattr(settings, "supplier_profiles_dir", profiles_dir)
    return TestClient(app)


def test_create_list_get_update_delete_output_spec(client):
    create = client.post(
        "/api/v1/output-specs",
        json={
            "spec_id": "lab_report.standard",
            "name": "Lab Report Output",
            "columns": [
                {"header": "PATIENT", "source": "field.document_id", "width": 20},
            ],
        },
    )
    assert create.status_code == 201, create.text

    listed = client.get("/api/v1/output-specs")
    assert listed.status_code == 200
    ids = [s["spec_id"] for s in listed.json()["specs"]]
    assert "lab_report.standard" in ids

    detail = client.get("/api/v1/output-specs/lab_report.standard")
    assert detail.status_code == 200

    updated = client.put(
        "/api/v1/output-specs/lab_report.standard",
        json={
            "spec_id": "lab_report.standard",
            "name": "Lab Report Output v2",
            "columns": detail.json()["columns"],
        },
    )
    assert updated.status_code == 200

    deleted = client.delete("/api/v1/output-specs/lab_report.standard")
    assert deleted.status_code == 204


def test_clone_output_spec(client):
    clone = client.post(
        "/api/v1/output-specs/temforce.standard/clone",
        json={"new_spec_id": "temforce.copy", "name": "TemForce Copy"},
    )
    assert clone.status_code == 201, clone.text
    assert clone.json()["spec_id"] == "temforce.copy"
    assert len(clone.json()["columns"]) == 8


def test_create_output_spec_rejects_duplicate_or_empty_headers(client):
    response = client.post(
        "/api/v1/output-specs",
        json={
            "spec_id": "invalid.headers",
            "name": "Invalid headers",
            "columns": [
                {"header": "DATE", "source": "field.invoice_date"},
                {"header": "DATE", "source": "field.due_date"},
                {"header": " ", "source": "field.invoice_number"},
            ],
        },
    )

    assert response.status_code == 422
    issues = response.json()["detail"]["issues"]
    assert "every column requires a non-empty header" in issues
    assert "column headers must be unique: DATE" in issues
