"""Extraction model API endpoints."""

import json
import math
from pathlib import Path
from uuid import uuid4

import structlog
from fastapi import APIRouter, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from stencil.api.deps import CurrentUser, DbSession, Pagination
from stencil.api.schemas import (
    ExtractionModelResponse,
    ExtractionPreviewResponse,
    MessageResponse,
    PaginatedResponse,
)
from stencil.api.uploads import stream_upload_to, validate_upload_filename
from stencil.audit import actor_label, append_event
from stencil.config import settings
from stencil.db import crud
from stencil.models import registry

logger = structlog.get_logger()

router = APIRouter(prefix="/models", tags=["models"])


class ApproveRequest(BaseModel):
    approved_by: str | None = None


class RollbackRequest(BaseModel):
    approved_by: str | None = None


class RulesRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_json: dict


class TestRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    intake_id: str
    model_json: dict | None = None


class DraftTestRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    intake_id: str
    model_json: dict
    # The deliverable to preview against when the draft is not yet bound to a
    # supplier profile. Ignored once it is — the profile is the authority.
    output_spec_id: str | None = None
    field_schema_id: str | None = None


class DraftEvalFolderRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_json: dict
    layout_id: str


class TrainRequest(BaseModel):
    add_intake_ids: list[str] = []


class TrainingSetRequest(BaseModel):
    """The intended Train/Holdout split for a candidate model."""

    train_intake_ids: list[str] = []
    holdout_intake_ids: list[str] = []


class BootstrapRequest(BaseModel):
    supplier_profile_id: str


def _load_model_and_profile(db, model_id: str):
    """Resolve a model record + its parsed model and owning profile, or 404."""
    from stencil.models.schema import ExtractionModel
    from stencil.profiles.loader import find_profile_by_supplier, get_profile

    record = crud.get_extraction_model(db, model_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction model not found")
    try:
        model = ExtractionModel.model_validate(record.model_json)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Stored model is unreadable: {e}")
    profile = get_profile(record.supplier_profile_id) if record.supplier_profile_id else None
    if profile is None:
        profile = find_profile_by_supplier(record.supplier, active_only=False)
    if profile is None:
        raise HTTPException(status_code=409, detail="Owning supplier profile not found")
    return record, model, profile


@router.get("", response_model=PaginatedResponse[ExtractionModelResponse])
def list_models(
    db: DbSession,
    pagination: Pagination,
    status: str | None = Query(None),
    supplier: str | None = Query(None),
):
    items, total = crud.list_extraction_models(
        db,
        page=pagination.page,
        per_page=pagination.per_page,
        status=status,
        supplier=supplier,
    )
    return PaginatedResponse(
        items=[ExtractionModelResponse.model_validate(m) for m in items],
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=max(1, math.ceil(total / pagination.per_page)),
    )


@router.post("/bootstrap", response_model=ExtractionModelResponse)
def bootstrap_model(db: DbSession, body: BootstrapRequest, current_user: CurrentUser):
    """Ensure a workbench-ready model exists for a profile (cold start).

    This is the single entry point into the training workbench: if the profile
    already has a model, its latest version is returned (idempotent); otherwise
    an empty candidate is created so the operator can upload, tag, train, and
    approve entirely within the workbench — no separate authoring flow."""
    from stencil.profiles.loader import get_profile

    profile = get_profile(body.supplier_profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Supplier profile not found")

    existing = crud.find_model_by_profile(db, body.supplier_profile_id)
    if existing is not None:
        return ExtractionModelResponse.model_validate(existing)

    model = registry.bootstrap_candidate(db, profile)
    record = crud.get_extraction_model(db, model.model_id)
    if record is not None:
        record.created_by = actor_label(current_user, record.created_by)
        model_json = dict(record.model_json or {})
        model_json["created_by"] = record.created_by
        record.model_json = model_json
        append_event(db, entity_type="model", entity_id=record.id, action="created", actor=current_user)
        db.commit()
    record = crud.get_extraction_model(db, model.model_id)
    return ExtractionModelResponse.model_validate(record)


@router.get("/{model_id}", response_model=ExtractionModelResponse)
def get_model(db: DbSession, model_id: str):
    record = crud.get_extraction_model(db, model_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction model not found")
    return ExtractionModelResponse.model_validate(record)


@router.get("/{model_id}/training-set")
def get_training_set(db: DbSession, model_id: str):
    """The invoices this model is authored to reproduce, with per-invoice
    membership/metadata and the LAST training run's reproduction result.

    Cheap and poll-safe: performs no model execution (that is reserved for the
    approve gate). Per-invoice pass/fail reflects the most recent Train run."""
    from stencil.models.training import build_training_set_view

    _record, model, _profile = _load_model_and_profile(db, model_id)
    return build_training_set_view(db, model)


@router.put("/{model_id}/approve", response_model=ExtractionModelResponse)
def approve_model(db: DbSession, model_id: str, body: ApproveRequest, current_user: CurrentUser):
    from pydantic import ValidationError

    from stencil.models.training import evaluate_model_training_set

    _record, model, profile = _load_model_and_profile(db, model_id)
    try:
        gate = evaluate_model_training_set(db, model, profile)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Training set contains invalid invoice data",
                "errors": exc.errors(),
            },
        ) from exc
    if not gate["approvable"]:
        raise HTTPException(
            status_code=409,
            detail={"message": "Model is not approvable yet: " + "; ".join(gate["reasons"]),
                    "gate": gate},
        )
    # Promote: approve this model and retire any prior approved one for the
    # profile (kept as retired for rollback).
    actor = actor_label(current_user, body.approved_by or "admin")
    if not registry.promote_model(db, model_id, approved_by=actor):
        raise HTTPException(status_code=404, detail="Extraction model not found")
    record = crud.get_extraction_model(db, model_id)
    append_event(db, entity_type="model", entity_id=model_id, action="approved", actor=current_user)
    db.commit()
    _activate_owning_profile(record, actor=current_user)
    return ExtractionModelResponse.model_validate(record)


@router.post("/{model_id}/clone", response_model=ExtractionModelResponse)
def clone_model(db: DbSession, model_id: str, current_user: CurrentUser):
    """Clone a model into a new candidate for safe retraining.

    Used to retrain an approved model without taking it out of production: the
    clone is trained/approved separately, and approving it supersedes the
    original (which is retired, not deleted, so you can roll back)."""
    clone = registry.clone_model(db, model_id)
    if clone is None:
        raise HTTPException(status_code=404, detail="Extraction model not found")
    record = crud.get_extraction_model(db, clone.model_id)
    if record is not None:
        record.created_by = actor_label(current_user, record.created_by)
        model_json = dict(record.model_json or {})
        model_json["created_by"] = record.created_by
        record.model_json = model_json
        append_event(
            db,
            entity_type="model",
            entity_id=record.id,
            action="cloned",
            actor=current_user,
            metadata={"source": model_id},
        )
        db.commit()
    return ExtractionModelResponse.model_validate(record)


@router.post("/{model_id}/train", response_model=MessageResponse)
def train_model(db: DbSession, model_id: str, body: TrainRequest, current_user: CurrentUser):
    """Add invoices to the training set and re-author the model from the whole
    set (async). Used by both 'Add training invoices' and 'Retrain'."""
    from stencil.models.training import model_training_set_ids
    from stencil.tasks.worker import train_model_task

    record, model, _profile = _load_model_and_profile(db, model_id)
    if record.status == "approved":
        raise HTTPException(
            status_code=409,
            detail="Approved models are retrained by cloning — clone first, then train the clone.",
        )

    add = [i.strip() for i in body.add_intake_ids if i and i.strip()]
    # Validate the additions share this model's layout fingerprint.
    for iid in add:
        intake = crud.get_intake(db, iid)
        if intake is None:
            raise HTTPException(status_code=404, detail=f"Invoice {iid} not found")
        if intake.layout_fingerprint and model.layout_fingerprint and \
                intake.layout_fingerprint != model.layout_fingerprint:
            raise HTTPException(
                status_code=409,
                detail=f"Invoice {iid} has a different layout fingerprint — "
                       "create a separate supplier profile for that layout.",
            )

    full_set = list(dict.fromkeys(model_training_set_ids(model) + add))
    if not full_set:
        raise HTTPException(status_code=400, detail="No training invoices to train on")

    async_result = train_model_task.delay(model_id, full_set)
    append_event(
        db,
        entity_type="model",
        entity_id=model_id,
        action="training_started",
        actor=current_user,
        metadata={"invoice_count": len(full_set)},
    )
    db.commit()
    return MessageResponse(
        message=f"Retraining {model_id} on {len(full_set)} invoice(s). Task {async_result.id}.",
    )


def _writable_candidate(db, model_id: str):
    """Return a candidate model_id safe to edit/train.

    If the target is approved (live in production), reuse the profile's existing
    candidate clone if one exists, otherwise clone it once — so production keeps
    running on the live version and parallel uploads don't spawn N orphan clones.
    Returns the (possibly new) model_id.
    """
    record = crud.get_extraction_model(db, model_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction model not found")
    if record.status != "approved":
        return model_id
    # Idempotent: reuse an existing candidate for this profile rather than cloning
    # again on every upload/edit.
    if record.supplier_profile_id:
        existing = crud.find_model_by_profile(db, record.supplier_profile_id, status="candidate")
        if existing is not None:
            return existing.id
    clone = registry.clone_model(db, model_id)
    if clone is None:
        raise HTTPException(status_code=409, detail="Could not clone the approved model")
    logger.info("models.workbench_autocloned", source=model_id, clone=clone.model_id)
    return clone.model_id


async def _save_uploaded_pdf(file: UploadFile) -> tuple[Path, str]:
    """Validate a PDF upload and stage it. Returns (temp_path, display_name).

    The on-disk name carries a uuid to avoid collisions; the display name is the
    operator's original filename (persisted as IntakeRecord.original_filename).
    Streamed with a size cap rather than buffered whole in memory.
    """
    safe_name = validate_upload_filename(file, allowed_suffixes=(".pdf",))
    dest = settings.inbound_dir / f"training_{uuid4().hex}_{safe_name}"
    await stream_upload_to(file, dest)
    return dest, safe_name


@router.post("/{model_id}/training-invoices")
async def upload_model_training_invoice(
    db: DbSession,
    model_id: str,
    current_user: CurrentUser,
    file: UploadFile,
    role: str = Form("train"),
):
    """Stage a sample invoice PDF as training data for a model (workbench).

    The PDF is only STAGED — archived and added to the model's Train or Holdout
    list. It is NOT processed, extracted, or delivered now; that happens when the
    operator clicks Train. So uploads are instant, never appear in the Invoices
    list, and never spin up a processing run. If the target model is the live
    (approved) one, it is cloned to a candidate first so production keeps running;
    the returned model_id is the candidate to keep working on.
    """
    if role not in ("train", "holdout"):
        raise HTTPException(status_code=400, detail="role must be 'train' or 'holdout'")

    _record, _model, profile = _load_model_and_profile(db, model_id)
    target_id = _writable_candidate(db, model_id)

    dest, display_name = await _save_uploaded_pdf(file)

    from stencil.intake.service import process_new_pdf

    intake_id = process_new_pdf(
        db, dest, intake_source="training",
        supplier_profile_id=profile.profile_id, status="staged",
        original_filename=display_name,
    )
    registry.add_training_intake(db, target_id, intake_id, role=role, updated_by=actor_label(current_user))
    append_event(
        db,
        entity_type="model",
        entity_id=target_id,
        action="training_invoice_added",
        actor=current_user,
        metadata={"intake_id": intake_id, "role": role},
    )
    db.commit()
    logger.info("models.training_sample_staged", intake_id=intake_id,
                model_id=target_id, role=role)

    return {
        "intake_id": intake_id,
        "model_id": target_id,
        "role": role,
        "status": "staged",
        "cloned": target_id != model_id,
    }


@router.put("/{model_id}/training-set", response_model=ExtractionModelResponse)
def update_training_set(db: DbSession, model_id: str, body: TrainingSetRequest, current_user: CurrentUser):
    """Persist the intended Train/Holdout split without re-authoring.

    If the target is approved, it is cloned first (production keeps running) and
    the split is applied to the candidate, which is returned."""
    target_id = _writable_candidate(db, model_id)
    updated = registry.set_training_membership(
        db, target_id,
        train_intake_ids=body.train_intake_ids,
        holdout_intake_ids=body.holdout_intake_ids,
        updated_by=actor_label(current_user),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Extraction model not found")
    append_event(db, entity_type="model", entity_id=target_id, action="training_set_updated", actor=current_user)
    db.commit()
    return ExtractionModelResponse.model_validate(crud.get_extraction_model(db, target_id))


@router.get("/{model_id}/training-status")
def get_training_status(db: DbSession, model_id: str):
    """Latest model-training run for this model (poll target for the workbench).

    Separate from invoice processing: this reports the training pipeline's own
    progress — extracting ground truth, authoring, validating — not an invoice."""
    run = crud.get_latest_training_run(db, model_id)
    if run is None:
        return {"running": False, "run": None}
    return {
        "running": run.state == "running",
        "run": {
            "id": run.id,
            "model_id": run.model_id,
            "state": run.state,
            "step": run.step,
            "message": run.message,
            "completed_steps": run.completed_steps,
            "total_steps": run.total_steps,
            "detail": run.detail or {},
            "estimated_cost_usd": float(run.estimated_cost_usd or 0),
            "tokens_input": run.tokens_input,
            "tokens_output": run.tokens_output,
            "error_message": run.error_message,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        },
    }


@router.get("/{model_id}/versions")
def list_model_versions(db: DbSession, model_id: str):
    """All versions in this model's lineage (same supplier profile), newest first,
    with which one is live (approved) for one-click rollback / promotion."""
    record = crud.get_extraction_model(db, model_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction model not found")
    profile_id = record.supplier_profile_id
    if not profile_id:
        records = [record]
    else:
        records = crud.list_models_by_profile(db, profile_id)
    records = sorted(records, key=lambda r: r.version, reverse=True)
    live_id = next((r.id for r in records if r.status == "approved"), None)
    versions = [
        {
            "model_id": r.id,
            "version": r.version,
            "status": r.status,
            "is_live": r.id == live_id,
            "confidence": r.confidence,
            "times_used": r.times_used,
            "validation_success_count": r.validation_success_count,
            "validation_attempt_count": r.validation_attempt_count,
            "training_invoice_count": len((r.model_json or {}).get("training_intake_ids", []) or []),
            "holdout_invoice_count": len((r.model_json or {}).get("holdout_intake_ids", []) or []),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "approved_at": r.approved_at.isoformat() if r.approved_at else None,
            "approved_by": r.approved_by,
            "updated_by": r.updated_by,
            "retired_by": r.retired_by,
            "retired_at": r.retired_at.isoformat() if r.retired_at else None,
            "notes": r.notes,
        }
        for r in records
    ]
    return {"profile_id": profile_id, "live_model_id": live_id, "versions": versions}


@router.post("/{model_id}/rollback", response_model=ExtractionModelResponse)
def rollback_model(db: DbSession, model_id: str, body: RollbackRequest, current_user: CurrentUser):
    """Make a previously-approved (now retired) version live again.

    Re-promotes it directly — it was approved before, so no re-authoring is
    needed — and retires whichever version is currently live."""
    record = crud.get_extraction_model(db, model_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction model not found")
    if record.status == "approved":
        raise HTTPException(status_code=409, detail="This version is already live")
    actor = actor_label(current_user, body.approved_by or "rollback")
    if not registry.promote_model(db, model_id, approved_by=actor):
        raise HTTPException(status_code=404, detail="Extraction model not found")
    record = crud.get_extraction_model(db, model_id)
    append_event(db, entity_type="model", entity_id=model_id, action="rollback_promoted", actor=current_user)
    db.commit()
    _activate_owning_profile(record, actor=current_user)
    return ExtractionModelResponse.model_validate(record)


def _activate_owning_profile(record, *, actor=None) -> None:
    """On model approval, move the owning profile from training to active."""
    from stencil.api.profiles import set_profile_status

    profile_id = record.supplier_profile_id if record else None
    if not profile_id:
        return
    try:
        set_profile_status(profile_id, "active", actor=actor)
    except Exception:
        # Profile may not exist (e.g. deleted) — approval still stands.
        pass


@router.put("/{model_id}/retire", response_model=ExtractionModelResponse)
def retire_model(db: DbSession, model_id: str, current_user: CurrentUser):
    if not registry.retire_model(db, model_id, retired_by=actor_label(current_user)):
        raise HTTPException(status_code=404, detail="Extraction model not found")
    record = crud.get_extraction_model(db, model_id)
    append_event(db, entity_type="model", entity_id=model_id, action="retired", actor=current_user)
    db.commit()
    return ExtractionModelResponse.model_validate(record)


@router.get("/{model_id}/usage")
def get_model_usage(db: DbSession, model_id: str):
    record = crud.get_extraction_model(db, model_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction model not found")
    return {
        "model_id": record.id,
        "times_used": record.times_used,
        "last_used_at": record.last_used_at.isoformat() if record.last_used_at else None,
        "success_rate": record.success_rate,
        "confidence": record.confidence,
    }


@router.put("/{model_id}/rules", response_model=ExtractionModelResponse)
def update_model_rules(db: DbSession, model_id: str, body: RulesRequest, current_user: CurrentUser):
    """Save edited model rules (manual tinkering). Keeps the current status."""
    from stencil.models.schema import ExtractionModel

    record = crud.get_extraction_model(db, model_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction model not found")
    try:
        ExtractionModel.model_validate(body.model_json)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid model rules: {e}")

    registry.update_model_rules(db, model_id, body.model_json, updated_by=actor_label(current_user))
    append_event(db, entity_type="model", entity_id=model_id, action="rules_updated", actor=current_user)
    db.commit()
    return ExtractionModelResponse.model_validate(crud.get_extraction_model(db, model_id))


@router.post("/{model_id}/activate", response_model=ExtractionModelResponse)
def activate_model(db: DbSession, model_id: str, current_user: CurrentUser):
    """Manually approve & activate a model (e.g. a reviewed flagged model)."""
    record = crud.get_extraction_model(db, model_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction model not found")
    registry.approve_model(db, model_id, approved_by=actor_label(current_user, "manual_review"))
    record = crud.get_extraction_model(db, model_id)
    append_event(db, entity_type="model", entity_id=model_id, action="activated", actor=current_user)
    db.commit()
    _activate_owning_profile(record, actor=current_user)
    return ExtractionModelResponse.model_validate(record)


@router.post("/draft/test")
def test_draft_model(db: DbSession, body: DraftTestRequest):
    """Dry-run a DRAFT model (not yet persisted) against a stored invoice.

    The live-preview engine for the visual builder: returns the extracted invoice,
    the deliverable output rows, the full execution trace (which rows matched, how
    they were classified/grouped, per-field raw→value — used to annotate the
    overlay), and a diff against the invoice's AI ground truth when one exists.
    Self-checks are not raised so the builder always sees output while iterating.
    """
    from stencil.api.intakes import resolve_intake_pdf_path
    from stencil.models.diff import diff_invoices
    from stencil.models.interpreter import execute_model_with_trace
    from stencil.models.schema import ExtractionModel
    from stencil.output.preview import preview_for_profile
    from stencil.profiles.loader import get_profile
    from stencil.specs.loader import get_output_spec, resolve_output_spec
    from stencil.validation.schema import CanonicalInvoice

    try:
        model = ExtractionModel.model_validate(body.model_json)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid model rules: {e}") from e

    # Accepts an intake_id or a builder sample_id — the resolver 404s if neither
    # has a PDF on disk.
    pdf_path = resolve_intake_pdf_path(body.intake_id)
    try:
        # allow_empty: a model that matches nothing is exactly when the author
        # needs the trace + drop reasons, so return them instead of a bare 422.
        model_invoice, trace = execute_model_with_trace(
            model, pdf_path, body.intake_id, skip_self_checks=True, allow_empty=True
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Model execution failed: {e}") from e

    profile = get_profile(model.supplier_profile_id) if model.supplier_profile_id else None
    # No profile yet (the builder can author before one exists): fall back to the
    # deliverable the author picked, so the preview shows the columns they will
    # actually ship rather than the system default.
    spec = (
        resolve_output_spec(profile)
        if profile is not None
        else get_output_spec(body.output_spec_id) if body.output_spec_id
        else resolve_output_spec(None)
    )

    baseline = None
    baseline_path = settings.completed_dir / body.intake_id / "canonical_invoice.json"
    if baseline_path.exists():
        baseline = CanonicalInvoice.model_validate(
            json.loads(baseline_path.read_text(encoding="utf-8"))
        )

    is_match, diff_report = None, None
    if baseline is not None:
        diff = diff_invoices(baseline, model_invoice, spec)
        is_match, diff_report = diff.is_match, diff.to_report()

    return {
        "intake_id": body.intake_id,
        "invoice": model_invoice.model_dump(mode="json"),
        "output": preview_for_profile(
            model_invoice,
            profile,
            extraction_path="model",
            output_spec_id=body.output_spec_id,
            field_schema_id=body.field_schema_id,
        ),
        "trace": trace,
        "diff": diff_report,
        "is_match": is_match,
    }


@router.post("/draft/eval-folder")
def eval_draft_against_folder(body: DraftEvalFolderRequest):
    """Score a DRAFT model against a whole eval-case folder (ST_DEBUG only).

    Runs the model against every ``eval_cases/<layout_id>/invoices/*.pdf`` and
    diffs each result against its committed ``expected/<stem>.xlsx``, so the
    author can see the model generalize (or not) across real invoices as one
    ``matched / expected`` number per file — the same row diff the eval runner
    uses, minus any AI cost."""
    if not settings.debug:
        raise HTTPException(status_code=403, detail="Eval-folder scoring requires ST_DEBUG")

    from stencil.evals import scoring
    from stencil.evals.dataset import discover_cases
    from stencil.models.interpreter import execute_model_with_trace
    from stencil.models.schema import ExtractionModel
    from stencil.output.mapper import output_spec_to_columns
    from stencil.output.preview import preview_for_profile
    from stencil.specs.loader import resolve_output_spec

    try:
        model = ExtractionModel.model_validate(body.model_json)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid model rules: {e}") from e

    cases = [c for c in discover_cases() if c.layout_id == body.layout_id and c.has_expected]
    if not cases:
        raise HTTPException(
            status_code=404,
            detail=f"No eval cases with expected output for layout {body.layout_id!r}",
        )

    files = []
    for case in cases:
        profile = case.load_profile()
        columns = output_spec_to_columns(resolve_output_spec(profile))
        entry = {"file": case.pdf_path.name}
        try:
            invoice, _ = execute_model_with_trace(
                model, case.pdf_path, f"eval-{case.case_id.replace('/', '-')}",
                skip_self_checks=True, allow_empty=True,
            )
            preview = preview_for_profile(invoice, profile, extraction_path="model")
            metrics = scoring.diff_metrics(case.expected_rows(), preview.get("rows") or [], columns)
            entry.update({
                "matched_rows": metrics["matched_rows"],
                "expected_rows": metrics["expected_rows"],
                "actual_rows": metrics["actual_rows"],
                "is_match": metrics["is_match"],
                # Why it scored what it scored. Without these the UI can only show
                # "0/384" — true but useless for fixing the rule that caused it.
                "cell_diff_summary": metrics["cell_diff_summary"],
                "missing_rows": metrics["missing_rows"],
                "extra_rows": metrics["extra_rows"],
                "missing_examples": metrics["missing_examples"][:3],
                "extra_examples": metrics["extra_examples"][:3],
            })
        except Exception as e:  # a model that fails on one invoice must not abort the batch
            entry["error"] = str(e)
        files.append(entry)

    ok = sum(1 for f in files if f.get("is_match"))
    return {
        "layout_id": body.layout_id,
        "files": files,
        "files_matched": ok,
        "files_total": len(files),
    }


@router.post("/{model_id}/test")
def test_model(db: DbSession, model_id: str, body: TestRequest):
    """Dry-run a model (optionally with edited rules) against an invoice and
    compare its output to that invoice's AI baseline. No DB writes."""
    from stencil.models.diff import diff_invoices
    from stencil.models.interpreter import execute_model
    from stencil.models.schema import ExtractionModel
    from stencil.output.xlsx_writer import write_xlsx
    from stencil.profiles.loader import get_profile
    from stencil.specs.loader import resolve_output_spec
    from stencil.validation.schema import CanonicalInvoice

    record = crud.get_extraction_model(db, model_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction model not found")

    # Resolve the model rules to test (edited override or the saved model)
    rules = body.model_json if body.model_json is not None else record.model_json
    try:
        model = ExtractionModel.model_validate(rules)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid model rules: {e}")

    # Locate the source invoice PDF (archive copy is the durable one)
    intake = crud.get_intake(db, body.intake_id)
    if intake is None:
        raise HTTPException(status_code=404, detail="Intake not found")
    pdf_path = settings.archive_dir / body.intake_id / "original.pdf"
    if not pdf_path.exists():
        pdf_path = settings.processing_dir / body.intake_id / "original.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Source PDF for this invoice not found")

    try:
        model_invoice = execute_model(model, pdf_path, body.intake_id)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Model execution failed: {e}")

    # Load the AI baseline for this invoice (its delivered canonical JSON)
    baseline = None
    baseline_path = settings.completed_dir / body.intake_id / "canonical_invoice.json"
    if baseline_path.exists():
        baseline = CanonicalInvoice.model_validate(json.loads(baseline_path.read_text(encoding="utf-8")))

    spec = resolve_output_spec(
        get_profile(model.supplier_profile_id) if model.supplier_profile_id else None
    )
    is_valid, reason, diff_report = None, "no AI baseline available for comparison", None
    if baseline is not None:
        diff = diff_invoices(baseline, model_invoice, spec)
        is_valid, reason, diff_report = diff.is_match, diff.summary(), diff.to_report()
    review_root = settings.completed_dir / body.intake_id / "model_review"
    review_root.mkdir(parents=True, exist_ok=True)
    try:
        write_xlsx(model_invoice, review_root / "test_latest.xlsx", spec)
    except Exception:
        pass

    return {
        "is_valid": is_valid,
        "reason": reason,
        "diff": diff_report,
        "model_output": model_invoice.model_dump(mode="json"),
        "ai_baseline": baseline.model_dump(mode="json") if baseline else None,
    }


@router.post("/{model_id}/preview", response_model=ExtractionPreviewResponse)
async def preview_model_extraction(db: DbSession, model_id: str, file: UploadFile):
    """Run this model against an UPLOADED sample PDF and return a tabular preview
    of the deliverable — how the trained model performs on a new invoice. No DB
    writes, no delivery. Deterministic replay (no OpenAI call)."""
    from stencil.api.uploads import save_uploaded_pdf
    from stencil.models.interpreter import execute_model
    from stencil.models.schema import ExtractionModel
    from stencil.output.preview import preview_for_profile
    from stencil.profiles.loader import get_profile

    record = crud.get_extraction_model(db, model_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction model not found")
    try:
        model = ExtractionModel.model_validate(record.model_json)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid model rules: {e}") from e

    from fastapi.concurrency import run_in_threadpool

    profile = get_profile(model.supplier_profile_id) if model.supplier_profile_id else None
    pdf_path = await save_uploaded_pdf(file, prefix="modelpreview")

    def _run() -> dict:
        # Layout parsing + replay is CPU-bound; keep it off the event loop.
        model_invoice = execute_model(model, pdf_path, "preview")
        return preview_for_profile(model_invoice, profile, extraction_path="model")

    try:
        return await run_in_threadpool(_run)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Model execution failed: {e}") from e
    finally:
        pdf_path.unlink(missing_ok=True)


@router.get("/{model_id}/review/{intake_id}/{filename:path}")
def get_review_artifact(model_id: str, intake_id: str, filename: str):
    """Serve a model_review artifact (Excel/JSON) for download/preview."""
    from fastapi.responses import FileResponse

    safe = Path(filename)
    if safe.is_absolute() or ".." in safe.parts:
        raise HTTPException(status_code=400, detail="Invalid path")
    if safe.suffix not in (".xlsx", ".json", ".txt"):
        raise HTTPException(status_code=400, detail="Unsupported file type")

    base = (settings.completed_dir / intake_id / "model_review").resolve()
    file_path = (base / safe).resolve()
    if base not in file_path.parents and file_path != base:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(path=str(file_path), filename=file_path.name)


@router.delete("/{model_id}", response_model=MessageResponse)
def delete_model(db: DbSession, model_id: str, current_user: CurrentUser):
    record = crud.get_extraction_model(db, model_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction model not found")
    append_event(
        db,
        entity_type="model",
        entity_id=model_id,
        action="deleted",
        actor=current_user,
        metadata={"supplier_profile_id": record.supplier_profile_id, "status": record.status},
    )
    if not registry.delete_model(db, model_id):
        raise HTTPException(status_code=404, detail="Extraction model not found")
    db.commit()
    return MessageResponse(message=f"Model {model_id} deleted")
