"""Editing an existing profile with AI: profile <-> draft round-trip and the
finalize merge that saves a new version while preserving non-AI config."""

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from stencil.extraction.layout import LayoutDocument
from stencil.fields.loader import default_field_schema
from stencil.output.spec import OutputColumn, OutputSpec
from stencil.profiles.authoring import (
    draft_to_supplier_profile_dict,
    supplier_profile_to_draft,
)
from stencil.profiles.schema import SupplierProfile
from stencil.validation.schema import (
    CanonicalInvoice,
    ExtractionMetadata,
    ExtractionPath,
    InvoiceHeader,
    LineItem,
)


def _source_profile() -> SupplierProfile:
    return SupplierProfile.model_validate({
        "profile_id": "acme.standard.v1",
        "version": 3,
        "status": "active",
        "identity": {"canonical_name": "Acme Corp", "aliases": ["ACME", "Acme Inc"]},
        "classification": {"output_type": "standard"},
        "output_spec_id": "temforce.standard",
        "field_schema_id": "invoice.standard",
        "field_overrides": [
            {
                "name": "invoice_date",
                "scope": "document",
                "type": "date",
                "role": "none",
                "label_hint": "Invoice Date",
                "date_format": "%d/%m/%Y",
            },
            {
                "name": "plan_cost",
                "scope": "row",
                "type": "currency",
                "role": "none",
                "label_hint": None,
            }
        ],
        "output_mapping_overrides": [
            {
                "output_header": "EXT_DATE",
                "source": "row.billing_period_start",
                "fallback": "field.invoice_date",
            }
        ],
        "delivery": {"accounts": [
            {"label": "East", "inbound_path": "C:/in/east", "output_path": "C:/out/east"},
            {"label": "West", "inbound_path": "C:/in/west", "output_path": "C:/out/west"},
        ]},
        "training_config": {"min_validation_successes": 5, "require_reconciliation": True},
        "advanced": {
            "document_structure": {"detail_start_marker": "Charges", "detail_end_marker": "Total Due"},
            "document_field_defaults": {"account_number": "East"},
            "line_item_hints": {
                "line_item_granularity": "per_charge_row",
                "service_id_preference": "parent_identifier",
                "amount_column_label": "Charges",
                "amount_source": "table_charges_column",
                "tax_source": "table_tax_column",
                "tax_output_mode": "extract_exact",
                "skip_row_keywords": ["carried forward"],
            },
            "require_line_item_identifier": True,
        },
        "owner": "denis",
    })


def test_profile_to_draft_round_trips_hints():
    prof = _source_profile()
    draft = supplier_profile_to_draft(prof)
    authored = draft_to_supplier_profile_dict(
        draft, output_spec_id=prof.output_spec_id, field_schema_id=prof.field_schema_id,
        field_schema=default_field_schema(),
    )
    rebuilt = SupplierProfile.model_validate({**authored, "profile_id": "acme.standard.v1", "status": "draft"})

    assert rebuilt.identity.canonical_name == "Acme Corp"
    assert rebuilt.identity.aliases == ["ACME", "Acme Inc"]
    assert rebuilt.line_item_hints.line_item_granularity == "per_charge_row"
    assert rebuilt.line_item_hints.service_id_preference == "parent_identifier"
    assert rebuilt.line_item_hints.amount_column_label == "Charges"
    assert rebuilt.line_item_hints.amount_source == "table_charges_column"
    assert rebuilt.line_item_hints.tax_source == "table_tax_column"
    assert rebuilt.line_item_hints.tax_output_mode == "extract_exact"
    assert rebuilt.line_item_hints.skip_row_keywords == ["carried forward"]
    assert rebuilt.document_structure.detail_start_marker == "Charges"
    assert rebuilt.document_structure.detail_end_marker == "Total Due"
    assert rebuilt.advanced.require_line_item_identifier is True
    assert rebuilt.output_mapping_overrides[0].source == "row.billing_period_start"


def test_finalize_merge_preserves_non_ai_config_as_new_version():
    """The edit-mode finalize layers authored hints over the source so delivery,
    training, owner, and version lineage carry into the new version."""
    prof = _source_profile()
    draft = supplier_profile_to_draft(prof)
    authored = draft_to_supplier_profile_dict(
        draft, output_spec_id=prof.output_spec_id, field_schema_id=prof.field_schema_id,
        field_schema=default_field_schema(),
    )
    from stencil.api.profile_authoring import _merge_source_profile_config

    merged = _merge_source_profile_config(prof, authored)
    merged["version"] = prof.version + 1
    merged["profile_id"] = "acme.standard.v2"
    merged["status"] = "draft"
    new = SupplierProfile.model_validate(merged)

    assert new.profile_id == "acme.standard.v2"
    assert new.version == 4
    assert new.status == "draft"
    # Non-AI config preserved from the source.
    assert [a.label for a in new.delivery.accounts] == ["East", "West"]
    assert new.training_config.min_validation_successes == 5
    assert new.owner == "denis"
    assert new.advanced.document_field_defaults == {"account_number": "East"}
    assert any(field.name == "plan_cost" for field in new.field_overrides)
    invoice_date = next(field for field in new.field_overrides if field.name == "invoice_date")
    assert invoice_date.date_format == "%d/%m/%Y"
    assert new.output_mapping_overrides[0].source == "row.billing_period_start"
    # AI-authored hints carried through.
    assert new.line_item_hints.line_item_granularity == "per_charge_row"


def test_scoped_field_path_selects_row_field_without_name_ambiguity():
    draft = {
        "profile": {
            **supplier_profile_to_draft(_source_profile())["profile"],
            "field_overrides": [
                {
                    "field_path": "row.billing_period_start",
                    "label_hint": "Billing Period",
                    "date_format": None,
                }
            ],
        }
    }

    authored = draft_to_supplier_profile_dict(
        draft,
        output_spec_id="temforce.standard",
        field_schema_id="invoice.standard",
        field_schema=default_field_schema(),
    )

    assert authored["field_overrides"][0]["scope"] == "row"


def test_edit_merge_uses_complete_authored_mapping_list_and_legacy_omission_preserves():
    from stencil.api.profile_authoring import _merge_source_profile_config

    source = _source_profile()
    reset = _merge_source_profile_config(source, {"output_mapping_overrides": []})
    assert reset["output_mapping_overrides"] == []

    legacy = _merge_source_profile_config(source, {"notes": "Legacy authored draft"})
    assert legacy["output_mapping_overrides"] == source.model_dump(mode="json")[
        "output_mapping_overrides"]


def test_edit_with_ai_endpoints_and_tasks_are_wired():
    import inspect

    from stencil.api.profile_authoring import CreateSessionRequest
    from stencil.db.crud import create_authoring_session
    from stencil.profiles import authoring_runtime
    from stencil.tasks.worker import authoring_reextract_task  # noqa: F401

    assert "source_profile_id" in CreateSessionRequest.model_fields
    assert "source_profile_id" in inspect.signature(create_authoring_session).parameters
    assert "draft_profile" in inspect.signature(create_authoring_session).parameters
    assert hasattr(authoring_runtime, "_reextract_done_invoices_with_draft")


def test_authoring_preview_and_blueprint_diff_use_effective_output_mapping(
    tmp_path, monkeypatch,
):
    from stencil.profiles import authoring_runtime

    spec = OutputSpec(
        spec_id="date.mapping",
        columns=[
            OutputColumn(header="EXT_DATE", source="field.invoice_date"),
            OutputColumn(header="formula", source="field.due_date"),
        ],
    )
    monkeypatch.setattr(authoring_runtime, "get_output_spec", lambda _spec_id: spec)
    monkeypatch.setattr("stencil.specs.loader.get_output_spec", lambda _spec_id: spec)
    monkeypatch.setattr(authoring_runtime, "get_field_schema", lambda _schema_id: default_field_schema())

    invoice = CanonicalInvoice(
        intake_id="authoring-date-map",
        header=InvoiceHeader(
            supplier_name="Acme",
            invoice_number="INV-1",
            invoice_date=date(2026, 8, 3),
            due_date=date(2026, 8, 31),
        ),
        line_items=[LineItem(
            line_number=1,
            description="service",
            amount=Decimal("100"),
            billing_period_start=date(2026, 7, 1),
            billing_period_end=date(2026, 7, 31),
        )],
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )
    (tmp_path / "extracted.json").write_text(
        json.dumps(invoice.model_dump(mode="json")), encoding="utf-8")
    (tmp_path / "layout.json").write_text(
        json.dumps(LayoutDocument(pages=[]).model_dump(mode="json")), encoding="utf-8")
    (tmp_path / "expected_rows.json").write_text(
        json.dumps([["07/01/2026", "07/31/2026"]]), encoding="utf-8")
    sample = SimpleNamespace(id="sample-1", filename="sample.pdf", extraction_status="done", artifact_dir=str(tmp_path))
    monkeypatch.setattr(authoring_runtime.crud, "list_authoring_invoices", lambda _db, _id: [sample])
    session = SimpleNamespace(
        id="session-1",
        output_spec_id=spec.spec_id,
        field_schema_id="invoice.standard",
        source_profile_id=None,
    )
    draft = {"profile": {
        "output_mapping_overrides": [
            {
                "output_header": "EXT_DATE",
                "source": "row.billing_period_start",
                "fallback": "field.invoice_date",
            },
            {
                "output_header": "formula",
                "source": "row.billing_period_end",
                "fallback": "field.due_date",
            },
        ]
    }}

    previews = authoring_runtime._previews_for_draft(object(), session, draft)

    assert previews["sample-1"]["preview"]["rows"] == [["07/01/2026", "07/31/2026"]]
    assert previews["sample-1"]["diff"]["is_match"] is True


def test_authoring_preview_keeps_failed_samples_visible(monkeypatch):
    from stencil.profiles import authoring_runtime

    sample = SimpleNamespace(
        id="sample-1",
        filename="sample.pdf",
        extraction_status="error",
        error_message="date normalization failed",
    )
    monkeypatch.setattr(authoring_runtime.crud, "list_authoring_invoices", lambda _db, _id: [sample])
    monkeypatch.setattr(authoring_runtime, "_preview_profile_from_draft", lambda session, draft: object())
    monkeypatch.setattr(authoring_runtime, "resolve_output_spec", lambda profile: SimpleNamespace(columns=[]))
    monkeypatch.setattr(authoring_runtime, "output_spec_to_columns", lambda spec: [])

    previews = authoring_runtime._previews_for_draft(
        object(), SimpleNamespace(id="session-1"), {"profile": {}}
    )

    assert previews["sample-1"] == {
        "filename": "sample.pdf",
        "error": "date normalization failed",
    }


def test_initial_authoring_extraction_uses_selected_field_schema(tmp_path, monkeypatch):
    from stencil.profiles import authoring_runtime

    artifact_dir = tmp_path / "sample"
    artifact_dir.mkdir()
    (artifact_dir / "source.pdf").write_bytes(b"pdf")
    session = SimpleNamespace(
        id="session-1",
        field_schema_id="custom.selected",
        source_profile_id=None,
        output_spec_id="temforce.standard",
    )
    invoice = SimpleNamespace(id="invoice-1", artifact_dir=str(artifact_dir), has_expected=False)
    selected_schema = SimpleNamespace(schema_id="custom.selected")
    captured = {}
    monkeypatch.setattr(authoring_runtime.crud, "get_authoring_session", lambda _db, _id: session)
    monkeypatch.setattr(authoring_runtime.crud, "get_authoring_invoice", lambda _db, _id: invoice)
    monkeypatch.setattr(authoring_runtime.crud, "update_authoring_invoice", lambda *args, **kwargs: None)
    monkeypatch.setattr(authoring_runtime, "get_field_schema", lambda schema_id: selected_schema)
    monkeypatch.setattr(authoring_runtime, "_parse_expected_blueprint", lambda *args: None)
    monkeypatch.setattr(
        authoring_runtime,
        "_extract_and_cache",
        lambda *args, **kwargs: captured.update(field_schema=kwargs["field_schema"]),
    )

    authoring_runtime.run_extraction(object(), session_id=session.id, invoice_id=invoice.id)

    assert captured["field_schema"] is selected_schema


def test_authoring_turn_auto_reextracts_refine_turns_with_existing_draft():
    import inspect

    from stencil.profiles.authoring_runtime import run_turn

    source = inspect.getsource(run_turn)
    assert "previous_draft = session.draft_profile" in source
    assert 'if i.extraction_status in {"uploaded", "done"}' in source
    assert "if previous_draft and candidate_invoice_ids" in source
    assert 'elif candidate_invoice_ids:' in source
    assert 'elif result.selected_plan_id == "deterministic.v1"' not in source
    assert "_reextract_done_invoices_with_draft" in source
    assert '"reextracted_invoice_ids": reextracted_invoice_ids' in source


def test_authoring_queue_serializes_turns_in_order(isolated_db, monkeypatch):
    from stencil.db import crud
    from stencil.db.session import SessionLocal
    from stencil.profiles import authoring_runtime
    from stencil.tasks.worker import _run_authoring_queue

    seen: list[str] = []

    def fake_run_turn(db, *, session_id: str, user_message: str):
        running = crud.list_authoring_jobs(db, session_id, statuses={"running"})
        queued = crud.list_authoring_jobs(db, session_id, statuses={"queued"})
        assert len(running) == 1
        if user_message == "first":
            assert [j.message for j in queued] == ["second"]
        seen.append(user_message)
        session = crud.get_authoring_session(db, session_id)
        conversation = [*list(session.conversation or []), {"role": "user", "content": user_message}]
        crud.update_authoring_session(db, session_id, conversation=conversation)
        return {"assistant_message": f"handled {user_message}", "draft_profile": {}, "previews": {}}

    monkeypatch.setattr(authoring_runtime, "run_turn", fake_run_turn)

    db = SessionLocal()
    try:
        session = crud.create_authoring_session(
            db, supplier_name="Acme", output_spec_id="temforce.standard",
            field_schema_id="invoice.standard",
        )
        crud.add_authoring_invoice(
            db, session_id=session.id, filename="sample.pdf",
            has_expected=False, artifact_dir="unused",
        )
        first = crud.create_authoring_job(db, session_id=session.id, kind="turn", message="first")
        second = crud.create_authoring_job(db, session_id=session.id, kind="turn", message="second")

        result = _run_authoring_queue(db, session.id)

        assert result["processed"] == 2
        assert seen == ["first", "second"]
        assert crud.get_authoring_session(db, session.id).status == "active"
        assert crud.get_authoring_job(db, first.id).status == "done"
        assert crud.get_authoring_job(db, second.id).status == "done"
    finally:
        db.close()


def test_cancel_authoring_revokes_all_session_runners_and_resets_state(isolated_db, monkeypatch):
    from stencil.api.profile_authoring import cancel_session_authoring
    from stencil.db import crud
    from stencil.db.session import SessionLocal
    from stencil.tasks import worker

    revoked: list[tuple[list[str], bool]] = []
    monkeypatch.setattr(
        worker.app.control,
        "revoke",
        lambda task_ids, terminate=False: revoked.append((task_ids, terminate)),
    )

    db = SessionLocal()
    try:
        session = crud.create_authoring_session(
            db, supplier_name="Acme", output_spec_id="temforce.standard",
            field_schema_id="invoice.standard",
        )
        invoice = crud.add_authoring_invoice(
            db, session_id=session.id, filename="sample.pdf",
            has_expected=False, artifact_dir="unused",
        )
        old_runner = crud.create_authoring_job(
            db, session_id=session.id, kind="turn", message="first")
        current = crud.create_authoring_job(
            db, session_id=session.id, kind="turn", message="second")
        queued = crud.create_authoring_job(
            db, session_id=session.id, kind="turn", message="third")
        crud.update_authoring_job(db, old_runner.id, status="done")
        crud.update_authoring_job(db, current.id, status="running")
        crud.update_authoring_invoice(db, invoice.id, extraction_status="pending")
        crud.update_authoring_session(db, session.id, status="running")

        result = cancel_session_authoring(db, session.id)

        assert result == {"status": "cancelled", "cancelled_jobs": 2}
        assert revoked == [([old_runner.id, current.id, queued.id], True)]
        assert crud.get_authoring_job(db, old_runner.id).status == "done"
        assert crud.get_authoring_job(db, current.id).status == "cancelled"
        assert crud.get_authoring_job(db, queued.id).status == "cancelled"
        assert crud.get_authoring_invoice(db, invoice.id).extraction_status == "uploaded"
        assert crud.get_authoring_session(db, session.id).status == "active"
    finally:
        db.close()


def test_authoring_dispatch_uses_job_id_as_celery_task_id(monkeypatch):
    from stencil.api.profile_authoring import _enqueue_runner
    from stencil.tasks import worker

    dispatched = []
    monkeypatch.setattr(
        worker.authoring_turn_task,
        "apply_async",
        lambda *, args, task_id: dispatched.append((args, task_id)),
    )

    _enqueue_runner("session-1", "abc12345", kind="turn", message="draft it")

    assert dispatched == [(["session-1", "abc12345", "draft it"], "abc12345")]


def test_delete_uploaded_authoring_invoice_removes_artifacts_and_preview(isolated_db, tmp_path, monkeypatch):
    from stencil.api.profile_authoring import delete_invoice
    from stencil.db import crud
    from stencil.db.session import SessionLocal
    from stencil.profiles import authoring_runtime

    monkeypatch.setattr(authoring_runtime.settings, "work_dir", tmp_path)

    db = SessionLocal()
    try:
        session = crud.create_authoring_session(
            db, supplier_name="Acme", output_spec_id="temforce.standard",
            field_schema_id="invoice.standard",
        )
        artifact_dir = tmp_path / "authoring" / session.id / "inv1"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "source.pdf").write_text("pdf", encoding="utf-8")
        (artifact_dir / "blueprint.xlsx").write_text("xlsx", encoding="utf-8")
        invoice = crud.add_authoring_invoice(
            db, session_id=session.id, filename="sample.pdf",
            has_expected=True, artifact_dir=str(artifact_dir),
        )
        crud.update_authoring_session(db, session.id, previews={invoice.id: {"filename": "sample.pdf"}})

        response = delete_invoice(db, session.id, invoice.id)

        assert response == {"invoice_id": invoice.id, "status": "deleted"}
        assert crud.get_authoring_invoice(db, invoice.id) is None
        assert not artifact_dir.exists()
        assert crud.get_authoring_session(db, session.id).previews == {}
    finally:
        db.close()


def test_delete_authoring_invoice_rejects_finalized_or_busy_sessions(isolated_db, tmp_path, monkeypatch):
    from fastapi import HTTPException

    from stencil.api.profile_authoring import delete_invoice
    from stencil.db import crud
    from stencil.db.session import SessionLocal
    from stencil.profiles import authoring_runtime

    monkeypatch.setattr(authoring_runtime.settings, "work_dir", tmp_path)

    db = SessionLocal()
    try:
        session = crud.create_authoring_session(
            db, supplier_name="Acme", output_spec_id="temforce.standard",
            field_schema_id="invoice.standard",
        )
        artifact_dir = tmp_path / "authoring" / session.id / "inv1"
        artifact_dir.mkdir(parents=True)
        invoice = crud.add_authoring_invoice(
            db, session_id=session.id, filename="sample.pdf",
            has_expected=False, artifact_dir=str(artifact_dir),
        )
        crud.create_authoring_job(db, session_id=session.id, kind="turn", message="queued")

        try:
            delete_invoice(db, session.id, invoice.id)
            raise AssertionError("expected busy delete to be rejected")
        except HTTPException as exc:
            assert exc.status_code == 409

        for job in crud.list_authoring_jobs(db, session.id):
            crud.update_authoring_job(db, job.id, status="done")
        crud.update_authoring_session(db, session.id, status="finalized")

        try:
            delete_invoice(db, session.id, invoice.id)
            raise AssertionError("expected finalized delete to be rejected")
        except HTTPException as exc:
            assert exc.status_code == 400
    finally:
        db.close()


def test_reextract_with_draft_includes_new_uploaded_samples(isolated_db, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from stencil.db import crud
    from stencil.db.session import SessionLocal
    from stencil.profiles import authoring_runtime

    extracted: list[str] = []
    parsed: list[str] = []

    def fake_extract_and_cache(db, *, session, invoice_id, pdf, artifact_dir, call_type, profile, field_schema):
        extracted.append(invoice_id)

    def fake_parse_blueprint(session, invoice, artifact_dir):
        parsed.append(invoice.id)

    monkeypatch.setattr(authoring_runtime, "_preview_profile_from_draft", lambda session, draft: SimpleNamespace())
    monkeypatch.setattr(authoring_runtime, "resolve_merged_field_schema", lambda profile: SimpleNamespace())
    monkeypatch.setattr(authoring_runtime, "_extract_and_cache", fake_extract_and_cache)
    monkeypatch.setattr(authoring_runtime, "_parse_expected_blueprint", fake_parse_blueprint)

    db = SessionLocal()
    try:
        session = crud.create_authoring_session(
            db, supplier_name="Acme", output_spec_id="temforce.standard",
            field_schema_id="invoice.standard", draft_profile={"profile": {}},
        )
        done_dir = tmp_path / "done"
        uploaded_dir = tmp_path / "uploaded"
        done_dir.mkdir()
        uploaded_dir.mkdir()
        (done_dir / "source.pdf").write_text("pdf", encoding="utf-8")
        (uploaded_dir / "source.pdf").write_text("pdf", encoding="utf-8")
        done = crud.add_authoring_invoice(
            db, session_id=session.id, filename="done.pdf",
            has_expected=False, artifact_dir=str(done_dir),
        )
        crud.update_authoring_invoice(db, done.id, extraction_status="done")
        uploaded = crud.add_authoring_invoice(
            db, session_id=session.id, filename="new.pdf",
            has_expected=True, artifact_dir=str(uploaded_dir),
        )

        reextracted = authoring_runtime._reextract_done_invoices_with_draft(
            db, session_id=session.id, draft=session.draft_profile)

        assert reextracted == [done.id, uploaded.id]
        assert extracted == [done.id, uploaded.id]
        assert parsed == [done.id, uploaded.id]
        assert crud.get_authoring_invoice(db, uploaded.id).extraction_status == "done"
    finally:
        db.close()


def test_authoring_reextract_can_continue_after_one_sample_fails(isolated_db, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from stencil.db import crud
    from stencil.db.session import SessionLocal
    from stencil.profiles import authoring_runtime

    monkeypatch.setattr(authoring_runtime, "_preview_profile_from_draft", lambda session, draft: SimpleNamespace())
    monkeypatch.setattr(authoring_runtime, "resolve_merged_field_schema", lambda profile: SimpleNamespace())
    monkeypatch.setattr(authoring_runtime, "_parse_expected_blueprint", lambda *args: None)

    failed_id = None

    def fake_extract(db, *, invoice_id, **kwargs):
        if invoice_id == failed_id:
            raise AttributeError("nullable label")

    monkeypatch.setattr(authoring_runtime, "_extract_and_cache", fake_extract)

    db = SessionLocal()
    try:
        session = crud.create_authoring_session(
            db, supplier_name="Acme", output_spec_id="temforce.standard",
            field_schema_id="invoice.standard", draft_profile={"profile": {}},
        )
        failed_dir = tmp_path / "failed"
        good_dir = tmp_path / "good"
        failed_dir.mkdir()
        good_dir.mkdir()
        (failed_dir / "source.pdf").write_bytes(b"pdf")
        (good_dir / "source.pdf").write_bytes(b"pdf")
        failed = crud.add_authoring_invoice(
            db, session_id=session.id, filename="failed.pdf",
            has_expected=False, artifact_dir=str(failed_dir),
        )
        failed_id = failed.id
        good = crud.add_authoring_invoice(
            db, session_id=session.id, filename="good.pdf",
            has_expected=False, artifact_dir=str(good_dir),
        )

        reextracted = authoring_runtime._reextract_done_invoices_with_draft(
            db,
            session_id=session.id,
            draft=session.draft_profile,
            continue_on_error=True,
        )

        assert reextracted == [good.id]
        assert crud.get_authoring_invoice(db, failed.id).extraction_status == "error"
        assert crud.get_authoring_invoice(db, good.id).extraction_status == "done"
    finally:
        db.close()
