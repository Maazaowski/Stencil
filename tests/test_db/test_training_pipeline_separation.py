"""Training data is separate from production: excluded from lists/stats, and
AI cost is attributed by purpose (extraction vs classification vs model training)."""

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


def _intake(db, *, status="completed", source=None) -> str:
    rec = IntakeRecord(
        original_filename="x.pdf", original_pdf_path="/x.pdf",
        status=status, intake_source=source,
    )
    db.add(rec)
    db.commit()
    return rec.id


def test_list_intakes_excludes_training_by_default(db):
    prod = _intake(db, source="watcher")
    _intake(db, source="training")  # staged sample
    items, total, _counts, _jobs = crud.list_intakes(db)
    ids = {i.id for i in items}
    assert total == 1
    assert prod in ids

    # Explicitly asking for training source returns them.
    items_t, total_t, _c, _j = crud.list_intakes(db, intake_source="training")
    assert total_t == 1


def test_dashboard_stats_count_production_only(db):
    _intake(db, status="completed", source="watcher")
    _intake(db, status="completed", source="upload")
    _intake(db, status="completed", source="training")  # must not count
    _intake(db, status="failed", source="training")      # must not count

    stats = crud.get_dashboard_stats(db)
    assert stats["total_invoices"] == 2
    assert stats["completed_invoices"] == 2
    assert stats["failed_invoices"] == 0
    # Throughput: both production invoices finished the pipeline.
    assert stats["completion_rate"] == 100.0


def test_success_rate_measures_reconciliation_not_completion(db):
    """`success_rate` is the ACCURACY number.

    An invoice that finished with warnings is still "completed", but if its
    totals did not reconcile it is not a success. Counting it as one reported
    ~90% while barely half of extractions actually balanced.
    """
    ok = _intake(db, status="completed", source="watcher")
    warned = _intake(db, status="completed_with_warnings", source="watcher")
    for intake_id, reconciled in ((ok, True), (warned, False)):
        db.add(ExtractionJob(
            intake_id=intake_id, extraction_path="ai", is_reconciled=reconciled,
        ))
    db.commit()

    stats = crud.get_dashboard_stats(db)
    # Both invoices "completed"...
    assert stats["completion_rate"] == 100.0
    # ...but only one reconciled.
    assert stats["success_rate"] == 50.0
    assert stats["reconciled_jobs"] == 1
    assert stats["scored_jobs"] == 2
    # And the warned invoice still needs a human to look at it.
    assert stats["pending_exceptions"] == 1


def test_ai_cost_breakdown_attributes_training_extraction(db):
    prod = _intake(db, source="watcher")
    train = _intake(db, source="training")

    crud.log_ai_call(db, intake_id=prod, call_type="extraction",
                     ai_model_name="m", estimated_cost_usd=1.0)
    crud.log_ai_call(db, intake_id=prod, call_type="classification",
                     ai_model_name="m", estimated_cost_usd=0.2)
    crud.log_ai_call(db, intake_id=train, call_type="extraction",
                     ai_model_name="m", estimated_cost_usd=0.5)   # training sample
    crud.log_ai_call(db, intake_id=train, call_type="model_authoring",
                     ai_model_name="m", estimated_cost_usd=0.3)

    bd = crud.get_ai_cost_breakdown(db, days=3650)
    assert bd["buckets"]["production_extraction"]["cost"] == 1.0
    assert bd["buckets"]["classification"]["cost"] == 0.2
    # training-sample extraction + authoring both count as model training.
    assert bd["buckets"]["model_training"]["cost"] == 0.8
    assert bd["total_cost"] == 2.0

    stats = crud.get_dashboard_stats(db)
    assert stats["model_training_cost"] == 0.8
    assert round(stats["extraction_cost"], 6) == 1.2  # everything minus training


def test_gate_never_extracts_for_unprocessed_samples(db, tmp_path, monkeypatch):
    """The training-set gate is polled in the web request thread — it must report
    staged-but-unprocessed samples as 'pending' and NEVER trigger AI extraction."""
    from stencil.models import training
    from stencil.models.schema import ExtractionModel
    from stencil.profiles.schema import (
        ClassificationSignals,
        SupplierIdentity,
        SupplierProfile,
    )

    monkeypatch.setattr(training.settings, "archive_dir", tmp_path / "archive")
    monkeypatch.setattr(training.settings, "processing_dir", tmp_path / "processing")
    monkeypatch.setattr(training.settings, "completed_dir", tmp_path / "completed")

    # A staged sample: PDF on disk, but NO ground truth json.
    (tmp_path / "archive" / "s1").mkdir(parents=True)
    (tmp_path / "archive" / "s1" / "original.pdf").write_bytes(b"%PDF-1.4")

    extracted = {"called": False}
    monkeypatch.setattr(
        training, "_extract_ground_truth",
        lambda *a, **k: extracted.__setitem__("called", True) or None,
    )
    # Layout extraction must not even be reached for a pending sample on poll.
    monkeypatch.setattr(
        training, "extract_layout_document",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not parse layout on gate poll")),
    )

    profile = SupplierProfile(
        profile_id="colt.standard.v1",
        identity=SupplierIdentity(canonical_name="Colt"),
        classification=ClassificationSignals(output_type="standard"),
    )
    model = ExtractionModel(model_id="colt.standard.v1", supplier_profile_id="colt.standard.v1",
                            supplier="Colt", layout_fingerprint="sha256:fp",
                            training_intake_ids=["s1"])

    gate = training.evaluate_model_training_set(db, model, profile)
    assert extracted["called"] is False  # NO AI extraction from the gate
    row = gate["invoices"][0]
    assert row["ok"] is False
    assert "Not processed yet" in row["reason"]


def test_training_run_lifecycle(db):
    run = crud.create_training_run(db, model_id="colt.standard.v1",
                                   supplier_profile_id="colt.standard.v1", total_steps=4)
    assert run.state == "running"
    assert crud.get_latest_training_run(db, "colt.standard.v1").id == run.id

    crud.update_training_run(db, run.id, state="success", step="done",
                             completed_steps=4, message="done")
    latest = crud.get_latest_training_run(db, "colt.standard.v1")
    assert latest.state == "success" and latest.completed_steps == 4


def test_deleting_model_clears_stale_training_runs(db):
    from stencil.models import registry
    from stencil.models.schema import ExtractionModel

    registry.save_model(
        db,
        ExtractionModel(
            model_id="colt.standard.v1",
            supplier_profile_id="colt.standard.v1",
            supplier="Colt",
            layout_fingerprint="sha256:fp",
        ),
    )
    crud.create_training_run(
        db,
        model_id="colt.standard.v1",
        supplier_profile_id="colt.standard.v1",
        total_steps=4,
    )

    assert crud.delete_extraction_model(db, "colt.standard.v1") is True

    assert crud.get_latest_training_run(db, "colt.standard.v1") is None
