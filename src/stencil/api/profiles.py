"""Supplier profile API endpoints: CRUD, lifecycle, training workflow, simple hints."""

import json
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel, Field

from stencil.api.deps import CurrentUser, DbSession
from stencil.api.uploads import save_uploaded_pdf, stream_upload_to
from stencil.config import settings
from stencil.db import crud
from stencil.profiles.loader import (
    delete_profile as _delete_profile_db,
)
from stencil.profiles.loader import (
    get_active_profiles,
    get_profile,
    load_all_profiles,
    save_profile,
)
from stencil.profiles.schema import PROFILE_STATUSES, SupplierProfile

logger = structlog.get_logger()

router = APIRouter(prefix="/profiles", tags=["profiles"])

PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ProfileSummary(BaseModel):
    profile_id: str
    supplier_name: str
    output_type: str
    output_spec_id: str
    version: int
    status: str
    layout_description: str | None = None
    # Representative folder pair (the first account); account_count > 1 means the
    # profile serves multiple billing accounts.
    inbound_path: str | None = None
    output_path: str | None = None
    account_count: int = 0
    audit: dict | None = None


class SimpleExtractionHints(BaseModel):
    """Non-technical, UI-facing setup. Identity + which OutputSpec to deliver is
    all most sources need; the rest is optional 'advanced' help for tricky
    layouts that the AI extractor can't read unaided."""

    output_spec_id: str = Field(default="temforce.standard", description="Which output format to deliver")
    line_items_start_text: str | None = Field(
        default=None, description="Where do line items start? (text printed on the document)")
    table_header_texts: list[str] = Field(
        default_factory=list, description="Table/section header texts for the line items")
    charge_listing_style: str | None = Field(
        default=None, description="How are charges grouped? per_charge_row / per_service_group / per_total_group")
    include_zero_amount_lines: bool = False
    notes: str | None = Field(default=None, description="Anything else the extractor should know?")


def _summary(profile: SupplierProfile) -> ProfileSummary:
    accounts = profile.effective_accounts
    first = accounts[0] if accounts else None
    return ProfileSummary(
        profile_id=profile.profile_id,
        supplier_name=profile.identity.canonical_name,
        output_type=profile.classification.output_type,
        output_spec_id=profile.output_spec_id,
        version=profile.version,
        status=profile.status,
        layout_description=profile.layout_description,
        inbound_path=first.inbound_path if first else profile.inbound_path,
        output_path=first.output_path if first else profile.output_path,
        account_count=len(accounts),
        audit=profile.audit,
    )


def apply_simple_hints(profile: SupplierProfile, hints: SimpleExtractionHints) -> SupplierProfile:
    """Map the simplified UI hints onto the internal profile schema."""
    profile.output_spec_id = hints.output_spec_id
    profile.advanced.document_structure.detail_start_marker = hints.line_items_start_text
    profile.advanced.line_item_hints.detail_table_anchors = hints.table_header_texts
    profile.advanced.line_item_hints.line_item_granularity = hints.charge_listing_style
    profile.advanced.include_zero_amount_line_items = hints.include_zero_amount_lines
    profile.notes = hints.notes
    return profile


def simple_hints_from_profile(profile: SupplierProfile) -> SimpleExtractionHints:
    return SimpleExtractionHints(
        output_spec_id=profile.output_spec_id,
        line_items_start_text=profile.advanced.document_structure.detail_start_marker,
        table_header_texts=profile.advanced.line_item_hints.detail_table_anchors,
        charge_listing_style=profile.advanced.line_item_hints.line_item_granularity,
        include_zero_amount_lines=profile.advanced.include_zero_amount_line_items,
        notes=profile.notes,
    )


def _acknowledged_review_warnings(profile: SupplierProfile) -> set[str]:
    audit = profile.audit or {}
    return {
        str(item.get("warning"))
        for item in audit.get("review_warning_acknowledgements", [])
        if isinstance(item, dict) and item.get("warning")
    }


def _profile_readiness_issues(profile: SupplierProfile) -> list[str]:
    from stencil.fields.loader import field_schema_exists, resolve_merged_field_schema
    from stencil.specs.loader import output_spec_exists, validate_profile_output_mapping

    issues: list[str] = []
    if not profile.identity.canonical_name:
        issues.append("Missing canonical supplier name")
    if not profile.classification.output_type:
        issues.append("No output type specified")
    if profile.status not in PROFILE_STATUSES:
        issues.append(f"Invalid status '{profile.status}' (allowed: {', '.join(PROFILE_STATUSES)})")
    if not field_schema_exists(profile.field_schema_id):
        issues.append(f"Unknown field_schema_id '{profile.field_schema_id}'")
    if not output_spec_exists(profile.output_spec_id):
        issues.append(f"Unknown output_spec_id '{profile.output_spec_id}'")
    if field_schema_exists(profile.field_schema_id) and output_spec_exists(profile.output_spec_id):
        schema = resolve_merged_field_schema(profile)
        issues.extend(validate_profile_output_mapping(profile, schema))
    if profile.status in ("training", "active"):
        # Authoring evidence helps users review AI decisions, but it is not an
        # execution gate. Users may save an active profile and validate changes
        # with real production documents. Structural schema/mapping errors below
        # remain blocking because the runtime cannot execute them safely.
        accounts = profile.effective_accounts
        if not accounts:
            issues.append(
                f"Profile in '{profile.status}' status requires at least one delivery account "
                "(inbound_path + output_path)"
            )
        for account in accounts:
            if not account.inbound_path:
                issues.append(
                    f"Profile in '{profile.status}' status requires inbound_path for account "
                    f"'{account.label}'"
                )
            if not account.output_path:
                issues.append(
                    f"Profile in '{profile.status}' status requires output_path for account "
                    f"'{account.label}'"
                )
        issues.extend(_account_ownership_conflicts(profile))
    return issues


def _account_ownership_conflicts(profile: SupplierProfile) -> list[str]:
    """One account folder may belong to only one active/training profile.

    Two profiles watching the same inbound folder collide in the watcher's
    registry and one is silently dropped, so we reject it at save/activate time.
    """
    from stencil.profiles.loader import account_owner_index, normalize_account_path

    others = account_owner_index(
        statuses={"training", "active"}, exclude_profile_id=profile.profile_id
    )
    issues: list[str] = []
    for account in profile.effective_accounts:
        key = normalize_account_path(account.inbound_path)
        if key and key in others:
            owner = others[key][0]
            issues.append(
                f"Inbound folder '{account.inbound_path}' is already mapped to profile "
                f"'{owner}'. An account can belong to only one profile — reassign it there or "
                "in the Accounts view."
            )
    return issues


def _persist_profile(profile: SupplierProfile, *, actor=None) -> None:
    """Validate the id and upsert the profile into the DB (source of truth)."""
    if not PROFILE_ID_PATTERN.fullmatch(profile.profile_id):
        raise HTTPException(status_code=400, detail="Invalid profile ID")
    save_profile(profile, actor=actor)


def set_profile_status(
    profile_id: str,
    status: str,
    *,
    actor=None,
    acknowledge_warnings: bool = False,
) -> SupplierProfile:
    """Transition a profile's lifecycle status and persist it."""
    if status not in PROFILE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    profile = get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    profile.status = status
    profile.last_updated_date = datetime.now().date().isoformat()
    if acknowledge_warnings and profile.authoring_evidence:
        audit = dict(profile.audit or {})
        acknowledgements = list(audit.get("review_warning_acknowledgements") or [])
        actor_name = (
            getattr(actor, "email", None)
            or getattr(actor, "display_name", None)
            or str(actor or "unknown")
        )
        now = datetime.now().isoformat()
        already = {item.get("warning") for item in acknowledgements if isinstance(item, dict)}
        acknowledgements.extend(
            {"warning": warning, "actor": actor_name, "at": now}
            for warning in profile.authoring_evidence.review_warnings
            if warning not in already
        )
        audit["review_warning_acknowledgements"] = acknowledgements
        profile.audit = audit
    issues = _profile_readiness_issues(profile)
    if issues:
        raise HTTPException(status_code=422, detail={"issues": issues})
    _persist_profile(profile, actor=actor)
    logger.info("profiles.status_changed", profile_id=profile_id, status=status)
    return profile


@router.get("", response_model=list[SupplierProfile])
def list_profiles():
    profiles = load_all_profiles()
    return list(profiles.values())


@router.get("/active", response_model=list[ProfileSummary])
def list_active_profiles():
    profiles = get_active_profiles()
    return [_summary(p) for p in profiles]


class ProfileLintResponse(BaseModel):
    """Deterministic setup issues for a profile (notes vs executable fields)."""

    conflicts: list[dict] = Field(default_factory=list)
    ignored_notes: list[str] = Field(default_factory=list)


@router.post("/lint", response_model=ProfileLintResponse)
def lint_profile(profile: SupplierProfile):
    """Report notes-vs-field contradictions for a (possibly unsaved) profile.

    Deterministic — no AI — so the editor can call it on every draft. Reuses the
    same instruction compiler the extraction path uses, so what it flags is
    exactly what would confuse extraction.
    """
    from stencil.extraction.instructions import compile_instructions
    from stencil.fields.loader import resolve_merged_field_schema

    schema = resolve_merged_field_schema(profile)
    compiled = compile_instructions(
        profile.model_dump(),
        schema,
        profile.classification.output_type,
        profile.identity.canonical_name,
    )
    payload = compiled.as_dict()
    return ProfileLintResponse(
        conflicts=payload["conflicts"],
        ignored_notes=payload["ignored_notes"],
    )


@router.get("/{profile_id}", response_model=SupplierProfile)
def get_profile_detail(profile_id: str):
    profile = get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.post("", response_model=ProfileSummary, status_code=201)
def create_profile(profile: SupplierProfile, current_user: CurrentUser):
    if not PROFILE_ID_PATTERN.fullmatch(profile.profile_id):
        raise HTTPException(status_code=400, detail="Invalid profile ID")
    if get_profile(profile.profile_id) is not None:
        raise HTTPException(status_code=409, detail="Profile already exists")
    issues = _profile_readiness_issues(profile)
    if issues:
        raise HTTPException(status_code=422, detail={"issues": issues})

    _persist_profile(profile, actor=current_user)
    return _summary(get_profile(profile.profile_id) or profile)


@router.put("/{profile_id}", response_model=ProfileSummary)
def update_profile(profile_id: str, profile: SupplierProfile, current_user: CurrentUser):
    if profile.profile_id != profile_id:
        raise HTTPException(status_code=400, detail="Profile ID cannot be changed")
    if get_profile(profile_id) is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    issues = _profile_readiness_issues(profile)
    if issues:
        raise HTTPException(status_code=422, detail={"issues": issues})

    _persist_profile(profile, actor=current_user)
    return _summary(get_profile(profile_id) or profile)


@router.delete("/{profile_id}")
def delete_profile(profile_id: str, current_user: CurrentUser):
    if not _delete_profile_db(profile_id, actor=current_user):
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"message": f"Profile {profile_id} deleted"}


@router.post("/{profile_id}/validate")
def validate_profile(profile_id: str):
    profile = get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    issues = _profile_readiness_issues(profile)

    return {
        "profile_id": profile_id,
        "valid": len(issues) == 0,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Simple hints (non-technical wizard layer)
# ---------------------------------------------------------------------------


@router.get("/{profile_id}/simple-hints", response_model=SimpleExtractionHints)
def get_simple_hints(profile_id: str):
    profile = get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return simple_hints_from_profile(profile)


@router.put("/{profile_id}/simple-hints", response_model=SupplierProfile)
def update_simple_hints(profile_id: str, hints: SimpleExtractionHints, current_user: CurrentUser):
    profile = get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    apply_simple_hints(profile, hints)
    profile.last_updated_date = datetime.now().date().isoformat()
    _persist_profile(profile, actor=current_user)
    return get_profile(profile_id)


# ---------------------------------------------------------------------------
# Lifecycle + training workflow
# ---------------------------------------------------------------------------


@router.post("/{profile_id}/start-training", response_model=ProfileSummary)
def start_training(
    profile_id: str,
    current_user: CurrentUser,
    acknowledge_warnings: bool = False,
):
    """draft (or retired) -> training. Enables training uploads for this profile."""
    profile = get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile.status not in ("draft", "retired", "training", "active"):
        raise HTTPException(status_code=400, detail=f"Cannot start training from status '{profile.status}'")
    return _summary(set_profile_status(
        profile_id,
        "training",
        actor=current_user,
        acknowledge_warnings=acknowledge_warnings,
    ))


@router.post("/{profile_id}/activate", response_model=ProfileSummary)
def activate_profile(
    db: DbSession,
    profile_id: str,
    current_user: CurrentUser,
    acknowledge_warnings: bool = False,
):
    """training -> active. Requires an approved model linked to this profile."""
    profile = get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    record = crud.find_model_by_profile(db, profile_id, status="approved")
    if record is None:
        raise HTTPException(
            status_code=422,
            detail="Profile has no approved extraction model. Approve the model first.",
        )
    return _summary(set_profile_status(
        profile_id,
        "active",
        actor=current_user,
        acknowledge_warnings=acknowledge_warnings,
    ))


@router.post("/{profile_id}/retire", response_model=ProfileSummary)
def retire_profile(profile_id: str, current_user: CurrentUser):
    return _summary(set_profile_status(profile_id, "retired", actor=current_user))


@router.post("/{profile_id}/training-invoices")
async def upload_training_invoice(db: DbSession, profile_id: str, file: UploadFile):
    """Upload a sample invoice PDF for a profile in training status.

    Queues the training pipeline: AI extracts (ground truth), the grounded
    authoring loop authors/validates the profile's model, and side-by-side
    review artifacts are written. Never delivers to the profile output_path.
    """
    profile = get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile.status != "training":
        raise HTTPException(
            status_code=400,
            detail=f"Profile is not in training status (status={profile.status}). Start training first.",
        )

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    safe_name = Path(file.filename).name
    if safe_name != file.filename or safe_name in {"", ".", ".."} or any(
        sep in file.filename for sep in ("/", "\\", "\x00")
    ):
        raise HTTPException(status_code=400, detail="Invalid filename")

    dest = settings.inbound_dir / f"training_{uuid4().hex}_{safe_name}"
    await stream_upload_to(file, dest)

    from stencil.intake.service import process_new_pdf
    from stencil.tasks.worker import train_profile_model_task

    intake_id = process_new_pdf(
        db, dest, intake_source="training", supplier_profile_id=profile_id,
        original_filename=safe_name,
    )
    crud.log_processing_step(
        db, intake_id=intake_id, step="intake", status="completed",
        message=f"Training invoice uploaded for profile {profile_id}",
        details={"profile_id": profile_id, "filename": safe_name},
    )
    async_result = train_profile_model_task.delay(intake_id, profile_id)
    crud.set_intake_celery_task(db, intake_id, async_result.id)
    logger.info("training.upload_queued", intake_id=intake_id,
                profile_id=profile_id, task_id=async_result.id)

    return {"intake_id": intake_id, "profile_id": profile_id, "status": "queued"}


@router.post("/{profile_id}/preview")
async def start_profile_preview(profile_id: str, file: UploadFile):
    """Kick off an AI extraction of a SAMPLE PDF under this profile and return a
    job id to poll. The extraction runs in the Celery worker because it calls
    OpenAI and can take minutes — far longer than an HTTP proxy holds a socket
    open. Poll GET /profiles/preview/{job_id} for the result."""
    from stencil.tasks.worker import preview_profile_task

    profile = get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    pdf_path = await save_uploaded_pdf(file, prefix="preview")
    job_id = uuid4().hex
    preview_profile_task.delay(job_id, profile_id, str(pdf_path))
    logger.info("profile.preview.queued", profile_id=profile_id, job_id=job_id)
    return {"job_id": job_id, "status": "pending"}


@router.get("/preview/{job_id}")
def get_profile_preview(job_id: str):
    """Poll an AI preview job. Returns ``{status: pending}`` until the worker
    finishes, then ``{status: done, preview: {...}}`` or ``{status: error, detail}``.
    The result file is consumed (deleted) once a terminal state is returned."""

    if not re.fullmatch(r"[0-9a-f]{8,64}", job_id):
        raise HTTPException(status_code=400, detail="Invalid job id")
    result_path = (settings.work_dir / "previews" / f"{job_id}.json").resolve()
    base = (settings.work_dir / "previews").resolve()
    if not str(result_path).startswith(str(base)) or not result_path.exists():
        return {"status": "pending"}
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "pending"}
    result_path.unlink(missing_ok=True)  # one-shot
    return payload


@router.get("/{profile_id}/training-status")
def get_training_status(db: DbSession, profile_id: str):
    """Model candidate status, validation counts, and fingerprint for the training panel."""
    profile = get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    record = crud.find_model_by_profile(db, profile_id)
    min_successes = profile.training_config.min_validation_successes

    if record is None:
        return {
            "profile_id": profile_id,
            "profile_status": profile.status,
            "model_id": None,
            "model_status": None,
            "layout_fingerprint": None,
            "validation_success_count": 0,
            "validation_failure_count": 0,
            "validation_attempt_count": 0,
            "min_validation_successes": min_successes,
            "ready_for_approval": False,
            "last_validation_error": None,
            "validation_intake_ids": [],
        }

    return {
        "profile_id": profile_id,
        "profile_status": profile.status,
        "model_id": record.id,
        "model_status": record.status,
        "layout_fingerprint": record.layout_fingerprint,
        "validation_success_count": record.validation_success_count,
        "validation_failure_count": record.validation_failure_count,
        "validation_attempt_count": record.validation_attempt_count,
        "min_validation_successes": min_successes,
        "ready_for_approval": (
            record.status == "candidate"
            and record.validation_success_count >= min_successes
        ),
        "last_validation_error": record.last_validation_error,
        "validation_intake_ids": list(record.validation_intake_ids or []),
    }


class ManualModelRequest(BaseModel):
    """A hand-authored model from the visual builder, plus the sample invoice it
    was built against (used to compute the routing fingerprint + family key)."""

    model_config = {"protected_namespaces": ()}
    sample_intake_id: str
    model_json: dict
    notes: str | None = None


@router.post("/{profile_id}/models", status_code=201)
def create_manual_model(
    db: DbSession, profile_id: str, body: ManualModelRequest, current_user: CurrentUser
):
    """Persist a hand-authored model from the builder as a candidate.

    Identity + routing keys are derived server-side from the profile and the
    sample invoice — the builder only supplies the rule document. Refuses if an
    approved model already exists for the profile (retire it to replace)."""
    from stencil.api.intakes import resolve_intake_pdf_path
    from stencil.api.schemas import ExtractionModelResponse
    from stencil.audit import append_event
    from stencil.fingerprint.fingerprinter import compute_layout_family_key, fingerprint_pdf
    from stencil.models import registry
    from stencil.models.schema import ExtractionModel, ModelStatus

    profile = get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    try:
        model = ExtractionModel.model_validate(body.model_json)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid model rules: {e}") from e

    # Accepts an intake_id or builder sample_id; the resolver 404s if no PDF.
    pdf_path = resolve_intake_pdf_path(body.sample_intake_id)
    fingerprint, features = fingerprint_pdf(pdf_path, profile=profile)
    family_key = compute_layout_family_key(
        features,
        supplier=profile.identity.canonical_name,
        output_type=profile.classification.output_type,
    )

    # Force identity/routing fields so a builder can never mis-key the model.
    model.model_id = profile.profile_id
    model.supplier_profile_id = profile.profile_id
    model.supplier = profile.identity.canonical_name
    model.output_type = profile.classification.output_type
    model.layout_fingerprint = fingerprint
    model.layout_family_key = family_key
    model.status = ModelStatus.CANDIDATE
    model.created_by = "manual"
    model.created_from_intake = body.sample_intake_id
    if body.notes is not None:
        model.notes = body.notes

    result = registry.save_model(db, model)
    if result.action == "kept_existing_approved":
        raise HTTPException(
            status_code=409,
            detail="An approved model already exists for this profile; retire it before replacing.",
        )
    append_event(
        db, entity_type="model", entity_id=model.model_id, action="created_manual", actor=current_user
    )
    db.commit()
    record = crud.get_extraction_model(db, model.model_id)
    return ExtractionModelResponse.model_validate(record)
