"""Extraction model registry — CRUD + filesystem persistence.

Models are keyed by supplier profile: one model per profile (= one per layout
variant), with the layout fingerprint stored for routing and drift detection.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import structlog
from sqlalchemy.orm import Session

from stencil.config import settings
from stencil.db import crud
from stencil.models.schema import ExtractionModel, ModelStatus

logger = structlog.get_logger()

SaveModelAction = Literal[
    "created",
    "updated_candidate",
    "kept_existing_approved",
]


@dataclass(frozen=True)
class SaveModelResult:
    model_id: str
    action: SaveModelAction
    persisted_status: str


def save_model(db: Session, model: ExtractionModel) -> SaveModelResult:
    """Save an ExtractionModel to both the database and the filesystem."""
    model_json = model.model_dump(mode="json")

    save_result = crud.save_extraction_model(
        db,
        model_id=model.model_id,
        version=model.version,
        supplier=model.supplier,
        output_type=model.output_type,
        supplier_profile_id=model.supplier_profile_id,
        layout_fingerprint=model.layout_fingerprint,
        layout_family_key=model.layout_family_key,
        model_json=model_json,
        confidence=model.confidence,
        created_by=model.created_by,
        created_from_intake=model.created_from_intake,
        notes=model.notes,
    )

    record = save_result.record
    # Write the PERSISTED record to the filesystem (not the input model) so an
    # existing approved model isn't clobbered on disk by a discarded replacement.
    persisted = _try_parse_model(record.model_json)
    if persisted is not None:
        _save_model_to_filesystem(persisted)

    logger.info(
        "registry.model_saved",
        model_id=model.model_id,
        supplier=model.supplier,
        action=save_result.action,
        status=record.status,
    )
    return SaveModelResult(
        model_id=record.id,
        action=save_result.action,
        persisted_status=record.status,
    )


def save_failed_model(db: Session, model: ExtractionModel, reason: str) -> SaveModelResult:
    """Persist the last authored rules after failed validation.

    A failed training run should still leave a concrete, editable rule document
    in the workbench. The model stays non-approvable because its status is
    failed_validation and the training-set gate still fails.
    """
    model.status = ModelStatus.FAILED_VALIDATION
    save_result = crud.save_extraction_model(
        db,
        model_id=model.model_id,
        version=model.version,
        supplier=model.supplier,
        output_type=model.output_type,
        supplier_profile_id=model.supplier_profile_id,
        layout_fingerprint=model.layout_fingerprint,
        layout_family_key=model.layout_family_key,
        model_json=model.model_dump(mode="json"),
        confidence=model.confidence,
        created_by=model.created_by,
        created_from_intake=model.created_from_intake,
        notes=model.notes,
    )

    record = save_result.record
    if save_result.action != "kept_existing_approved":
        record = crud.update_model_status(db, record.id, ModelStatus.FAILED_VALIDATION.value)
        if record is not None:
            record = crud.set_model_last_validation_error(db, record.id, reason) or record

    persisted = _try_parse_model(record.model_json) if record is not None else None
    if persisted is not None:
        _save_model_to_filesystem(persisted)

    logger.info(
        "registry.failed_model_saved",
        model_id=model.model_id,
        supplier=model.supplier,
        action=save_result.action,
        status=record.status if record is not None else None,
    )
    return SaveModelResult(
        model_id=record.id if record is not None else model.model_id,
        action=save_result.action,
        persisted_status=record.status if record is not None else "unknown",
    )


def load_model(db: Session, model_id: str) -> ExtractionModel | None:
    record = crud.get_extraction_model(db, model_id)
    if record is None:
        return None
    return _try_parse_model(record.model_json)


def find_approved_model(db: Session, profile_id: str, fingerprint: str) -> ExtractionModel | None:
    """Production routing: approved model for this profile with an EXACT fingerprint match."""
    record = crud.find_model_by_profile(db, profile_id, status="approved")
    if record is None:
        return None
    if record.layout_fingerprint != fingerprint:
        return None
    return _try_parse_model(record.model_json)


def find_candidate_model(db: Session, profile_id: str, fingerprint: str | None = None) -> ExtractionModel | None:
    """Candidate model for this profile (training/validation). Fingerprint optional filter."""
    record = crud.find_model_by_profile(db, profile_id, status="candidate")
    if record is None:
        return None
    if fingerprint is not None and record.layout_fingerprint != fingerprint:
        return None
    return _try_parse_model(record.model_json)


def find_model_for_profile(db: Session, profile_id: str, status: str | None = None) -> ExtractionModel | None:
    """Any model owned by this profile, optionally filtered by status."""
    record = crud.find_model_by_profile(db, profile_id, status=status)
    if record is None:
        return None
    return _try_parse_model(record.model_json)


def approve_model(db: Session, model_id: str, approved_by: str) -> bool:
    """Approve a candidate model for production use."""
    record = crud.update_model_status(db, model_id, "approved", approved_by=approved_by)
    if record is None:
        return False
    model = _try_parse_model(record.model_json)
    if model is not None:
        model.status = ModelStatus.APPROVED
        model.approved_by = approved_by
        model.approved_at = datetime.now()
        _save_model_to_filesystem(model)
    logger.info("registry.model_approved", model_id=model_id, approved_by=approved_by)
    return True


def bootstrap_candidate(db: Session, profile) -> ExtractionModel:
    """Create the first, empty candidate model for a profile (cold start).

    The model has no rules and an empty fingerprint yet — the workbench fills it
    in: uploaded invoices are ingested for ground truth, then the explicit Train
    step authors the real rules and computes the fingerprint from the first PDF.
    One model per profile, so the v1 id equals the profile id.
    """
    model = ExtractionModel(
        model_id=profile.profile_id,
        supplier_profile_id=profile.profile_id,
        supplier=profile.identity.canonical_name,
        output_type=profile.classification.output_type,
        layout_fingerprint="",
        status=ModelStatus.CANDIDATE,
        created_by="workbench_bootstrap",
        notes="Created for the training workbench",
    )
    save_model(db, model)
    logger.info("registry.model_bootstrapped", model_id=model.model_id)
    return model


def clone_model(db: Session, model_id: str) -> ExtractionModel | None:
    """Create a candidate clone of a model (for safely retraining an approved one).

    The clone is a NEW row with a versioned id ({profile}#v{N}); the source model
    is untouched, so production keeps using it until the clone is approved.
    """
    record = crud.get_extraction_model(db, model_id)
    if record is None:
        return None
    source = _try_parse_model(record.model_json)
    if source is None:
        return None

    profile_id = source.supplier_profile_id
    next_version = crud.max_model_version_for_profile(db, profile_id) + 1
    clone = source.model_copy(deep=True)
    # URL-safe versioned id (no '#': it breaks proxies / is a URL fragment).
    clone.model_id = f"{profile_id}__v{next_version}"
    clone.version = next_version
    clone.status = ModelStatus.CANDIDATE
    clone.approved_by = None
    clone.approved_at = None
    clone.notes = f"Clone of {model_id} for retraining"
    save_model(db, clone)
    logger.info("registry.model_cloned", source=model_id, clone=clone.model_id)
    return clone


def promote_model(db: Session, model_id: str, approved_by: str) -> bool:
    """Approve a model and retire any other approved model for the same profile.

    The previously-approved row is retired (not deleted) so it stays available
    for rollback. Returns False if the model doesn't exist.
    """
    record = crud.get_extraction_model(db, model_id)
    if record is None:
        return False
    profile_id = record.supplier_profile_id
    if not approve_model(db, model_id, approved_by=approved_by):
        return False
    if profile_id:
        for other in crud.list_models_by_profile(db, profile_id):
            if other.id != model_id and other.status == "approved":
                retire_model(db, other.id, retired_by=approved_by)
                logger.info("registry.model_superseded", superseded=other.id, by=model_id)
    return True


def retire_model(db: Session, model_id: str, *, retired_by: str | None = None) -> bool:
    record = crud.update_model_status(db, model_id, "retired", retired_by=retired_by)
    if record is None:
        return False
    model = _try_parse_model(record.model_json)
    if model is not None:
        model.status = ModelStatus.RETIRED
        _save_model_to_filesystem(model)
    logger.info("registry.model_retired", model_id=model_id)
    return True


def flag_model(db: Session, model_id: str, reason: str):
    """Flag a model that failed in production — records the failure, does NOT retire."""
    record = crud.record_model_validation_failure(db, model_id, reason)
    if record is None:
        return None
    logger.warning("registry.model_flagged", model_id=model_id, reason=reason)
    return record


def record_validation_success(db: Session, model_id: str, intake_id: str):
    record = crud.record_model_validation_success(db, model_id, intake_id)
    if record is None:
        return None
    model = _try_parse_model(record.model_json)
    if model is not None:
        _save_model_to_filesystem(model)
    return record


def record_validation_failure(db: Session, model_id: str, error: str):
    record = crud.record_model_validation_failure(db, model_id, error)
    if record is None:
        return None
    model = _try_parse_model(record.model_json)
    if model is not None:
        _save_model_to_filesystem(model)
    return record


def set_training_membership(
    db: Session,
    model_id: str,
    *,
    train_intake_ids: list[str],
    holdout_intake_ids: list[str],
    updated_by: str | None = None,
) -> ExtractionModel | None:
    """Persist a model's intended Train/Holdout split without re-authoring.

    Holdout always wins on conflict (an id cannot be in both lists), and the
    primary authoring invoice (created_from_intake) can never be held out.
    """
    record = crud.get_extraction_model(db, model_id)
    if record is None:
        return None
    model = _try_parse_model(record.model_json)
    if model is None:
        return None

    holdout = list(dict.fromkeys(i.strip() for i in holdout_intake_ids if i and i.strip()))
    if model.created_from_intake:
        holdout = [i for i in holdout if i != model.created_from_intake]
    holdout_set = set(holdout)
    train = list(dict.fromkeys(
        i.strip() for i in train_intake_ids if i and i.strip() and i.strip() not in holdout_set
    ))

    model.training_intake_ids = train
    model.holdout_intake_ids = holdout
    updated = update_model_rules(db, model_id, model.model_dump(mode="json"), updated_by=updated_by)
    logger.info(
        "registry.training_membership_set",
        model_id=model_id, train=len(train), holdout=len(holdout),
    )
    return updated


def add_training_intake(
    db: Session,
    model_id: str,
    intake_id: str,
    *,
    role: str = "train",
    updated_by: str | None = None,
) -> ExtractionModel | None:
    """Append one intake to a model's Train or Holdout list (idempotent)."""
    record = crud.get_extraction_model(db, model_id)
    if record is None:
        return None
    model = _try_parse_model(record.model_json)
    if model is None:
        return None
    train = list(model.training_intake_ids or [])
    holdout = list(model.holdout_intake_ids or [])
    train = [i for i in train if i != intake_id]
    holdout = [i for i in holdout if i != intake_id]
    if role == "holdout":
        holdout.append(intake_id)
    else:
        train.append(intake_id)
    return set_training_membership(
        db, model_id, train_intake_ids=train, holdout_intake_ids=holdout, updated_by=updated_by,
    )


def update_model_rules(
    db: Session,
    model_id: str,
    model_json: dict,
    *,
    updated_by: str | None = None,
) -> ExtractionModel | None:
    """Replace a model's rules (manual edit) and sync the filesystem JSON."""
    record = crud.update_model_rules(db, model_id, model_json, updated_by=updated_by)
    if record is None:
        return None
    model = _try_parse_model(record.model_json)
    if model is not None:
        _save_model_to_filesystem(model)
    logger.info("registry.model_rules_updated", model_id=model_id)
    return model


def delete_model(db: Session, model_id: str) -> bool:
    """Permanently delete a model: removes the DB record AND the filesystem JSON."""
    deleted = crud.delete_extraction_model(db, model_id)
    if not deleted:
        return False
    path = settings.extraction_models_dir / f"{model_id}.json"
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("registry.model_file_delete_failed", model_id=model_id, error=str(e))
    logger.info("registry.model_deleted", model_id=model_id)
    return True


def _try_parse_model(model_json: dict) -> ExtractionModel | None:
    try:
        return ExtractionModel.model_validate(model_json)
    except Exception:
        return None


def _save_model_to_filesystem(model: ExtractionModel) -> Path:
    models_dir = settings.extraction_models_dir
    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / f"{model.model_id}.json"
    path.write_text(
        json.dumps(model.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )
    return path
