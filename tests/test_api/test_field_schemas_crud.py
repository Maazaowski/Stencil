"""Tests for field schema CRUD API."""


import pytest
from fastapi.testclient import TestClient

from stencil.config import settings
from stencil.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    schemas_dir = tmp_path / "field_schemas"
    profiles_dir = tmp_path / "supplier_profiles"
    schemas_dir.mkdir()
    profiles_dir.mkdir()
    monkeypatch.setattr(settings, "field_schemas_dir", schemas_dir)
    monkeypatch.setattr(settings, "supplier_profiles_dir", profiles_dir)
    return TestClient(app)


def test_create_list_get_update_delete_field_schema(client):
    create = client.post(
        "/api/v1/field-schemas",
        json={
            "schema_id": "lab_report.standard",
            "name": "Lab Report",
            "template": "blank_tabular",
        },
    )
    assert create.status_code == 201, create.text

    listed = client.get("/api/v1/field-schemas")
    assert listed.status_code == 200
    ids = [s["schema_id"] for s in listed.json()["schemas"]]
    assert "lab_report.standard" in ids

    detail = client.get("/api/v1/field-schemas/lab_report.standard")
    assert detail.status_code == 200
    assert len(detail.json()["fields"]) >= 2

    updated = client.put(
        "/api/v1/field-schemas/lab_report.standard",
        json={
            "schema_id": "lab_report.standard",
            "name": "Lab Report v2",
            "fields": detail.json()["fields"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Lab Report v2"

    deleted = client.delete("/api/v1/field-schemas/lab_report.standard")
    assert deleted.status_code == 204


def test_clone_field_schema(client):
    clone = client.post(
        "/api/v1/field-schemas/invoice.standard/clone",
        json={"new_schema_id": "invoice.copy", "name": "Invoice Copy"},
    )
    assert clone.status_code == 201, clone.text
    assert clone.json()["schema_id"] == "invoice.copy"
    assert len(clone.json()["fields"]) > 10


def test_cannot_delete_builtin_invoice_schema(client):
    response = client.delete("/api/v1/field-schemas/invoice.standard")
    assert response.status_code == 400
