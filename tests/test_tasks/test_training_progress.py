"""Training progress DB updates must survive concurrent intake writes."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from stencil.config import settings
from stencil.db import crud
from stencil.db.models import Base, IntakeRecord
from stencil.models.training import compute_training_step_budget
from stencil.pricing import estimate_cost_usd
from stencil.profiles.schema import ClassificationSignals, SupplierIdentity, SupplierProfile


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


def test_compute_training_step_budget_matches_set_loader(db):
    """7 train+holdout invoices -> 16 steps (7 extract + author + 4 test + 3 holdout + save)."""
    ids = [f"id-{i}" for i in range(4)]
    holdout = [f"hold-{i}" for i in range(3)]
    budget = compute_training_step_budget(db, _profile(), ids, holdout)
    assert budget == 16


def test_progress_update_with_stale_intakes_in_session(db):
    """Simulate worker: main session prefetches intakes; another session updates them."""
    rec = IntakeRecord(
        original_filename="a.pdf", original_pdf_path="/a.pdf", status="completed",
    )
    db.add(rec)
    db.commit()
    intake_id = rec.id

    # Prefetch into main session (as train_model_on_set does for filenames).
    crud.get_intake(db, intake_id)

    run = crud.create_training_run(
        db, model_id="colt.standard.v1", supplier_profile_id="colt.standard.v1",
        total_steps=16, message="start",
    )

    # Worker thread updates the same intake on a separate session.
    worker_db = sessionmaker(bind=db.get_bind())()
    crud.update_intake_fingerprint(worker_db, intake_id, "sha256:abc")
    worker_db.close()

    progress_db = sessionmaker(bind=db.get_bind())()
    fields = {
        "step": "extracting",
        "completed_steps": 1,
        "total_steps": 16,
        "message": "Extracted a.pdf",
        "detail": {"invoices": {intake_id: {"state": "extracted", "filename": "a.pdf"}}},
        "tokens_input": 100,
        "tokens_output": 50,
        "estimated_cost_usd": float(estimate_cost_usd(
            settings.openai_model_model_generation, 100, 50)),
    }
    updated = crud.update_training_run(progress_db, run.id, **fields)
    progress_db.close()

    assert updated is not None
    assert updated.completed_steps == 1
    assert updated.tokens_input == 100
