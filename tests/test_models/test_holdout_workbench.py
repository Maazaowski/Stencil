"""Workbench: holdout split, membership editing, and holdout carry-through.

Holdout invoices are the generalization signal — the model must reproduce them
but is never authored from them. They are warn-only: failing holdout never
blocks approval, but it is surfaced as a warning.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from stencil.db.models import Base
from stencil.extraction.layout import LayoutDocument
from stencil.models import registry, training
from stencil.models.authoring import AuthoringResult
from stencil.models.schema import ExtractionModel
from stencil.profiles.schema import (
    ClassificationSignals,
    SupplierIdentity,
    SupplierProfile,
)
from stencil.validation.schema import (
    CanonicalInvoice,
    ExtractionMetadata,
    ExtractionPath,
    InvoiceHeader,
    LineItem,
    OutputType,
)


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


def _profile() -> SupplierProfile:
    return SupplierProfile(
        profile_id="colt.standard.v1",
        identity=SupplierIdentity(canonical_name="Colt"),
        classification=ClassificationSignals(output_type="standard"),
    )


def _model(**kw) -> ExtractionModel:
    base = dict(
        model_id="colt.standard.v1", supplier_profile_id="colt.standard.v1",
        supplier="Colt", layout_fingerprint="sha256:fp",
    )
    base.update(kw)
    return ExtractionModel(**base)


def _invoice(intake_id: str = "t1") -> CanonicalInvoice:
    return CanonicalInvoice(
        intake_id=intake_id,
        output_type=OutputType.STANDARD,
        header=InvoiceHeader(supplier_name="Colt", invoice_number="1", invoice_date=date(2024, 2, 1)),
        line_items=[LineItem(line_number=1, description="Wave", charge_type="recurring", amount=Decimal("10"))],
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )


# ── model_holdout_set_ids ───────────────────────────────────────────────


def test_holdout_excludes_ids_also_in_training_set():
    model = _model(training_intake_ids=["a", "b"], holdout_intake_ids=["b", "h1"])
    # "b" is in both — it counts as training, never as holdout.
    assert training.model_holdout_set_ids(model) == ["h1"]


# ── evaluate_model_training_set: holdout is warn-only ────────────────────


def test_failing_holdout_warns_but_does_not_block_approval(db, monkeypatch):
    monkeypatch.setattr(training.settings, "model_validation_required_successes", 3)
    model = _model(training_intake_ids=["a", "b", "c"], holdout_intake_ids=["h1"])

    def fake_eval_one(_db, _model, _profile, iid):
        ok = not iid.startswith("h")  # training passes, holdout fails
        return {"intake_id": iid, "filename": None, "line_count": 1, "ok": ok,
                "reason": "reproduced exactly" if ok else "row mismatch"}

    monkeypatch.setattr(training, "_evaluate_one", fake_eval_one)

    gate = training.evaluate_model_training_set(db, model, _profile())

    assert gate["reproduces_all"] is True
    assert gate["approvable"] is True            # holdout never blocks
    assert gate["holdout_reproduces_all"] is False
    assert gate["warnings"]                       # but it IS surfaced
    assert gate["holdout_intake_ids"] == ["h1"]
    assert len(gate["holdout"]) == 1


def test_passing_holdout_has_no_warning(db, monkeypatch):
    monkeypatch.setattr(training.settings, "model_validation_required_successes", 3)
    model = _model(training_intake_ids=["a", "b", "c"], holdout_intake_ids=["h1"])
    monkeypatch.setattr(
        training, "_evaluate_one",
        lambda _db, _m, _p, iid: {"intake_id": iid, "filename": None, "line_count": 1,
                                  "ok": True, "reason": "reproduced exactly"},
    )

    gate = training.evaluate_model_training_set(db, model, _profile())

    assert gate["approvable"] is True
    assert gate["holdout_reproduces_all"] is True
    assert gate["warnings"] == []


# ── set_training_membership / add_training_intake ───────────────────────


def test_set_membership_holdout_wins_and_protects_authoring_invoice(db):
    registry.save_model(db, _model(created_from_intake="a", training_intake_ids=["a"]))

    # "x" is requested as BOTH train and holdout (holdout must win); "a" is the
    # authoring invoice and must never be held out.
    updated = registry.set_training_membership(
        db, "colt.standard.v1",
        train_intake_ids=["a", "x", "y"],
        holdout_intake_ids=["x", "a"],
    )

    assert updated is not None
    assert "a" in updated.training_intake_ids        # authoring invoice stays train
    assert "a" not in updated.holdout_intake_ids
    assert "x" in updated.holdout_intake_ids          # holdout wins the conflict
    assert "x" not in updated.training_intake_ids
    assert "y" in updated.training_intake_ids


def test_add_training_intake_moves_between_roles(db):
    registry.save_model(db, _model(training_intake_ids=["a"]))

    registry.add_training_intake(db, "colt.standard.v1", "b", role="holdout")
    m1 = registry.load_model(db, "colt.standard.v1")
    assert "b" in m1.holdout_intake_ids and "b" not in m1.training_intake_ids

    # Re-tagging the same invoice as train moves it back (no duplicates).
    registry.add_training_intake(db, "colt.standard.v1", "b", role="train")
    m2 = registry.load_model(db, "colt.standard.v1")
    assert "b" in m2.training_intake_ids and "b" not in m2.holdout_intake_ids


# ── holdout carries through re-authoring ─────────────────────────────────


def test_holdout_survives_reauthoring(db, monkeypatch, tmp_path):
    """Authoring never sees holdout ids, but the saved model must remember them."""
    saved: list[ExtractionModel] = []

    def fake_author(**kw):
        model = _model(training_intake_ids=[kw["intake_id"]])
        return AuthoringResult(model=model, tokens_input=1, tokens_output=1,
                               duration_ms=1, ai_model_name="test")

    monkeypatch.setattr(training, "author_extraction_model", fake_author)
    monkeypatch.setattr(training, "write_model_review_artifacts", lambda *a, **k: [])
    monkeypatch.setattr(training, "build_model_authoring_evidence", lambda *a, **k: {})
    monkeypatch.setattr(
        training, "execute_model",
        lambda model, pdf_path, intake_id, document=None, skip_self_checks=False: _invoice(intake_id),
    )
    monkeypatch.setattr(training.settings, "completed_dir", tmp_path)

    def fake_save(_db, model):
        saved.append(model)
        from stencil.models.registry import SaveModelResult
        return SaveModelResult(model_id=model.model_id, action="created", persisted_status="candidate")

    monkeypatch.setattr(training, "save_model", fake_save)

    example = training.TrainingExample(
        intake_id="a", ai_invoice=_invoice("a"), document=LayoutDocument(pages=[]), pdf_path=None,
    )
    result = training.train_model_on_set(
        db, profile=_profile(), fingerprint="sha256:fp", layout_family_key=None,
        intake_ids=["a"], preloaded=[example], holdout_intake_ids=["h1", "h2"],
    )

    assert result.success is True
    assert saved and saved[-1].holdout_intake_ids == ["h1", "h2"]


def test_holdout_validates_after_success_without_blocking_save(db, monkeypatch, tmp_path):
    """Holdout is evaluated after train passes, but never blocks candidate save."""
    saved: list[ExtractionModel] = []
    author_seen_extras: list[str] = []

    def fake_author(**kw):
        author_seen_extras.extend(ex.intake_id for ex in kw.get("extra_examples") or [])
        model = _model(training_intake_ids=[kw["intake_id"]])
        return AuthoringResult(model=model, tokens_input=1, tokens_output=1,
                               duration_ms=1, ai_model_name="test")

    def fake_execute(model, pdf_path, intake_id, document=None, skip_self_checks=False):
        if intake_id == "h1":
            invoice = _invoice("h1")
            invoice.rows[0]["amount"] = Decimal("99")
            return invoice
        return _invoice(intake_id)

    def fake_save(_db, model):
        saved.append(model)
        from stencil.models.registry import SaveModelResult
        return SaveModelResult(model_id=model.model_id, action="created", persisted_status="candidate")

    monkeypatch.setattr(training, "author_extraction_model", fake_author)
    monkeypatch.setattr(training, "execute_model", fake_execute)
    monkeypatch.setattr(training, "save_model", fake_save)
    monkeypatch.setattr(training, "write_model_review_artifacts", lambda *a, **k: [])
    monkeypatch.setattr(training.settings, "completed_dir", tmp_path)

    train = training.TrainingExample(
        intake_id="a", ai_invoice=_invoice("a"), document=LayoutDocument(pages=[]), pdf_path=None,
    )
    holdout = training.TrainingExample(
        intake_id="h1", ai_invoice=_invoice("h1"), document=LayoutDocument(pages=[]), pdf_path=None,
    )

    result = training.train_model_on_set(
        db, profile=_profile(), fingerprint="sha256:fp", layout_family_key=None,
        intake_ids=["a"], preloaded=[train, holdout], holdout_intake_ids=["h1"],
    )

    assert result.success is True
    assert saved and saved[-1].holdout_intake_ids == ["h1"]
    assert author_seen_extras == []  # holdout was not shown to authoring
    assert result.per_invoice_detail["a"]["ok"] is True
    assert result.per_invoice_detail["h1"]["ok"] is False
    assert result.per_invoice_detail["h1"]["expected_lines"] == 1
