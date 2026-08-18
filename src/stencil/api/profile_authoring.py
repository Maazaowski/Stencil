"""Interactive AI profile-authoring API.

A conversational flow that drafts a ``SupplierProfile`` from sample invoices:
create a session, upload 5-6 invoices (each AI-extracted once in the worker), then
chat — every turn re-authors the draft and previews the deliverable per invoice.
Finalizing saves a real draft-status profile that opens in the normal editor.

Long-running work (extraction, the authoring LLM call) runs in Celery and is
surfaced via the same job-polling pattern as ``/profiles/{id}/preview``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel, Field

from stencil.api.deps import CurrentUser, DbSession
from stencil.api.uploads import save_uploaded_file, save_uploaded_pdf
from stencil.config import settings
from stencil.db import crud
from stencil.fields.loader import field_schema_exists, get_field_schema, resolve_merged_field_schema
from stencil.profiles.authoring import (
    draft_to_supplier_profile_dict,
    merge_source_profile_config,
    supplier_profile_to_draft,
)
from stencil.profiles.loader import get_profile, save_profile
from stencil.profiles.schema import SupplierProfile
from stencil.specs.loader import (
    get_output_spec,
    output_spec_exists,
    resolve_output_spec,
    validate_profile_output_mapping,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/profiles/authoring", tags=["profile-authoring"])

PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class CreateSessionRequest(BaseModel):
    supplier_name: str | None = None
    output_spec_id: str = "temforce.standard"
    field_schema_id: str = "invoice.standard"
    # Seed the session from an existing profile to refine its extraction hints with
    # AI; finalizing saves a new version that re-merges the source's non-AI config.
    source_profile_id: str | None = None
    category_override: Literal["standard", "wireless", "time_and_material"] | None = None


class MessageRequest(BaseModel):
    message: str = Field(..., min_length=1)


class FinalizeRequest(BaseModel):
    profile_id: str
    layout_description: str | None = None
    output_spec_id: str | None = None
    field_schema_id: str | None = None


def _invoice_summary(inv) -> dict:
    return {
        "id": inv.id,
        "filename": inv.filename,
        "extraction_status": inv.extraction_status,
        "has_expected": inv.has_expected,
        "error_message": inv.error_message,
        "scan_metadata": inv.scan_metadata,
    }


def _blueprint_summary(blueprint) -> dict:
    return {
        "id": blueprint.id,
        "filename": blueprint.filename,
        "invoice_id": blueprint.invoice_id,
        "matching_metadata": blueprint.matching_metadata,
    }


def _job_summary(job) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "message": job.message,
        "invoice_id": job.invoice_id,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def _session_state(db: DbSession, session) -> dict:
    invoices = crud.list_authoring_invoices(db, session.id)
    blueprints = crud.list_authoring_blueprints(db, session.id)
    jobs = crud.list_recent_authoring_jobs(db, session.id)
    return {
        "session_id": session.id,
        "status": session.status,
        "supplier_name": session.supplier_name,
        "output_spec_id": session.output_spec_id,
        "field_schema_id": session.field_schema_id,
        "phase": session.phase,
        "inferred_category": session.inferred_category,
        "category_override": session.category_override,
        "discovery": session.discovery_report,
        "validation": session.validation_summary,
        "engine_version": session.engine_version,
        "conversation": session.conversation or [],
        "draft_profile": session.draft_profile,
        "previews": session.previews or {},
        "source_profile_id": session.source_profile_id,
        "finalized_profile_id": session.finalized_profile_id,
        "estimated_cost_usd": float(session.estimated_cost_usd or 0),
        "invoices": [_invoice_summary(i) for i in invoices],
        "blueprints": [_blueprint_summary(item) for item in blueprints],
        "jobs": [_job_summary(j) for j in jobs],
    }


def _require_session(db: DbSession, session_id: str):
    session = crud.get_authoring_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Authoring session not found")
    return session


def _require_active_session(session) -> None:
    if session.status != "active":
        raise HTTPException(status_code=400, detail="Session is no longer active")


def _require_not_finalized(session) -> None:
    if session.status == "finalized":
        raise HTTPException(status_code=400, detail="Session is no longer active")


def _require_no_active_jobs(db: DbSession, session_id: str) -> None:
    if crud.has_active_authoring_jobs(db, session_id):
        raise HTTPException(status_code=409, detail="An authoring job is already queued or running")


def _merge_source_profile_config(source, authored: dict) -> dict:
    """Layer AI-authored hints over a source profile without dropping config.

    Source-only advanced settings and structural/custom field overrides are not
    part of the AI draft surface, so a shallow merge would silently erase them.
    """
    return merge_source_profile_config(source, authored)


def _enqueue_runner(
    session_id: str,
    job_id: str,
    *,
    kind: str,
    message: str | None = None,
    invoice_id: str | None = None,
) -> None:
    from stencil.tasks.worker import authoring_reextract_task, authoring_turn_task

    if kind == "reextract":
        authoring_reextract_task.apply_async(
            args=[session_id, job_id, invoice_id], task_id=job_id)
    else:
        authoring_turn_task.apply_async(
            args=[session_id, job_id, message or ""], task_id=job_id)


@router.post("/sessions")
def create_session(db: DbSession, body: CreateSessionRequest):
    supplier_name = body.supplier_name
    output_spec_id = body.output_spec_id
    field_schema_id = body.field_schema_id
    seed_draft: dict | None = None

    # Editing an existing profile with AI: seed the draft from it and inherit its
    # output spec / field schema / supplier so refinements build on the current hints.
    if body.source_profile_id:
        source = get_profile(body.source_profile_id)
        if source is None:
            raise HTTPException(status_code=404, detail=f"Profile '{body.source_profile_id}' not found")
        supplier_name = source.identity.canonical_name
        output_spec_id = source.output_spec_id
        field_schema_id = source.field_schema_id
        seed_draft = supplier_profile_to_draft(source)

    if not output_spec_exists(output_spec_id):
        raise HTTPException(status_code=400, detail=f"Output spec '{output_spec_id}' does not exist")
    if not field_schema_exists(field_schema_id):
        raise HTTPException(status_code=400, detail=f"Field schema '{field_schema_id}' does not exist")
    session = crud.create_authoring_session(
        db, supplier_name=supplier_name,
        output_spec_id=output_spec_id, field_schema_id=field_schema_id,
        source_profile_id=body.source_profile_id, draft_profile=seed_draft,
        category_override=body.category_override,
    )
    logger.info("authoring.session.created", session_id=session.id, source_profile_id=body.source_profile_id)
    return _session_state(db, session)


@router.get("/sessions/{session_id}")
def get_session(db: DbSession, session_id: str):
    return _session_state(db, _require_session(db, session_id))


@router.post("/sessions/{session_id}/cancel")
def cancel_session_authoring(db: DbSession, session_id: str):
    """Stop all queued/running authoring work, including in-flight AI calls.

    Every authoring job is also its Celery task id. A queue runner may still be
    executing later jobs under an earlier job's task id, so revoke every task id
    ever dispatched for this session. ``terminate=True`` kills the worker child
    that owns the provider HTTP connection instead of only suppressing queued work.
    """
    from stencil.tasks.worker import app

    session = _require_session(db, session_id)
    _require_not_finalized(session)
    jobs = crud.list_authoring_jobs(db, session.id)
    active = [job for job in jobs if job.status in {"queued", "running"}]
    if not active and session.status != "running":
        raise HTTPException(status_code=400, detail="No authoring work is running")

    task_ids = [job.id for job in jobs]
    try:
        if task_ids:
            app.control.revoke(task_ids, terminate=True)
    except Exception as exc:
        logger.error("authoring.cancel.revoke_failed", session_id=session.id, error=str(exc))
        raise HTTPException(
            status_code=503,
            detail="Could not reach the authoring worker; cancellation was not confirmed",
        ) from exc

    cancelled = crud.cancel_active_authoring_jobs(db, session.id)
    logger.info(
        "authoring.session.cancelled",
        session_id=session.id,
        cancelled_jobs=cancelled,
        revoked_tasks=len(task_ids),
    )
    return {"status": "cancelled", "cancelled_jobs": cancelled}


@router.post("/sessions/{session_id}/invoices")
async def upload_invoice(
    db: DbSession,
    session_id: str,
    file: UploadFile,
    expected: UploadFile | None = None,
):
    """Upload one sample PDF (and optionally its XLS/XLSX blueprint).

    The sample is only staged here — AI extraction is deferred to the first
    authoring turn, so uploading is instant and costs nothing until you engage."""
    from stencil.profiles.authoring_runtime import stage_sample

    session = _require_session(db, session_id)
    _require_active_session(session)
    _require_no_active_jobs(db, session.id)
    pdf_path = await save_uploaded_pdf(file, prefix="authoring")
    expected_path = None
    if expected is not None and expected.filename:
        expected_path = await save_uploaded_file(
            expected, prefix="authoring_blueprint", allowed_suffixes=(".xlsx", ".xls"))

    invoice = crud.add_authoring_invoice(
        db, session_id=session.id, filename=file.filename,
        has_expected=expected_path is not None, artifact_dir="",
    )
    artifact_dir = stage_sample(
        session.id, invoice.id, pdf_path=str(pdf_path),
        expected_xlsx_path=str(expected_path) if expected_path else None)
    crud.update_authoring_invoice(db, invoice.id, artifact_dir=str(artifact_dir))
    crud.update_authoring_session(db, session.id, phase="discovery_required")
    logger.info("authoring.invoice.staged", session_id=session.id, invoice_id=invoice.id)
    return {"invoice_id": invoice.id, "status": "uploaded"}


@router.delete("/sessions/{session_id}/invoices/{invoice_id}")
def delete_invoice(db: DbSession, session_id: str, invoice_id: str):
    """Remove an uploaded sample and all of its cached artifacts from an active session."""
    from stencil.profiles.authoring_runtime import delete_sample_artifacts

    session = _require_session(db, session_id)
    _require_active_session(session)
    _require_no_active_jobs(db, session.id)
    invoice = crud.get_authoring_invoice(db, invoice_id)
    if invoice is None or invoice.session_id != session.id:
        raise HTTPException(status_code=404, detail="Authoring invoice not found")

    delete_sample_artifacts(
        session_id=session.id,
        invoice_id=invoice.id,
        artifact_dir=invoice.artifact_dir,
    )
    previews = dict(session.previews or {})
    previews.pop(invoice.id, None)
    crud.update_authoring_session(db, session.id, previews=previews)
    crud.delete_authoring_invoice(db, invoice.id)
    crud.update_authoring_session(
        db,
        session.id,
        phase="discovery_required",
        discovery_report=None,
        validation_summary=None,
    )
    logger.info("authoring.invoice.deleted", session_id=session.id, invoice_id=invoice.id)
    return {"invoice_id": invoice.id, "status": "deleted"}


@router.post("/sessions/{session_id}/blueprints")
async def upload_standalone_blueprint(
    db: DbSession,
    session_id: str,
    file: UploadFile,
    invoice_id: str | None = None,
):
    from stencil.profiles.authoring_runtime import stage_standalone_blueprint

    session = _require_session(db, session_id)
    _require_active_session(session)
    _require_no_active_jobs(db, session.id)
    if invoice_id is not None:
        invoice = crud.get_authoring_invoice(db, invoice_id)
        if invoice is None or invoice.session_id != session.id:
            raise HTTPException(status_code=404, detail="Authoring invoice not found")
    uploaded = await save_uploaded_file(
        file,
        prefix="authoring_blueprint",
        allowed_suffixes=(".xlsx", ".xls"),
    )
    blueprint = crud.add_authoring_blueprint(
        db,
        session_id=session.id,
        invoice_id=invoice_id,
        filename=file.filename or Path(uploaded).name,
        artifact_path="",
    )
    target = stage_standalone_blueprint(
        session.id,
        blueprint.id,
        source_path=str(uploaded),
        filename=blueprint.filename,
    )
    crud.update_authoring_blueprint(db, blueprint.id, artifact_path=str(target))
    crud.update_authoring_session(db, session.id, phase="discovery_required")
    return {"blueprint_id": blueprint.id, "status": "uploaded"}


@router.delete("/sessions/{session_id}/blueprints/{blueprint_id}")
def delete_standalone_blueprint(db: DbSession, session_id: str, blueprint_id: str):
    from stencil.profiles.authoring_runtime import delete_blueprint_artifact

    session = _require_session(db, session_id)
    _require_active_session(session)
    _require_no_active_jobs(db, session.id)
    blueprint = crud.get_authoring_blueprint(db, blueprint_id)
    if blueprint is None or blueprint.session_id != session.id:
        raise HTTPException(status_code=404, detail="Authoring blueprint not found")
    delete_blueprint_artifact(session_id=session.id, artifact_path=blueprint.artifact_path)
    crud.delete_authoring_blueprint(db, blueprint.id)
    crud.update_authoring_session(
        db,
        session.id,
        phase="discovery_required",
        discovery_report=None,
        validation_summary=None,
    )
    return {"blueprint_id": blueprint.id, "status": "deleted"}


@router.post("/sessions/{session_id}/discover")
def discover_session(db: DbSession, session_id: str):
    session = _require_session(db, session_id)
    _require_active_session(session)
    _require_no_active_jobs(db, session.id)
    if not crud.list_authoring_invoices(db, session.id):
        raise HTTPException(status_code=400, detail="Upload at least one sample invoice first")
    job = crud.create_authoring_job(db, session_id=session.id, kind="discover")
    _enqueue_runner(session.id, job.id, kind="discover")
    return {"job_id": job.id, "status": job.status}


@router.post("/sessions/{session_id}/messages")
def send_message(db: DbSession, session_id: str, body: MessageRequest):
    """Append a user message and kick off an authoring turn. Returns a job id to
    poll at GET /sessions/{id}/messages/{job_id}."""
    session = _require_session(db, session_id)
    _require_not_finalized(session)
    # Samples are extracted on this turn, so staged samples are enough to start.
    ready = [i for i in crud.list_authoring_invoices(db, session.id)
             if i.extraction_status in {"uploaded", "pending", "done"}]
    if not ready:
        raise HTTPException(status_code=400, detail="Upload at least one sample invoice first")

    job = crud.create_authoring_job(db, session_id=session.id, kind="turn", message=body.message)
    _enqueue_runner(session.id, job.id, kind="turn", message=body.message)
    logger.info("authoring.turn.queued", session_id=session.id, job_id=job.id)
    return {"job_id": job.id, "status": job.status}


class ReextractRequest(BaseModel):
    invoice_id: str | None = None


@router.post("/sessions/{session_id}/reextract")
def reextract_samples(db: DbSession, session_id: str, body: ReextractRequest):
    """Re-run AI extraction on the cached samples using the current draft profile,
    so refinements that need the model to re-read the PDF take effect. Returns a
    job id to poll at GET /sessions/{id}/messages/{job_id} (same poll endpoint)."""
    session = _require_session(db, session_id)
    _require_active_session(session)
    _require_no_active_jobs(db, session.id)
    if not session.draft_profile:
        raise HTTPException(status_code=400, detail="Nothing authored yet — chat with the assistant first")
    ready = [
        i for i in crud.list_authoring_invoices(db, session.id)
        if i.extraction_status in {"uploaded", "done"}
    ]
    if not ready:
        raise HTTPException(status_code=400, detail="No uploaded or extracted invoices to re-extract")

    if body.invoice_id is not None and all(i.id != body.invoice_id for i in ready):
        raise HTTPException(status_code=404, detail="Authoring invoice not found or not ready")

    job = crud.create_authoring_job(
        db, session_id=session.id, kind="reextract", invoice_id=body.invoice_id)
    _enqueue_runner(session.id, job.id, kind="reextract", invoice_id=body.invoice_id)
    logger.info("authoring.reextract.queued", session_id=session.id, job_id=job.id, invoice_id=body.invoice_id)
    return {"job_id": job.id, "status": job.status}


@router.get("/sessions/{session_id}/messages/{job_id}")
def get_message_result(db: DbSession, session_id: str, job_id: str):
    """Poll an authoring turn. ``{status: pending}`` until the worker finishes,
    then the turn payload (assistant message, draft, previews) or an error."""
    if not re.fullmatch(r"[0-9a-f]{8,64}", job_id):
        raise HTTPException(status_code=400, detail="Invalid job id")
    job = crud.get_authoring_job(db, job_id)
    if job is not None:
        if job.session_id != session_id:
            raise HTTPException(status_code=404, detail="Authoring job not found")
        if job.status == "done":
            return {"status": "done", **(job.result_payload or {})}
        if job.status == "error":
            return {"status": "error", "detail": job.error_message or "Authoring job failed"}
        return {"status": job.status}

    # Compatibility for one-shot result files from jobs created before persistent
    # authoring jobs existed.
    result_path = (settings.work_dir / "authoring_turns" / f"{job_id}.json").resolve()
    base = (settings.work_dir / "authoring_turns").resolve()
    if not str(result_path).startswith(str(base)) or not result_path.exists():
        return {"status": "pending"}
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "pending"}
    result_path.unlink(missing_ok=True)  # one-shot; state also persists on the session
    return payload


@router.post("/sessions/{session_id}/finalize")
def finalize_session(db: DbSession, session_id: str, body: FinalizeRequest, current_user: CurrentUser):
    """Turn the current draft into a real draft-status SupplierProfile.

    Delivery folders and lifecycle (training/activation) are configured afterward
    in the normal profile editor."""
    session = _require_session(db, session_id)
    _require_active_session(session)
    _require_no_active_jobs(db, session.id)
    if not session.draft_profile:
        raise HTTPException(status_code=400, detail="Nothing authored yet — chat with the assistant first")
    if not PROFILE_ID_PATTERN.match(body.profile_id):
        raise HTTPException(status_code=400, detail="Invalid profile ID")
    if get_profile(body.profile_id) is not None:
        raise HTTPException(status_code=409, detail=f"Profile '{body.profile_id}' already exists")

    spec_id = body.output_spec_id or session.output_spec_id
    schema_id = body.field_schema_id or session.field_schema_id
    authored = draft_to_supplier_profile_dict(
        session.draft_profile, output_spec_id=spec_id, field_schema_id=schema_id,
        field_schema=get_field_schema(schema_id), output_spec=get_output_spec(spec_id),
    )

    # Editing an existing profile: layer the AI-authored hints over the source so
    # the new version keeps its delivery accounts, training config, and fingerprint
    # rules. Otherwise it's a fresh profile from just the authored draft.
    today = datetime.now().date().isoformat()
    if session.source_profile_id:
        source = get_profile(session.source_profile_id)
        if source is None:
            raise HTTPException(status_code=404, detail=f"Source profile '{session.source_profile_id}' not found")
        profile_dict = _merge_source_profile_config(source, authored)
        profile_dict["version"] = (source.version or 1) + 1
        profile_dict["layout_description"] = body.layout_description or source.layout_description
        profile_dict["created_date"] = source.created_date or today
    else:
        profile_dict = authored
        profile_dict["version"] = 1
        profile_dict["layout_description"] = body.layout_description
        profile_dict["created_date"] = today
    profile_dict["profile_id"] = body.profile_id
    profile_dict["status"] = "draft"
    profile_dict["last_updated_date"] = today
    try:
        profile = SupplierProfile.model_validate(profile_dict)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Authored profile is invalid: {exc}") from exc
    mapping_issues = validate_profile_output_mapping(
        profile, resolve_merged_field_schema(profile))
    if mapping_issues:
        raise HTTPException(
            status_code=422,
            detail={"issues": mapping_issues},
        )
    merged_schema = resolve_merged_field_schema(profile)
    by_path = {
        f"{'field' if str(field.scope) == 'document' else 'row'}.{field.name}": field
        for field in merged_schema.fields
    }
    date_blockers = []
    for column in resolve_output_spec(profile).columns:
        for source in (column.source, column.fallback):
            field = by_path.get(source or "")
            if field is not None and str(field.type) == "date" and not field.date_format:
                date_blockers.append(
                    f"Mapped date field '{source}' requires an explicit printed date format."
                )
    if profile.authoring_evidence and date_blockers:
        profile.authoring_evidence.hard_blockers = list(dict.fromkeys([
            *profile.authoring_evidence.hard_blockers,
            *date_blockers,
        ]))
        profile.authoring_evidence.unresolved_risks = list(dict.fromkeys([
            *profile.authoring_evidence.unresolved_risks,
            *date_blockers,
        ]))
        profile.authoring_evidence.status = "failed"

    save_profile(profile, actor=current_user)
    crud.update_authoring_session(
        db, session.id, status="finalized", finalized_profile_id=profile.profile_id)
    logger.info("authoring.session.finalized", session_id=session.id, profile_id=profile.profile_id)
    return {"profile_id": profile.profile_id, "status": "draft"}
