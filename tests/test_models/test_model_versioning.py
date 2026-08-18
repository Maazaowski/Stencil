"""Phase 2 — clone/versioning: one approved + candidate clones per profile."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from stencil.db import crud
from stencil.db.models import Base
from stencil.models import registry
from stencil.models.schema import ExtractionModel, ModelStatus


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
def _isolate_fs(tmp_path, monkeypatch):
    monkeypatch.setattr(registry.settings, "extraction_models_dir", tmp_path)


def _model(profile_id="eunetworks.standard.v2", training_ids=None) -> ExtractionModel:
    return ExtractionModel(
        model_id=profile_id, supplier_profile_id=profile_id, supplier="euNetworks",
        layout_fingerprint="sha256:fp", training_intake_ids=training_ids or ["a", "b", "c"],
    )


def test_routing_prefers_highest_version_by_status(db):
    registry.save_model(db, _model())  # v1 candidate (id == profile)
    crud.update_model_status(db, "eunetworks.standard.v2", "approved", approved_by="me")

    clone = registry.clone_model(db, "eunetworks.standard.v2")  # v2 candidate
    assert clone is not None
    assert clone.model_id == "eunetworks.standard.v2__v2"
    assert clone.status == ModelStatus.CANDIDATE

    approved = crud.find_model_by_profile(db, "eunetworks.standard.v2", status="approved")
    candidate = crud.find_model_by_profile(db, "eunetworks.standard.v2", status="candidate")
    assert approved.id == "eunetworks.standard.v2"          # original stays live
    assert candidate.id == "eunetworks.standard.v2__v2"     # clone is the candidate


def test_clone_copies_rules_and_training_set_without_touching_source(db):
    registry.save_model(db, _model(training_ids=["x", "y", "z"]))
    crud.update_model_status(db, "eunetworks.standard.v2", "approved", approved_by="me")

    clone = registry.clone_model(db, "eunetworks.standard.v2")
    assert clone.training_intake_ids == ["x", "y", "z"]
    assert clone.approved_by is None and clone.approved_at is None

    # Source is untouched and still approved.
    source = crud.get_extraction_model(db, "eunetworks.standard.v2")
    assert source.status == "approved"


def test_promote_retires_prior_approved_for_rollback(db):
    registry.save_model(db, _model())
    crud.update_model_status(db, "eunetworks.standard.v2", "approved", approved_by="me")
    clone = registry.clone_model(db, "eunetworks.standard.v2")

    assert registry.promote_model(db, clone.model_id, approved_by="me") is True

    promoted = crud.get_extraction_model(db, clone.model_id)
    superseded = crud.get_extraction_model(db, "eunetworks.standard.v2")
    assert promoted.status == "approved"
    assert superseded.status == "retired"          # kept for rollback, not deleted

    # Exactly one approved model is routable for the profile.
    approved = crud.find_model_by_profile(db, "eunetworks.standard.v2", status="approved")
    assert approved.id == clone.model_id
