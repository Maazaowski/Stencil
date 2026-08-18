"""Multi-invoice (corpus-grounded) training: set accumulation, save gate, approvability."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from stencil.db import crud
from stencil.db.models import Base
from stencil.extraction.layout import LayoutDocument
from stencil.extraction.prompts import build_authoring_user_prompt
from stencil.models import registry, training
from stencil.models.authoring import AuthoringResult
from stencil.models.registry import SaveModelResult
from stencil.models.schema import ExtractionModel, LineItemStrategy
from stencil.models.training import TrainingExample
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


def _profile() -> SupplierProfile:
    return SupplierProfile(
        profile_id="eunetworks.standard.v2",
        identity=SupplierIdentity(canonical_name="euNetworks"),
        classification=ClassificationSignals(output_type="standard"),
    )


def _invoice(intake_id: str, amounts: list[str]) -> CanonicalInvoice:
    items = [
        LineItem(line_number=i, service_id=f"S{i}", billing_reference=f"S{i}",
                 description="svc", charge_type="recurring", amount=Decimal(a))
        for i, a in enumerate(amounts, start=1)
    ]
    return CanonicalInvoice(
        intake_id=intake_id, output_type=OutputType.STANDARD,
        header=InvoiceHeader(supplier_name="euNetworks", invoice_number=f"INV-{intake_id}",
                             invoice_date=date(2026, 2, 1), account_number="A837737"),
        line_items=items,
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )


def _model(training_ids: list[str]) -> ExtractionModel:
    return ExtractionModel(
        model_id="eunetworks.standard.v2", supplier_profile_id="eunetworks.standard.v2",
        supplier="euNetworks", layout_fingerprint="sha256:fp",
        training_intake_ids=training_ids,
    )


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def test_authoring_prompt_presents_all_examples():
    prompt = build_authoring_user_prompt(
        page_texts=["PRIMARY PAGE"],
        target={"line_items": [{"amount": "700"}]},
        additional_examples=[
            {"label": "intake-2", "target": {"line_items": [{"amount": "150"}]},
             "page_texts": ["SECOND PAGE"]},
        ],
    )
    assert "2 invoices of the SAME layout" in prompt
    assert "PRIMARY INVOICE GROUND TRUTH" in prompt
    assert "ADDITIONAL SAME-LAYOUT INVOICE #2 (intake-2)" in prompt
    assert "must ALSO reproduce this exactly" in prompt
    assert "SECOND PAGE" in prompt


def test_authoring_prompt_includes_extra_example_evidence():
    """Each additional invoice's layout evidence must reach the authoring AI."""
    prompt = build_authoring_user_prompt(
        page_texts=["PRIMARY PAGE"],
        target={"line_items": [{"amount": "700"}]},
        additional_examples=[
            {
                "label": "intake-2",
                "target": {"line_items": [{"amount": "150"}]},
                "page_texts": ["SECOND PAGE"],
                "layout_evidence": {"line_items_evidence": "ROW_AT_Y_512"},
            },
        ],
    )
    assert "where invoice #2's ground-truth values sit" in prompt
    assert "ROW_AT_Y_512" in prompt


# ---------------------------------------------------------------------------
# Set cap
# ---------------------------------------------------------------------------


def test_cap_training_set_keeps_newest_and_richest(db, monkeypatch, tmp_path):
    monkeypatch.setattr(training.settings, "model_training_max_set_size", 2)
    monkeypatch.setattr(training.settings, "completed_dir", tmp_path)
    # Richness proxy = ground-truth file size.
    for iid, size in [("a", 100), ("b", 5000), ("c", 50), ("d", 10)]:
        d = tmp_path / iid
        d.mkdir()
        (d / "canonical_invoice.json").write_text("x" * size, encoding="utf-8")

    capped = training._cap_training_set(db, _profile(), ["a", "b", "c", "d"])
    assert len(capped) == 2
    assert "d" in capped              # newest (last) always kept
    assert "b" in capped              # richest of the rest
    assert capped[0] == "d"


# ---------------------------------------------------------------------------
# train_model_on_set save gate
# ---------------------------------------------------------------------------


@pytest.fixture()
def stub_training(monkeypatch, tmp_path):
    """Stub authoring + disk loading so training runs without AI or files."""
    monkeypatch.setattr(training.settings, "completed_dir", tmp_path)
    monkeypatch.setattr(training, "write_model_review_artifacts", lambda *a, **k: [])

    ground_truth = {
        "inv-rich": _invoice("inv-rich", ["700", "150", "800"]),
        "inv-oneoff": _invoice("inv-oneoff", ["250"]),
    }

    def fake_load(db, iid, profile, fingerprint, extract_if_missing=True):
        gt = ground_truth.get(iid)
        if gt is None:
            return None, f"{iid}: missing", 0, 0
        ex = TrainingExample(intake_id=iid, ai_invoice=gt, document=LayoutDocument(pages=[]))
        ex.page_texts = ["page"]
        ex.layout_evidence = {}
        return ex, None, 0, 0

    monkeypatch.setattr(training, "_load_training_example", fake_load)

    saved = {"models": []}

    def fake_save(db, model):
        saved["models"].append(model)
        return SaveModelResult(model_id=model.model_id, action="created", persisted_status="candidate")

    monkeypatch.setattr(training, "save_model", fake_save)
    return ground_truth, saved


def test_saves_when_model_reproduces_whole_set(db, stub_training, monkeypatch):
    ground_truth, saved = stub_training

    def fake_author(**kwargs):
        ids = [kwargs["intake_id"]] + [e.intake_id for e in kwargs.get("extra_examples") or []]
        m = _model(ids)
        return AuthoringResult(model=m, tokens_input=1, tokens_output=1, duration_ms=1,
                               ai_model_name="t")

    # Model reproduces each invoice exactly (returns that invoice's ground truth).
    monkeypatch.setattr(training, "author_extraction_model", fake_author)
    monkeypatch.setattr(training, "execute_model",
                        lambda model, pdf, iid, document=None, skip_self_checks=False: ground_truth[iid])

    result = training.train_model_on_set(
        db, profile=_profile(), fingerprint="sha256:fp", layout_family_key=None,
        intake_ids=["inv-rich", "inv-oneoff"],
    )
    assert result.success is True
    assert len(saved["models"]) == 1
    assert result.per_invoice == {"inv-rich": True, "inv-oneoff": True}
    # Richest invoice is the primary.
    assert saved["models"][0].training_intake_ids[0] == "inv-rich"


def test_progress_emits_granular_authoring_stages(db, monkeypatch, tmp_path):
    """The run reports concrete sub-steps (author -> test -> holdout -> save) and
    drives each invoice through live validation states."""
    monkeypatch.setattr(training.settings, "completed_dir", tmp_path)
    monkeypatch.setattr(training, "write_model_review_artifacts", lambda *a, **k: [])

    gt = {
        "inv-rich": _invoice("inv-rich", ["700", "150", "800"]),
        "inv-2": _invoice("inv-2", ["250"]),
        "hold-1": _invoice("hold-1", ["300"]),
    }

    def fake_load(db, iid, profile, fingerprint, extract_if_missing=True):
        ex = TrainingExample(intake_id=iid, ai_invoice=gt[iid], document=LayoutDocument(pages=[]))
        ex.page_texts, ex.layout_evidence = ["p"], {}
        return ex, None, 0, 0

    def fake_author(**kwargs):
        ids = [kwargs["intake_id"]] + [e.intake_id for e in kwargs.get("extra_examples") or []]
        return AuthoringResult(model=_model(ids), tokens_input=1, tokens_output=1,
                               duration_ms=1, ai_model_name="t")

    monkeypatch.setattr(training, "_load_training_example", fake_load)
    monkeypatch.setattr(training, "author_extraction_model", fake_author)
    monkeypatch.setattr(training, "execute_model",
                        lambda model, pdf, iid, document=None, skip_self_checks=False: gt[iid])
    monkeypatch.setattr(training, "save_model",
                        lambda db, model: SaveModelResult(
                            model_id=model.model_id, action="created", persisted_status="candidate"))

    events: list[tuple[str, dict]] = []

    def on_progress(step, done, total, message, detail):
        events.append((step, dict(detail or {})))

    result = training.train_model_on_set(
        db, profile=_profile(), fingerprint="sha256:fp", layout_family_key=None,
        intake_ids=["inv-rich", "inv-2"], holdout_intake_ids=["hold-1"],
        on_progress=on_progress,
    )
    assert result.success is True

    steps = [s for s, _ in events]
    for stage in ("extracting", "authoring", "testing", "holdout", "saving", "done"):
        assert stage in steps, f"missing stage {stage} in {steps}"
    assert (steps.index("authoring") < steps.index("testing")
            < steps.index("holdout") < steps.index("saving") < steps.index("done"))

    # Each invoice (train + holdout) reaches a live "passed" validation state.
    passed_ids = {
        d["invoice_update"]["intake_id"]
        for _, d in events
        if d.get("invoice_update", {}).get("state") == "passed"
    }
    assert {"inv-rich", "inv-2", "hold-1"} <= passed_ids


def test_blocks_save_when_one_member_fails(db, stub_training, monkeypatch):
    ground_truth, saved = stub_training

    def fake_author(**kwargs):
        ids = [kwargs["intake_id"]] + [e.intake_id for e in kwargs.get("extra_examples") or []]
        return AuthoringResult(model=_model(ids), tokens_input=1, tokens_output=1,
                               duration_ms=1, ai_model_name="t")

    monkeypatch.setattr(training, "author_extraction_model", fake_author)

    # The model mis-reads the one-off invoice's amount (the classic
    # single-invoice failure) -> output differs from ground truth -> must NOT save.
    def fake_execute(model, pdf, iid, document=None, skip_self_checks=False):
        if iid == "inv-oneoff":
            return _invoice("inv-oneoff", ["999"])  # wrong amount -> mismatch
        return ground_truth[iid]

    monkeypatch.setattr(training, "execute_model", fake_execute)

    result = training.train_model_on_set(
        db, profile=_profile(), fingerprint="sha256:fp", layout_family_key=None,
        intake_ids=["inv-rich", "inv-oneoff"],
    )
    assert result.success is False
    assert saved["models"] == []
    assert result.per_invoice["inv-rich"] is True
    assert result.per_invoice["inv-oneoff"] is False
    record = crud.get_extraction_model(db, "eunetworks.standard.v2")
    assert record is not None
    assert record.status == "failed_validation"
    assert record.last_validation_error
    assert record.validation_attempt_count == 0
    assert record.validation_failure_count == 0
    assert "inv-oneoff" in record.last_validation_error
    assert record.model_json["status"] == "failed_validation"
    assert record.model_json["training_intake_ids"] == ["inv-rich", "inv-oneoff"]
    assert record.model_json["layout_fingerprint"] == "sha256:fp"


def test_failed_validation_model_can_be_retrained_to_candidate(db, stub_training, monkeypatch):
    ground_truth, _saved = stub_training
    registry.save_failed_model(db, _model(["old"]), "first attempt failed")
    monkeypatch.setattr(training, "save_model", registry.save_model)

    def fake_author(**kwargs):
        ids = [kwargs["intake_id"]] + [e.intake_id for e in kwargs.get("extra_examples") or []]
        return AuthoringResult(model=_model(ids), tokens_input=1, tokens_output=1,
                               duration_ms=1, ai_model_name="t")

    monkeypatch.setattr(training, "author_extraction_model", fake_author)
    monkeypatch.setattr(training, "execute_model",
                        lambda model, pdf, iid, document=None, skip_self_checks=False: ground_truth[iid])

    result = training.train_model_on_set(
        db, profile=_profile(), fingerprint="sha256:fp", layout_family_key=None,
        intake_ids=["inv-rich", "inv-oneoff"],
    )
    assert result.success is True
    record = crud.get_extraction_model(db, "eunetworks.standard.v2")
    assert record.status == "candidate"
    assert record.last_validation_error is None
    assert record.model_json["status"] == "candidate"
    assert record.model_json["training_intake_ids"] == ["inv-rich", "inv-oneoff"]


def test_refine_loop_retries_failed_diff_then_succeeds(db, stub_training, monkeypatch):
    """First authored model misreads an invoice; the diff feedback drives a second
    authoring attempt that reproduces the whole set -> saved with attempts=2."""
    ground_truth, saved = stub_training
    monkeypatch.setattr(training.settings, "model_authoring_max_attempts", 3)

    state = {"author_calls": 0}

    def fake_author(**kwargs):
        state["author_calls"] += 1
        ids = [kwargs["intake_id"]] + [e.intake_id for e in kwargs.get("extra_examples") or []]
        return AuthoringResult(model=_model(ids), tokens_input=1, tokens_output=1,
                               duration_ms=1, ai_model_name="t")

    # The model only misreads the one-off invoice on the FIRST authoring attempt.
    def fake_execute(model, pdf, iid, document=None, skip_self_checks=False):
        if state["author_calls"] <= 1 and iid == "inv-oneoff":
            return _invoice("inv-oneoff", ["999"])
        return ground_truth[iid]

    monkeypatch.setattr(training, "author_extraction_model", fake_author)
    monkeypatch.setattr(training, "execute_model", fake_execute)

    result = training.train_model_on_set(
        db, profile=_profile(), fingerprint="sha256:fp", layout_family_key=None,
        intake_ids=["inv-rich", "inv-oneoff"],
    )
    assert result.success is True
    assert result.attempts == 2
    assert state["author_calls"] == 2
    assert len(saved["models"]) == 1


def test_refine_loop_stops_at_max_attempts(db, stub_training, monkeypatch):
    """A persistently-wrong model is re-authored up to the cap, then fails."""
    ground_truth, saved = stub_training
    monkeypatch.setattr(training.settings, "model_authoring_max_attempts", 2)

    state = {"author_calls": 0}

    def fake_author(**kwargs):
        state["author_calls"] += 1
        ids = [kwargs["intake_id"]] + [e.intake_id for e in kwargs.get("extra_examples") or []]
        return AuthoringResult(model=_model(ids), tokens_input=1, tokens_output=1,
                               duration_ms=1, ai_model_name="t")

    def fake_execute(model, pdf, iid, document=None, skip_self_checks=False):
        if iid == "inv-oneoff":
            return _invoice("inv-oneoff", ["999"])  # always wrong
        return ground_truth[iid]

    monkeypatch.setattr(training, "author_extraction_model", fake_author)
    monkeypatch.setattr(training, "execute_model", fake_execute)

    result = training.train_model_on_set(
        db, profile=_profile(), fingerprint="sha256:fp", layout_family_key=None,
        intake_ids=["inv-rich", "inv-oneoff"],
    )
    assert result.success is False
    assert result.attempts == 2
    assert state["author_calls"] == 2
    assert saved["models"] == []
    # Rich per-invoice detail is recorded for the workbench.
    assert result.per_invoice_detail["inv-rich"]["ok"] is True
    assert result.per_invoice_detail["inv-oneoff"]["ok"] is False
    assert result.per_invoice_detail["inv-oneoff"]["expected_lines"] == 1


def test_tax_warning_when_grouped_model_uses_invoice_rate_for_per_line_tax():
    ai_invoice = _invoice("ai", ["100.00"])
    ai_invoice.rows[0]["tax_amount"] = Decimal("21.00")
    ai_invoice.subtotal = Decimal("100.00")
    ai_invoice.tax = Decimal("21.00")
    ai_invoice.tax_rate = Decimal("0.21")
    model_invoice = _invoice("model", ["100.00"])
    model_invoice.tax_rate = Decimal("0.21")
    model = _model(["ai"])
    model.line_item_strategy = LineItemStrategy(
        granularity="per_charge_row",
        service_id_preference="first_identifier",
    )

    warning = training._tax_strategy_warning(model, ai_invoice, model_invoice)

    assert warning is not None
    assert "tax_rate" in warning


def test_load_training_example_populates_fingerprint_during_training_load(db, monkeypatch, tmp_path):
    monkeypatch.setattr(training.settings, "archive_dir", tmp_path / "archive")
    monkeypatch.setattr(training.settings, "completed_dir", tmp_path / "completed")
    record = crud.create_intake(
        db,
        original_filename="invoice.pdf",
        original_pdf_path="invoice.pdf",
        archive_pdf_path="invoice.pdf",
        supplier_profile_id=_profile().profile_id,
    )
    invoice_dir = training.settings.archive_dir / record.id
    invoice_dir.mkdir(parents=True)
    (invoice_dir / "original.pdf").write_bytes(b"%PDF-1.4\n")

    from stencil.fingerprint import fingerprinter

    monkeypatch.setattr(
        fingerprinter,
        "fingerprint_pdf",
        lambda pdf_path, profile=None: ("sha256:expected", object()),
    )
    monkeypatch.setattr(training, "extract_layout_document", lambda _pdf_path: LayoutDocument(pages=[]))
    monkeypatch.setattr(
        training,
        "_extract_ground_truth",
        lambda db, intake_id, pdf_path, document, profile, fingerprint: (
            _invoice(intake_id, ["100.00"]),
            0,
            0,
        ),
    )

    example, error, _tokens_in, _tokens_out = training._load_training_example(
        db, record.id, _profile(), "sha256:expected", extract_if_missing=True,
    )

    assert example is not None
    assert error is None
    assert crud.get_intake(db, record.id).layout_fingerprint == "sha256:expected"


def test_load_training_example_rejects_mixed_fingerprint(db, monkeypatch, tmp_path):
    monkeypatch.setattr(training.settings, "archive_dir", tmp_path / "archive")
    monkeypatch.setattr(training.settings, "completed_dir", tmp_path / "completed")
    record = crud.create_intake(
        db,
        original_filename="invoice.pdf",
        original_pdf_path="invoice.pdf",
        archive_pdf_path="invoice.pdf",
        supplier_profile_id=_profile().profile_id,
    )
    invoice_dir = training.settings.archive_dir / record.id
    invoice_dir.mkdir(parents=True)
    (invoice_dir / "original.pdf").write_bytes(b"%PDF-1.4\n")

    from stencil.fingerprint import fingerprinter

    monkeypatch.setattr(
        fingerprinter,
        "fingerprint_pdf",
        lambda pdf_path, profile=None: ("sha256:actual", object()),
    )

    example, error, _tokens_in, _tokens_out = training._load_training_example(
        db, record.id, _profile(), "sha256:expected", extract_if_missing=True,
    )

    assert example is None
    assert "layout fingerprint mismatch" in error
    assert "sha256:expected" in error
    assert "sha256:actual" in error
    assert crud.get_intake(db, record.id).layout_fingerprint == "sha256:actual"


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------


def test_gate_blocks_under_min_count(db, monkeypatch):
    monkeypatch.setattr(training.settings, "model_validation_required_successes", 3)
    gt = _invoice("only", ["700"])

    def fake_load(db, iid, profile, fingerprint, extract_if_missing=True):
        ex = TrainingExample(intake_id=iid, ai_invoice=gt, document=LayoutDocument(pages=[]))
        ex.page_texts, ex.layout_evidence = ["p"], {}
        return ex, None, 0, 0

    monkeypatch.setattr(training, "_load_training_example", fake_load)
    monkeypatch.setattr(training, "execute_model",
                        lambda model, pdf, iid, document=None, skip_self_checks=False: gt)

    gate = training.evaluate_model_training_set(db, _model(["only"]), _profile())
    assert gate["reproduces_all"] is True
    assert gate["approvable"] is False
    assert any("at least 3" in r for r in gate["reasons"])


def test_gate_passes_with_min_count_all_reproduced(db, monkeypatch):
    monkeypatch.setattr(training.settings, "model_validation_required_successes", 3)
    gts = {i: _invoice(i, ["700"]) for i in ("a", "b", "c")}

    def fake_load(db, iid, profile, fingerprint, extract_if_missing=True):
        ex = TrainingExample(intake_id=iid, ai_invoice=gts[iid], document=LayoutDocument(pages=[]))
        ex.page_texts, ex.layout_evidence = ["p"], {}
        return ex, None, 0, 0

    monkeypatch.setattr(training, "_load_training_example", fake_load)
    monkeypatch.setattr(training, "execute_model",
                        lambda model, pdf, iid, document=None, skip_self_checks=False: gts[iid])

    gate = training.evaluate_model_training_set(db, _model(["a", "b", "c"]), _profile())
    assert gate["approvable"] is True
    assert gate["reasons"] == []
