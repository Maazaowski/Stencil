"""Tests for the deterministic profile-lint endpoint."""

import pytest
from fastapi.testclient import TestClient

from stencil.config import settings
from stencil.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "supplier_profiles_dir", tmp_path / "supplier_profiles")
    (tmp_path / "supplier_profiles").mkdir()
    return TestClient(app)


def _profile(**advanced_hints):
    return {
        "profile_id": "lint.test",
        "identity": {"canonical_name": "Test"},
        "classification": {"output_type": "standard"},
        "notes": advanced_hints.pop("notes", None),
        "advanced": {"line_item_hints": advanced_hints},
    }


def test_lint_flags_note_vs_field_conflict(client):
    body = _profile(notes="EXT_TAX = Net Value x 0.23", tax_output_mode="none")

    resp = client.post("/api/v1/profiles/lint", json=body)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["conflicts"], data
    assert data["conflicts"][0]["field"] == "line_item_hints.tax_output_mode"
    assert "EXT_TAX = Net Value x 0.23" in data["ignored_notes"]


def test_lint_clean_profile_has_no_conflicts(client):
    body = _profile(notes="Dates are printed day-first.", tax_output_mode="extract_exact")

    resp = client.post("/api/v1/profiles/lint", json=body)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"conflicts": [], "ignored_notes": []}


def test_lint_does_not_require_a_saved_profile(client):
    # A brand-new draft (never persisted) can still be linted.
    body = _profile(notes="Compute per-line tax.", tax_source="none")

    resp = client.post("/api/v1/profiles/lint", json=body)

    assert resp.status_code == 200, resp.text
    assert any(c["field"] == "line_item_hints.tax_source" for c in resp.json()["conflicts"])
