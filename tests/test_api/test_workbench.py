"""Workbench API: model-scoped training upload, split editing, versions, rollback."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stencil.config import settings
from stencil.db import crud
from stencil.db.models import Base
from stencil.db.session import get_db
from stencil.main import app
from stencil.models import registry
from stencil.models.schema import ExtractionModel
from stencil.profiles.loader import load_all_profiles

PROFILE_ID = "colt.standard.v1"


@pytest.fixture()
def client(tmp_path, monkeypatch):
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

    # Keep all filesystem writes inside the test sandbox.
    monkeypatch.setattr(registry.settings, "extraction_models_dir", tmp_path / "models")
    monkeypatch.setattr(settings, "inbound_dir", tmp_path / "inbound")

    # Point the profile loader at this test's engine and seed the Colt profile the
    # workbench endpoints look up (the live registry seeds no client profiles).
    from stencil.db import session as db_session
    from stencil.profiles import loader as ploader
    from tests.corpus_utils import load_corpus_profile

    monkeypatch.setattr(db_session, "engine", engine)
    monkeypatch.setattr(db_session, "SessionLocal", session_factory)
    monkeypatch.setattr(ploader, "_profiles_cache", {})

    app.dependency_overrides[get_db] = override_get_db
    ploader.save_profile(load_corpus_profile("colt.standard"))
    load_all_profiles()
    try:
        yield TestClient(app), session_factory
    finally:
        app.dependency_overrides.clear()


def _seed_approved_v1(session_factory) -> None:
    session = session_factory()
    model = ExtractionModel(
        model_id=PROFILE_ID, supplier_profile_id=PROFILE_ID, supplier="Colt",
        layout_fingerprint="sha256:fp", training_intake_ids=["a", "b", "c"],
    )
    registry.save_model(session, model)
    crud.update_model_status(session, PROFILE_ID, "approved", approved_by="me")
    session.commit()
    session.close()


def test_bootstrap_creates_empty_candidate_cold_start(client):
    test_client, session_factory = client

    resp = test_client.post(
        "/api/v1/models/bootstrap", json={"supplier_profile_id": PROFILE_ID}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == PROFILE_ID
    assert body["status"] == "candidate"
    assert body["supplier_profile_id"] == PROFILE_ID
    # Empty until trained: no rules and no fingerprint yet.
    assert body["model_json"]["layout_fingerprint"] == ""

    session = session_factory()
    assert crud.get_extraction_model(session, PROFILE_ID).status == "candidate"
    session.close()


def test_bootstrap_is_idempotent_returns_existing(client):
    test_client, session_factory = client
    _seed_approved_v1(session_factory)

    resp = test_client.post(
        "/api/v1/models/bootstrap", json={"supplier_profile_id": PROFILE_ID}
    )
    assert resp.status_code == 200
    body = resp.json()
    # No new model is created; the existing (approved) one is returned untouched.
    assert body["id"] == PROFILE_ID
    assert body["status"] == "approved"


def test_bootstrap_unknown_profile_404(client):
    test_client, _ = client
    resp = test_client.post(
        "/api/v1/models/bootstrap", json={"supplier_profile_id": "does.not.exist"}
    )
    assert resp.status_code == 404


def test_update_training_set_autoclones_when_live(client):
    test_client, session_factory = client
    _seed_approved_v1(session_factory)

    resp = test_client.put(
        f"/api/v1/models/{PROFILE_ID}/training-set",
        json={"train_intake_ids": ["a", "b"], "holdout_intake_ids": ["c"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Editing a live model forks a candidate; production v1 is untouched.
    assert body["id"] != PROFILE_ID
    assert body["status"] == "candidate"
    assert body["model_json"]["holdout_intake_ids"] == ["c"]

    session = session_factory()
    assert crud.get_extraction_model(session, PROFILE_ID).status == "approved"
    session.close()


def test_versions_lists_lineage_with_live_marker(client):
    test_client, session_factory = client
    _seed_approved_v1(session_factory)
    session = session_factory()
    registry.clone_model(session, PROFILE_ID)  # candidate v2
    session.commit()
    session.close()

    resp = test_client.get(f"/api/v1/models/{PROFILE_ID}/versions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["live_model_id"] == PROFILE_ID
    by_id = {v["model_id"]: v for v in body["versions"]}
    assert by_id[PROFILE_ID]["is_live"] is True
    clone_id = f"{PROFILE_ID}__v2"
    assert clone_id in by_id and by_id[clone_id]["is_live"] is False


def test_rollback_repromotes_retired_version(client):
    test_client, session_factory = client
    _seed_approved_v1(session_factory)
    session = session_factory()
    clone = registry.clone_model(session, PROFILE_ID)
    registry.promote_model(session, clone.model_id, approved_by="me")  # v1 retired, v2 live
    session.commit()
    session.close()

    # Rolling back to the live version is rejected.
    assert test_client.post(f"/api/v1/models/{clone.model_id}/rollback", json={}).status_code == 409

    # Rolling back to the retired v1 makes it live again and retires v2.
    resp = test_client.post(f"/api/v1/models/{PROFILE_ID}/rollback", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"

    session = session_factory()
    assert crud.get_extraction_model(session, PROFILE_ID).status == "approved"
    assert crud.get_extraction_model(session, clone.model_id).status == "retired"
    session.close()


def _seed_candidate_with_run(session_factory) -> None:
    """A candidate model with 3 training invoices and one finished training run."""
    session = session_factory()
    model = ExtractionModel(
        model_id=PROFILE_ID, supplier_profile_id=PROFILE_ID, supplier="Colt",
        layout_fingerprint="sha256:fp", training_intake_ids=["a", "b", "c"],
    )
    registry.save_model(session, model)  # candidate by default
    run = crud.create_training_run(
        session, model_id=PROFILE_ID, supplier_profile_id=PROFILE_ID, total_steps=5,
    )
    crud.update_training_run(
        session, run.id, state="success", step="done", completed_steps=5,
        detail={
            "attempts": 2,
            "per_invoice": {
                "a": {"ok": True, "reason": "reproduced exactly", "expected_lines": 3},
                "b": {"ok": True, "reason": "reproduced exactly", "expected_lines": 2},
                "c": {"ok": True, "reason": "reproduced exactly", "expected_lines": 4},
            },
        },
    )
    session.commit()
    session.close()


def test_training_set_view_is_cheap_no_model_execution(client, monkeypatch):
    """GET /training-set reads last-run results and NEVER executes the model."""
    test_client, session_factory = client
    _seed_candidate_with_run(session_factory)

    import stencil.models.training as training_mod

    def _boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("training-set view must not execute the model")

    monkeypatch.setattr(training_mod, "execute_model", _boom)

    resp = test_client.get(f"/api/v1/models/{PROFILE_ID}/training-set")
    assert resp.status_code == 200
    body = resp.json()
    # Reflects the last run, with no model execution.
    assert body["reproduces_all"] is True
    assert body["approvable"] is True
    assert body["reasons"] == []
    assert body["last_run"]["attempts"] == 2
    by_id = {inv["intake_id"]: inv for inv in body["invoices"]}
    assert by_id["a"]["ok"] is True
    assert by_id["a"]["trained"] is True
    assert by_id["a"]["line_count"] == 3


def test_training_set_view_marks_untrained_invoices(client, monkeypatch):
    """Invoices not in the last run report as not-trained (no execution)."""
    test_client, session_factory = client
    # Candidate with members but NO training run yet.
    session = session_factory()
    registry.save_model(
        session,
        ExtractionModel(
            model_id=PROFILE_ID, supplier_profile_id=PROFILE_ID, supplier="Colt",
            layout_fingerprint="sha256:fp", training_intake_ids=["a", "b", "c"],
        ),
    )
    session.commit()
    session.close()

    import stencil.models.training as training_mod
    monkeypatch.setattr(
        training_mod, "execute_model",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no execution")),
    )

    resp = test_client.get(f"/api/v1/models/{PROFILE_ID}/training-set")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reproduces_all"] is False
    assert body["approvable"] is False
    assert all(inv["trained"] is False for inv in body["invoices"])
    assert any("not been trained" in r for r in body["reasons"])


def test_training_upload_stages_only_and_tags_holdout(client, monkeypatch):
    """Upload STAGES the PDF (no pipeline run, no invoice) and clones the live
    model first. Processing happens later, on Train."""
    test_client, session_factory = client
    _seed_approved_v1(session_factory)

    import stencil.intake.service as intake_service

    # Stage-only: no Celery task, no continue_pipeline. process_new_pdf is the
    # only side effect; stub it to return a known id without touching disk.
    monkeypatch.setattr(intake_service, "process_new_pdf", lambda *a, **k: "staged-1")

    resp = test_client.post(
        f"/api/v1/models/{PROFILE_ID}/training-invoices",
        data={"role": "holdout"},
        files={"file": ("sample.pdf", b"%PDF-1.4 test", "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cloned"] is True
    assert body["model_id"] != PROFILE_ID
    assert body["role"] == "holdout"
    assert body["status"] == "staged"
    assert body["intake_id"] == "staged-1"

    # The staged intake landed on the candidate clone's holdout list.
    session = session_factory()
    candidate = registry.load_model(session, body["model_id"])
    assert "staged-1" in candidate.holdout_intake_ids
    assert "staged-1" not in candidate.training_intake_ids
    # Production v1 is still approved and unchanged.
    assert crud.get_extraction_model(session, PROFILE_ID).status == "approved"
    session.close()
