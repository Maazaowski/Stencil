"""End-to-end pipeline orchestrator: profile + fingerprint routed extraction.

Routing (production):
1. Resolve the supplier profile (watcher directory, upload, or AI classification).
2. Compute the layout fingerprint.
3. Approved model for (profile_id, exact fingerprint)  -> model extraction, zero AI.
4. Candidate model with the same fingerprint           -> AI extraction + side-by-side validation.
5. Active profile but unknown fingerprint              -> AI extraction + layout-drift exception.
6. No profile                                          -> AI extraction only.

Training (profile.status == "training", explicit uploads only):
AI extraction (ground truth) -> grounded authoring loop -> candidate model saved
on exact match -> subsequent training uploads validate and credit the candidate.
"""

import re
from datetime import datetime
from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from stencil import runtime_settings
from stencil.classification.classifier import ClassificationResult, classify_invoice
from stencil.config import settings
from stencil.db import crud
from stencil.exceptions.handler import (
    ExceptionReason,
    create_exception_package,
)
from stencil.extraction.extractor import (
    build_canonical_invoice,
    extract_invoice,
)
from stencil.extraction.layout import LayoutDocument, extract_layout_document
from stencil.extraction.normalization import apply_layout_profile_hints
from stencil.fields.loader import (
    default_field_schema,
    resolve_merged_field_schema,
)
from stencil.fingerprint.fingerprinter import compute_layout_family_key, fingerprint_pdf
from stencil.intake.service import process_new_pdf
from stencil.models.diff import diff_invoices
from stencil.models.interpreter import execute_model
from stencil.models.registry import (
    find_approved_model,
    find_candidate_model,
    find_model_for_profile,
    flag_model,
    record_validation_failure,
    record_validation_success,
)
from stencil.models.sample_authoring import (
    author_from_sample,
    replay_and_verify,
    should_author_from_sample,
)
from stencil.models.schema import ExtractionModel
from stencil.models.training import author_and_save_profile_model
from stencil.output.json_writer import (
    write_canonical_json,
    write_extraction_log,
    write_manifest,
)
from stencil.output.naming import derive_output_xlsx_name
from stencil.output.review_artifacts import write_model_review_artifacts
from stencil.output.xlsx_writer import build_output_rows, write_xlsx
from stencil.pipeline.cancellation import check_cancelled, clear_cancel
from stencil.pipeline.events import publish_completed, publish_event, publish_failed
from stencil.pipeline.timing import stage_timer
from stencil.profiles.loader import find_profile_by_supplier, get_profile
from stencil.profiles.schema import LineItemHints, SupplierProfile
from stencil.specs.loader import resolve_output_spec
from stencil.validation.reconciler import reconcile
from stencil.validation.schema import CanonicalInvoice, ChargeType, ExtractedDocument, ReconciliationResult

logger = structlog.get_logger()


class NonRetryablePipelineError(Exception):
    """A deliberate pipeline failure that should not be retried by Celery."""


def _profile_log_details(profile: SupplierProfile | None, *, source: str | None = None) -> dict:
    details = {
        "profile_id": profile.profile_id if profile else None,
        "profile_version": profile.version if profile else None,
        "profile_status": profile.status if profile else None,
    }
    if source:
        details["profile_source"] = source
    return details


def process_invoice(db: Session, pdf_path: Path) -> str:
    """Full pipeline: intake -> fingerprint -> route -> extract -> validate -> output.

    Returns the intake_id.  Used by the file watcher (synchronous).
    """
    intake_id = process_new_pdf(db, pdf_path, intake_source="watcher")
    publish_event(intake_id, "intake", "completed", "PDF registered and archived")
    continue_pipeline(db, intake_id)
    return intake_id


def _resolve_profile_for_fingerprint(
    db: Session,
    intake_id: str,
    profile_id: str | None,
) -> SupplierProfile | None:
    """Return the supplier profile to use for layout fingerprinting, when known."""
    if profile_id:
        return get_profile(profile_id)

    intake = crud.get_intake(db, intake_id)
    if intake is not None and intake.supplier_profile_id:
        return get_profile(intake.supplier_profile_id)

    return None


def continue_pipeline(db: Session, intake_id: str,
                      profile_id: str | None = None,
                      mode: str = "production") -> None:
    """Run pipeline steps 2-6 for an existing intake.

    mode="production": profile + fingerprint routing (model / candidate / drift / AI).
    mode="training":   explicit training upload for a profile in training status.
    """
    processing_pdf = settings.processing_dir / intake_id / "original.pdf"

    try:
        clear_cancel(intake_id)
        check_cancelled(intake_id)

        # Step 2: Fingerprint (profile-scoped when layout_fingerprint is configured)
        publish_event(intake_id, "fingerprint", "running", "Analyzing PDF layout...")
        profile_for_fingerprint = _resolve_profile_for_fingerprint(db, intake_id, profile_id)
        with stage_timer() as fp_timer:
            fingerprint, features = fingerprint_pdf(
                processing_pdf,
                profile=profile_for_fingerprint,
            )
        crud.update_intake_fingerprint(db, intake_id, fingerprint)
        crud.log_processing_step(
            db, intake_id=intake_id, step="fingerprint", status="completed",
            message=f"Layout fingerprint: {fingerprint[:30]}...",
            duration_ms=fp_timer["duration_ms"],
            details={
                "page_count": features.page_count,
                "label_count": len(features.first_page_labels),
                "duration_ms": fp_timer["duration_ms"],
                **_profile_log_details(profile_for_fingerprint, source="fingerprint_profile"),
            },
        )
        publish_event(
            intake_id, "fingerprint", "completed",
            f"Layout fingerprint computed ({features.page_count} pages)",
            details={"duration_ms": fp_timer["duration_ms"]},
        )

        check_cancelled(intake_id)

        if mode == "training":
            if not profile_id:
                raise NonRetryablePipelineError("training mode requires a profile_id")
            profile = get_profile(profile_id)
            if profile is None:
                raise NonRetryablePipelineError(f"profile not found: {profile_id}")
            if profile.status != "training":
                raise NonRetryablePipelineError(
                    f"profile {profile_id} is not in training status (status={profile.status})"
                )
            invoice, job_id = _run_training_path(
                db, intake_id, processing_pdf, profile, fingerprint, features,
            )
            profile_for_output = None  # training never copies to output_path
        else:
            invoice, job_id, profile_for_output = _run_production_path(
                db, intake_id, processing_pdf, profile_id, fingerprint, features,
            )

        publish_event(intake_id, "extraction", "completed",
                      f"Extracted {len(invoice.line_items)} line items")

        _flag_rows_missing_service_id(db, intake_id, invoice, processing_pdf)

        check_cancelled(intake_id)

        # Step 4+5: Reconcile
        publish_event(intake_id, "reconciliation", "running", "Verifying totals and line items...")
        reconciliation_profile = profile_for_output or (
            find_profile_by_supplier(invoice.header.supplier_name)
            if invoice.header.supplier_name else None
        )
        field_schema = (
            resolve_merged_field_schema(reconciliation_profile)
            if reconciliation_profile else default_field_schema()
        )
        with stage_timer() as recon_timer:
            recon = reconcile(invoice, field_schema)
        if recon is not None:
            invoice.reconciliation = recon
            invoice.warnings.extend(recon.warnings)
            crud.log_processing_step(
                db, intake_id=intake_id, step="reconciliation", status="completed",
                message=f"Reconciled: {recon.is_reconciled}, variance: {recon.variance_pct:.4%}",
                duration_ms=recon_timer["duration_ms"],
                details={"is_reconciled": recon.is_reconciled,
                         "variance": str(recon.variance),
                         "duration_ms": recon_timer["duration_ms"],
                         **_profile_log_details(reconciliation_profile, source="reconciliation_profile")},
            )
            publish_event(intake_id, "reconciliation", "completed",
                          f"Reconciled: {recon.is_reconciled}, variance: {recon.variance_pct:.4%}",
                          details={"duration_ms": recon_timer["duration_ms"]})

            if not recon.is_reconciled:
                create_exception_package(
                    intake_id=intake_id,
                    reason=ExceptionReason.RECONCILIATION_FAILURE,
                    message=f"Variance {recon.variance_pct:.4%} exceeds threshold",
                    pdf_path=processing_pdf,
                    partial_invoice=invoice,
                )
        else:
            crud.log_processing_step(
                db, intake_id=intake_id, step="reconciliation", status="skipped",
                message="Reconciliation skipped (schema has no amount/total roles)",
                duration_ms=recon_timer["duration_ms"],
            )
            publish_event(intake_id, "reconciliation", "skipped", "Reconciliation not applicable")

        check_cancelled(intake_id)

        # Step 6: Generate output package
        publish_event(intake_id, "output", "running", "Generating XLSX and JSON output...")
        with stage_timer() as output_timer:
            profile = profile_for_output
            if mode != "training" and profile is None and invoice.header.supplier_name:
                profile = find_profile_by_supplier(invoice.header.supplier_name)
            _intake = crud.get_intake(db, intake_id)
            account_label = _intake.account_label if _intake else None
            delivery_blockers = [
                *_profile_delivery_blockers(profile),
                *_unverified_large_document_blockers(invoice, recon),
            ]
            custom_output_dir = (
                _resolve_output_dir(profile, account_label)
                if profile and not delivery_blockers
                else None
            )
            if delivery_blockers:
                create_exception_package(
                    intake_id=intake_id,
                    reason=ExceptionReason.PROFILE_VALIDATION_FAILURE,
                    message="Profile delivery paused: " + "; ".join(delivery_blockers),
                    pdf_path=processing_pdf,
                    partial_invoice=invoice,
                    error_details={"profile_id": profile.profile_id, "blockers": delivery_blockers},
                )
            xlsx_path, json_path, log_path, manifest_path = _generate_output(
                db, intake_id, invoice, custom_output_dir=custom_output_dir,
                profile=profile,
                account_label=account_label,
                duration_ms=output_timer["duration_ms"],
            )
        publish_event(
            intake_id, "output", "completed",
            f"Output written to {settings.completed_dir / intake_id}",
            details={"duration_ms": output_timer["duration_ms"]},
        )
        crud.update_extraction_job(
            db,
            job_id,
            is_reconciled=recon.is_reconciled if recon is not None else None,
            reconciliation_variance=float(recon.variance) if recon is not None else None,
            canonical_json_path=str(json_path),
            xlsx_output_path=str(xlsx_path),
            manifest_path=str(manifest_path),
            warnings={"items": invoice.warnings} if invoice.warnings else None,
        )

        warnings: list[str] = []
        if recon is not None and not recon.is_reconciled:
            warnings.append(f"Reconciliation failed (variance {recon.variance_pct:.2%})")
        if delivery_blockers:
            warnings.append("Delivery paused by profile validation: " + "; ".join(delivery_blockers))
        has_reconciliation_failure = recon is not None and not recon.is_reconciled
        if invoice.exceptions:
            warnings.extend(str(item) for item in invoice.exceptions)
        if invoice.exceptions and not has_reconciliation_failure:
            create_exception_package(
                intake_id=intake_id,
                reason=ExceptionReason.EXTRACTION_ERROR,
                message="; ".join(str(item) for item in invoice.exceptions),
                pdf_path=processing_pdf,
                partial_invoice=invoice,
                error_details={"exceptions": invoice.exceptions},
            )

        if mode == "training" and _training_outcome_failed(db, intake_id):
            warnings.append("Model training did not converge — AI output delivered for review")

        if warnings:
            crud.update_intake_status(db, intake_id, "completed_with_warnings",
                                      error_message="; ".join(warnings))
        else:
            crud.update_intake_status(db, intake_id, "completed")
        publish_completed(intake_id)

    except Exception as e:
        logger.error("pipeline.failed", intake_id=intake_id, error=str(e))
        crud.update_intake_status(db, intake_id, "failed", error_message=str(e))
        publish_failed(intake_id, str(e))
        crud.log_processing_step(
            db, intake_id=intake_id, step="pipeline", status="failed",
            message=str(e),
        )
        raise


# ---------------------------------------------------------------------------
# Production routing
# ---------------------------------------------------------------------------


def _run_production_path(
    db: Session,
    intake_id: str,
    pdf_path: Path,
    profile_id: str | None,
    fingerprint: str,
    features,
) -> tuple[CanonicalInvoice, str, SupplierProfile | None]:
    """Profile + fingerprint routing. Returns (invoice, job_id, profile)."""
    # Resolve the profile. The watcher provides it directly; manual uploads
    # without a profile are classified by AI first.
    profile = get_profile(profile_id) if profile_id else None
    profile_source = "watcher_profile" if profile else "classification"

    if profile is not None:
        classification = ClassificationResult(
            supplier_name=profile.identity.canonical_name,
            output_type=profile.classification.output_type,
            confidence=1.0,
        )
        crud.log_processing_step(
            db, intake_id=intake_id, step="classification", status="completed",
            message=f"Known supplier/profile: {classification.supplier_name} / {classification.output_type}",
            details=_profile_log_details(profile, source=profile_source),
        )
        publish_event(intake_id, "classification", "completed",
                      f"Known supplier: {classification.supplier_name}")
    else:
        publish_event(intake_id, "classification", "running", "Classifying invoice...")
        classification = _classify_and_log(db, intake_id, pdf_path)
        profile = find_profile_by_supplier(classification.supplier_name)
        profile_source = "active_supplier_lookup"

    if profile is None:
        invoice, job_id, _ = _process_ai_path(
            db, intake_id, pdf_path, fingerprint, features.page_count,
            classification=classification, supplier_profile=None,
        )
        return invoice, job_id, None

    # 1. Approved model with an EXACT fingerprint match -> zero-AI extraction.
    approved = find_approved_model(db, profile.profile_id, fingerprint)
    if approved is not None:
        invoice, job_id = _process_approved_model(
            db, intake_id, pdf_path, approved, fingerprint, classification, profile,
        )
        return invoice, job_id, profile

    candidate = find_candidate_model(db, profile.profile_id, fingerprint)

    # 1b. A long document is where reading every page is both expensive and
    #     unreliable -- 123 sequential AI calls on a 656-page invoice, returning
    #     between 0 and 384 rows across runs of the same file. Prefer rules:
    #     replay a candidate if one exists, else author from a page sample. Both
    #     are accepted only if the rows reconcile against the document's own
    #     stated totals, and anything short of that falls through to full
    #     extraction below, so this can save work but never lose data.
    if should_author_from_sample(features.page_count, profile):
        rules_first = _try_rules_before_reading(
            db, intake_id, pdf_path, profile, fingerprint,
            candidate=candidate, page_count=features.page_count,
        )
        if rules_first is not None:
            invoice, job_id = rules_first
            return invoice, job_id, profile

    # 2. Candidate model with the same fingerprint -> AI + side-by-side validation.
    if candidate is not None:
        invoice, job_id = _process_candidate_model(
            db, intake_id, pdf_path, candidate, fingerprint, classification, profile,
        )
        return invoice, job_id, profile

    # 3. Active profile but no model matches this fingerprint -> layout drift.
    existing_model = find_model_for_profile(db, profile.profile_id)
    if existing_model is not None and existing_model.layout_fingerprint != fingerprint:
        _flag_layout_drift(db, intake_id, pdf_path, profile, existing_model, fingerprint)

    invoice, job_id, _ = _process_ai_path(
        db, intake_id, pdf_path, fingerprint, features.page_count,
        classification=classification, supplier_profile=profile,
    )
    return invoice, job_id, profile


def _try_rules_before_reading(
    db: Session,
    intake_id: str,
    pdf_path: Path,
    profile: SupplierProfile,
    fingerprint: str,
    *,
    candidate: ExtractionModel | None,
    page_count: int,
) -> tuple[CanonicalInvoice, str] | None:
    """Try to answer a long document with rules instead of full extraction.

    Returns ``None`` for every failure, which sends the caller to ordinary AI
    extraction -- the path that always works. Nothing here is allowed to be the
    reason a document fails to process.
    """
    outcome = None
    try:
        if candidate is not None:
            outcome = replay_and_verify(candidate, pdf_path, intake_id)
            if not outcome.usable:
                logger.info("pipeline.rules_first.candidate_rejected",
                            intake_id=intake_id, reason=outcome.reason)
                outcome = None
        if outcome is None:
            outcome = author_from_sample(
                db, intake_id=intake_id, pdf_path=pdf_path, profile=profile,
                fingerprint=fingerprint,
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("pipeline.rules_first.failed", intake_id=intake_id, error=str(exc))
        return None

    if not outcome.usable or outcome.invoice is None:
        crud.log_processing_step(
            db, intake_id=intake_id, step="rules_first", status="skipped",
            message=f"Rules-first declined ({outcome.reason}); reading the full document.",
            details={"page_count": page_count, **outcome.metrics},
        )
        return None

    model = outcome.model
    crud.log_processing_step(
        db, intake_id=intake_id, step="rules_first", status="completed",
        message=(
            f"Authored from {len(outcome.sample_pages)} of {page_count} pages and "
            f"replayed across the document ({len(outcome.invoice.rows)} rows)."
            if outcome.sample_pages else
            f"Replayed existing rules across {page_count} pages "
            f"({len(outcome.invoice.rows)} rows)."
        ),
        details={
            "extraction_path": "model",
            "model_id": model.model_id if model else None,
            "sample_pages": outcome.sample_pages,
            **outcome.metrics,
            **_profile_log_details(profile, source="rules_first"),
        },
    )
    publish_event(intake_id, "extraction", "completed",
                  f"Rules replayed across {page_count} pages")

    job = crud.create_extraction_job(
        db,
        intake_id=intake_id,
        extraction_path="model",
        extraction_model_id=model.model_id if model else None,
        supplier_profile_id=profile.profile_id,
        supplier_name=profile.identity.canonical_name,
        output_type=profile.classification.output_type,
    )
    crud.update_extraction_job(
        db, job.id, status="completed", started_at=datetime.now(),
        tokens_input=outcome.tokens_input, tokens_output=outcome.tokens_output,
    )

    invoice = outcome.invoice
    _normalize_line_items(
        invoice,
        keep_zero_amount=_keep_zero_line_items(profile),
        require_identifier=_require_identifier(profile),
    )
    return invoice, job.id


def _flag_layout_drift(
    db: Session,
    intake_id: str,
    pdf_path: Path,
    profile: SupplierProfile,
    model: ExtractionModel,
    fingerprint: str,
) -> None:
    """The invoice's layout doesn't match the profile's model — never silently
    use the wrong model. Log + raise an exception package; an operator creates
    a new profile for the new layout variant."""
    crud.log_processing_step(
        db, intake_id=intake_id, step="layout_drift", status="detected",
        message=(
            f"Invoice layout does not match the model for profile {profile.profile_id}. "
            f"Falling back to AI extraction. If this layout recurs, create a new supplier "
            f"profile for it and train a dedicated model."
        ),
        details={
            "model_id": model.model_id,
            "model_fingerprint": (model.layout_fingerprint or "")[:16],
            "invoice_fingerprint": fingerprint[:16],
            **_profile_log_details(profile, source="drift_detection"),
        },
    )
    try:
        create_exception_package(
            intake_id=intake_id,
            reason=ExceptionReason.LAYOUT_DRIFT,
            message=(
                f"Layout drift for profile {profile.profile_id}: invoice fingerprint "
                f"{fingerprint[:16]}... differs from model fingerprint "
                f"{(model.layout_fingerprint or '')[:16]}..."
            ),
            pdf_path=pdf_path,
        )
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("pipeline.drift_exception_failed", intake_id=intake_id, error=str(exc))
    logger.warning("pipeline.layout_drift", intake_id=intake_id,
                   profile_id=profile.profile_id, model_id=model.model_id)


def _process_approved_model(
    db: Session,
    intake_id: str,
    pdf_path: Path,
    model: ExtractionModel,
    fingerprint: str,
    classification: ClassificationResult,
    profile: SupplierProfile,
) -> tuple[CanonicalInvoice, str]:
    """APPROVED: model only (zero AI cost), AI fallback on failure."""
    crud.log_processing_step(
        db, intake_id=intake_id, step="routing", status="completed",
        message=f"Using approved model (zero AI cost): {model.model_id}",
        details={
            "extraction_path": "model",
            "model_id": model.model_id,
            **_profile_log_details(profile, source="fingerprint_routing"),
        },
    )

    job = crud.create_extraction_job(
        db,
        intake_id=intake_id,
        extraction_path="model",
        extraction_model_id=model.model_id,
        supplier_profile_id=profile.profile_id,
        supplier_name=model.supplier,
        output_type=model.output_type,
    )
    crud.update_extraction_job(db, job.id, status="running", started_at=datetime.now())
    crud.increment_model_usage(db, model.model_id)

    try:
        with stage_timer() as model_timer:
            invoice = execute_model(model, pdf_path, intake_id)
            _normalize_line_items(
                invoice,
                keep_zero_amount=_keep_zero_line_items(profile),
                require_identifier=_require_identifier(profile),
            )
    except Exception as e:
        logger.warning("pipeline.model_execution_failed", intake_id=intake_id,
                       model_id=model.model_id, error=str(e))
        crud.log_processing_step(
            db, intake_id=intake_id, step="model_extraction", status="failed",
            message=f"Approved model failed: {e}. Flagging and falling back to AI.",
        )
        flag_model(db, model.model_id, f"Execution error: {e}")
        crud.update_extraction_job(db, job.id, status="fallback_to_ai")
        invoice, job_id, _ = _process_ai_path(
            db, intake_id, pdf_path, fingerprint, 0,
            classification=classification, supplier_profile=profile,
        )
        return invoice, job_id

    is_valid, reason = _validate_model_output_for_reuse(invoice)
    if not is_valid:
        logger.warning("pipeline.approved_model_validation_failed",
                       model_id=model.model_id, reason=reason)
        crud.log_processing_step(
            db, intake_id=intake_id, step="model_extraction", status="failed",
            message=f"Approved model output failed validation: {reason}. Flagging and falling back to AI.",
        )
        flag_model(db, model.model_id, f"Output validation failed: {reason}")
        crud.update_extraction_job(db, job.id, status="fallback_to_ai")
        invoice, job_id, _ = _process_ai_path(
            db, intake_id, pdf_path, fingerprint, invoice.metadata.total_pages,
            classification=classification, supplier_profile=profile,
        )
        return invoice, job_id

    crud.update_extraction_job(
        db, job.id,
        status="completed",
        completed_at=datetime.now(),
        overall_confidence=invoice.metadata.overall_confidence,
        line_item_count=len(invoice.line_items),
    )
    crud.log_processing_step(
        db, intake_id=intake_id, step="model_extraction", status="completed",
        message=f"Extracted {len(invoice.line_items)} line items via approved model (zero AI cost)",
        duration_ms=model_timer["duration_ms"],
        details={"duration_ms": model_timer["duration_ms"]},
    )
    logger.info("pipeline.model_path.completed", intake_id=intake_id,
                model=model.model_id, items=len(invoice.line_items))
    return invoice, job.id


def _process_candidate_model(
    db: Session,
    intake_id: str,
    pdf_path: Path,
    model: ExtractionModel,
    fingerprint: str,
    classification: ClassificationResult,
    profile: SupplierProfile,
) -> tuple[CanonicalInvoice, str]:
    """CANDIDATE: AI extracts (authoritative), model validates alongside."""
    crud.log_processing_step(
        db, intake_id=intake_id, step="routing", status="completed",
        message=f"Candidate model found — AI extraction + model validation: {model.model_id}",
        details={
            "extraction_path": "ai+model_validation",
            "model_id": model.model_id,
            **_profile_log_details(profile, source="fingerprint_routing"),
        },
    )

    invoice, job_id, _ = _process_ai_path(
        db, intake_id, pdf_path, fingerprint, 0,
        classification=classification, supplier_profile=profile,
    )

    try:
        model_invoice = execute_model(model, pdf_path, intake_id)
        _normalize_line_items(
            model_invoice,
            keep_zero_amount=_keep_zero_line_items(profile),
            require_identifier=_require_identifier(profile),
        )
        _validate_and_credit_model(db, intake_id, model, model_invoice, invoice, profile)
    except Exception as e:
        record_validation_failure(db, model.model_id, f"model execution error: {e}")
        logger.warning("pipeline.candidate_model_validation_error",
                       model_id=model.model_id, error=str(e))

    return invoice, job_id


# ---------------------------------------------------------------------------
# Training path
# ---------------------------------------------------------------------------


def _run_training_path(
    db: Session,
    intake_id: str,
    pdf_path: Path,
    profile: SupplierProfile,
    fingerprint: str,
    features,
) -> tuple[CanonicalInvoice, str]:
    """Explicit training upload: AI ground truth + author or validate the model."""
    classification = ClassificationResult(
        supplier_name=profile.identity.canonical_name,
        output_type=profile.classification.output_type,
        confidence=1.0,
    )
    crud.log_processing_step(
        db, intake_id=intake_id, step="classification", status="completed",
        message=f"Training upload for profile: {profile.profile_id}",
        details=_profile_log_details(profile, source="training_upload"),
    )
    layout_family_key = compute_layout_family_key(
        features,
        supplier=classification.supplier_name,
        output_type=classification.output_type,
    )

    # AI extraction is the ground truth during training.
    invoice, job_id, _ = _process_ai_path(
        db, intake_id, pdf_path, fingerprint, features.page_count,
        classification=classification, supplier_profile=profile,
    )

    candidate = find_candidate_model(db, profile.profile_id)
    approved = find_model_for_profile(db, profile.profile_id, status="approved")

    if approved is not None:
        crud.log_processing_step(
            db, intake_id=intake_id, step="model_training", status="skipped",
            message=f"Profile already has an approved model ({approved.model_id}); retire it to retrain.",
            details={"model_id": approved.model_id},
        )
        return invoice, job_id

    if candidate is not None:
        if candidate.layout_fingerprint and candidate.layout_fingerprint != fingerprint:
            record_validation_failure(
                db, candidate.model_id,
                f"training invoice fingerprint {fingerprint[:16]}... differs from model "
                f"fingerprint {candidate.layout_fingerprint[:16]}...",
            )
            crud.log_processing_step(
                db, intake_id=intake_id, step="model_validation", status="failed",
                message=(
                    "Training invoice has a DIFFERENT layout fingerprint than the candidate model. "
                    "If this is a genuinely different layout, create a separate profile for it."
                ),
                details={"model_id": candidate.model_id,
                         "model_fingerprint": candidate.layout_fingerprint[:16],
                         "invoice_fingerprint": fingerprint[:16]},
            )
            return invoice, job_id

        # Validate the existing candidate against this new training invoice.
        matched = False
        try:
            model_invoice = execute_model(candidate, pdf_path, intake_id)
            _normalize_line_items(
                model_invoice,
                keep_zero_amount=_keep_zero_line_items(profile),
                require_identifier=_require_identifier(profile),
            )
            matched = _validate_and_credit_model(db, intake_id, candidate, model_invoice, invoice, profile)
        except Exception as e:
            record_validation_failure(db, candidate.model_id, f"model execution error: {e}")
            crud.log_processing_step(
                db, intake_id=intake_id, step="model_validation", status="failed",
                message=f"Candidate model failed to execute on training invoice: {e}",
                details={"model_id": candidate.model_id},
            )

        # Mismatch -> this invoice has a row shape the candidate doesn't cover.
        # Training is the workbench's job now (never self-train in the pipeline):
        # record the failure and point the operator at the workbench to add this
        # invoice and retrain deliberately.
        if not matched:
            crud.log_processing_step(
                db, intake_id=intake_id, step="model_validation", status="failed",
                message=("Candidate model does not reproduce this invoice. Add it to the "
                         "model's training set in the workbench and retrain."),
                details={"model_id": candidate.model_id, "suggestion": "workbench_retrain"},
            )
        return invoice, job_id

    # No model yet — run the grounded authoring loop.
    publish_event(intake_id, "model_training", "running", "Authoring extraction model...")
    crud.log_processing_step(
        db, intake_id=intake_id, step="model_training", status="running",
        message="Authoring extraction model...",
        details={"profile_id": profile.profile_id},
    )
    result = author_and_save_profile_model(
        db,
        intake_id=intake_id,
        pdf_path=pdf_path,
        ai_invoice=invoice,
        profile=profile,
        fingerprint=fingerprint,
        layout_family_key=layout_family_key,
        job_id=job_id,
    )
    if result.success and result.model is not None:
        # The authoring loop proved an exact match on this invoice — credit it
        # and write the side-by-side review artifacts.
        _validate_and_credit_model(
            db, intake_id, result.model, result.model_invoice, invoice, profile,
        )
        msg = f"Candidate model authored in {result.attempts} attempt(s)"
        publish_event(intake_id, "model_training", "completed", msg)
        crud.log_processing_step(
            db, intake_id=intake_id, step="model_training", status="completed",
            message=msg,
            details={"profile_id": profile.profile_id, "attempts": result.attempts},
        )
    else:
        msg = "Model authoring did not converge — diff report saved for review"
        publish_event(intake_id, "model_training", "failed", msg)
        crud.log_processing_step(
            db, intake_id=intake_id, step="model_training", status="failed",
            message=msg,
            details={"profile_id": profile.profile_id, "attempts": result.attempts},
        )

    return invoice, job_id


def _training_outcome_failed(db: Session, intake_id: str) -> bool:
    """True when a training upload did not successfully train or validate a model."""
    logs = crud.get_processing_logs(db, intake_id)
    if any(log.step == "model_generation" and log.status == "failed" for log in logs):
        return True
    if any(log.step == "model_validation" and log.status == "failed" for log in logs):
        return True
    training = [log for log in logs if log.step == "model_training"]
    if not training:
        return False
    return training[-1].status in ("failed", "skipped")


# ---------------------------------------------------------------------------
# AI extraction path
# ---------------------------------------------------------------------------


def _extract_invoice_with_optional_artifacts(
    pdf_path: Path,
    *,
    supplier_name: str,
    output_type: str,
    supplier_profile_id: str | None,
    artifact_dir: Path,
):
    """Call extract_invoice with artifact_dir when the callable supports it.

    Some tests and extension points monkeypatch ``extract_invoice`` with the
    older signature. This keeps those callables compatible while production
    uses chunk artifact persistence.
    """
    import inspect

    kwargs = {
        "supplier_name": supplier_name,
        "output_type": output_type,
        "supplier_profile_id": supplier_profile_id,
    }
    try:
        params = inspect.signature(extract_invoice).parameters.values()
        supports_artifacts = any(
            param.name == "artifact_dir" or param.kind == inspect.Parameter.VAR_KEYWORD
            for param in params
        )
    except (TypeError, ValueError):  # pragma: no cover - defensive for exotic callables
        supports_artifacts = True
    if supports_artifacts:
        kwargs["artifact_dir"] = artifact_dir
    return extract_invoice(pdf_path, **kwargs)


def _process_ai_path(
    db: Session,
    intake_id: str,
    pdf_path: Path,
    fingerprint: str,
    page_count: int,
    classification: ClassificationResult | None = None,
    supplier_profile: SupplierProfile | None = None,
) -> tuple[CanonicalInvoice, str, str | None]:
    """AI extraction path (classification + extraction)."""
    crud.log_processing_step(
        db, intake_id=intake_id, step="routing", status="completed",
        message="Using AI extraction",
        details={"extraction_path": "ai"},
    )

    if classification is None:
        classification = _classify_and_log(db, intake_id, pdf_path)

    profile = supplier_profile or find_profile_by_supplier(classification.supplier_name)
    profile_id = profile.profile_id if profile else None
    profile_details = _profile_log_details(profile, source="active_supplier_lookup")

    job = crud.create_extraction_job(
        db,
        intake_id=intake_id,
        extraction_path="ai",
        supplier_profile_id=profile_id,
        supplier_name=classification.supplier_name,
        output_type=classification.output_type,
    )
    crud.update_extraction_job(db, job.id, status="running",
                               started_at=datetime.now())

    from stencil.pricing import estimate_cost_usd

    try:
        extraction_result = _extract_invoice_with_optional_artifacts(
            pdf_path,
            supplier_name=classification.supplier_name,
            output_type=classification.output_type,
            supplier_profile_id=profile_id,
            artifact_dir=settings.processing_dir / intake_id / "ai_chunks",
        )
    except Exception as exc:
        crud.log_ai_call(
            db,
            intake_id=intake_id,
            job_id=job.id,
            call_type="extraction",
            ai_model_name=runtime_settings.openai_model_extraction(),
            status="error",
            error_message=str(exc),
        )
        raise

    if extraction_result.ai_calls:
        _log_compact_ai_calls(db, intake_id, job.id, extraction_result)
    else:
        crud.log_ai_call(
            db,
            intake_id=intake_id,
            job_id=job.id,
            call_type="extraction",
            ai_model_name=extraction_result.ai_model_name,
            tokens_input=extraction_result.tokens_input,
            tokens_output=extraction_result.tokens_output,
            estimated_cost_usd=float(estimate_cost_usd(
                extraction_result.ai_model_name,
                extraction_result.tokens_input,
                extraction_result.tokens_output,
            )),
            duration_ms=extraction_result.duration_ms,
        )

    invoice = build_canonical_invoice(
        raw_data=extraction_result.raw_data,
        intake_id=intake_id,
        extraction_result=extraction_result,
        output_type=classification.output_type,
        supplier_profile_id=profile_id,
        layout_fingerprint=fingerprint,
        page_count=page_count,
    )
    cached_layout = settings.processing_dir / intake_id / "ai_chunks" / "layout.json"
    layout_document = (
        LayoutDocument.model_validate_json(cached_layout.read_text(encoding="utf-8"))
        if cached_layout.exists()
        else extract_layout_document(pdf_path)
    )
    line_item_hints = _effective_line_item_hints(profile)
    apply_layout_profile_hints(
        invoice,
        layout_document,
        profile,
        line_item_hints,
        preserve_existing_line_items=bool(extraction_result.ai_calls),
    )
    _normalize_line_items(
        invoice,
        keep_zero_amount=_keep_zero_line_items(profile),
        require_identifier=_require_identifier(profile),
    )
    output_row_count = len(build_output_rows(invoice, resolve_output_spec(profile)))
    discovery_metadata = (extraction_result.artifacts or {}).get("discovery_plan")
    if discovery_metadata:
        crud.update_intake_scan_metadata(db, intake_id, discovery_metadata)
    if extraction_result.ai_calls:
        _log_compact_ai_merge(
            db,
            intake_id,
            job.id,
            extraction_result,
            canonical_line_item_count=len(invoice.line_items),
            output_row_count=output_row_count,
        )

    crud.update_extraction_job(
        db, job.id,
        status="completed",
        completed_at=datetime.now(),
        ai_model_name=extraction_result.ai_model_name,
        tokens_input=extraction_result.tokens_input,
        tokens_output=extraction_result.tokens_output,
        estimated_cost_usd=invoice.metadata.estimated_cost_usd,
        extraction_duration_ms=extraction_result.duration_ms,
        overall_confidence=invoice.metadata.overall_confidence,
        line_item_count=len(invoice.line_items),
    )
    crud.log_processing_step(
        db,
        intake_id=intake_id,
        step="ai_extraction",
        status="completed",
        message=(
            f"Extracted {len(invoice.line_items)} canonical line items via AI "
            f"({output_row_count} output row(s))"
        ),
        duration_ms=extraction_result.duration_ms,
        details={**profile_details, "canonical_line_item_count": len(invoice.line_items),
                 "output_row_count": output_row_count,
                 "duration_ms": extraction_result.duration_ms},
    )

    # Aggregate total AI cost for this intake: classification + extraction.
    classification_cost = estimate_cost_usd(
        runtime_settings.openai_model_classification(),
        classification.tokens_input,
        classification.tokens_output,
    )
    total_cost = invoice.metadata.estimated_cost_usd + classification_cost
    total_tokens_input = extraction_result.tokens_input + classification.tokens_input
    total_tokens_output = extraction_result.tokens_output + classification.tokens_output
    crud.update_extraction_job(
        db, job.id,
        tokens_input=total_tokens_input,
        tokens_output=total_tokens_output,
        estimated_cost_usd=total_cost,
    )

    logger.info("pipeline.ai_path.completed", intake_id=intake_id,
                supplier=classification.supplier_name,
                items=len(invoice.line_items),
                total_tokens=total_tokens_input + total_tokens_output,
                total_cost_usd=str(total_cost))

    return invoice, job.id, None


def _log_compact_ai_calls(db: Session, intake_id: str, job_id: str, extraction_result) -> None:
    """Persist scalar/chunk AI call records emitted by compact chunked extraction."""
    from stencil.pricing import estimate_cost_usd

    for call in extraction_result.ai_calls or []:
        model_name = call.get("ai_model_name") or extraction_result.ai_model_name
        tokens_input = int(call.get("tokens_input") or 0)
        tokens_output = int(call.get("tokens_output") or 0)
        status = call.get("status") or "success"
        details = call.get("details") or {}
        crud.log_ai_call(
            db,
            intake_id=intake_id,
            job_id=job_id,
            call_type=call.get("call_type") or "extraction",
            ai_model_name=model_name,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            estimated_cost_usd=float(estimate_cost_usd(model_name, tokens_input, tokens_output)),
            duration_ms=int(call.get("duration_ms") or 0),
            status=status,
            error_message=call.get("error_message"),
        )

        if call.get("call_type") == "ai_scalars":
            crud.log_processing_step(
                db,
                intake_id=intake_id,
                job_id=job_id,
                step="ai_scalars",
                status="completed" if status == "success" else "failed",
                message=(
                    "Extracted compact invoice scalar fields"
                    if status == "success"
                    else f"Compact scalar extraction failed: {call.get('error_message')}"
                ),
                duration_ms=int(call.get("duration_ms") or 0),
                details=details,
            )
        elif call.get("call_type") == "ai_line_items_chunk":
            chunk_id = details.get("chunk_id")
            output_items = details.get("output_items", 0)
            crud.log_processing_step(
                db,
                intake_id=intake_id,
                job_id=job_id,
                step="ai_line_items_chunk",
                status="completed" if status == "success" else "failed",
                message=(
                    f"Chunk {chunk_id} extracted {output_items} compact row(s)"
                    if status == "success"
                    else f"Chunk {chunk_id} failed: {call.get('error_message')}"
                ),
                duration_ms=int(call.get("duration_ms") or 0),
                details=details,
            )


def _log_compact_ai_merge(
    db: Session,
    intake_id: str,
    job_id: str,
    extraction_result,
    *,
    canonical_line_item_count: int,
    output_row_count: int,
) -> None:
    artifacts = extraction_result.artifacts or {}
    crud.log_processing_step(
        db,
        intake_id=intake_id,
        job_id=job_id,
        step="ai_merge",
        status="completed",
        message=(
            f"Merged {artifacts.get('compact_row_count', canonical_line_item_count)} compact row(s) "
            f"into {canonical_line_item_count} canonical line item(s)"
        ),
        duration_ms=extraction_result.duration_ms,
        details={
            **artifacts,
            "canonical_line_item_count": canonical_line_item_count,
            "output_row_count": output_row_count,
        },
    )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _model_matches_ai(
    model_invoice: CanonicalInvoice,
    ai_invoice: CanonicalInvoice,
) -> tuple[bool, str]:
    """Return (is_exact_match, reason).

    The model is a valid substitute for AI only if it produces the EXACT same
    delivered output — every Temforce column of every row.
    """
    diff = diff_invoices(ai_invoice, model_invoice)
    return diff.is_match, diff.summary()


def _validate_and_credit_model(
    db: Session,
    intake_id: str,
    model: ExtractionModel,
    model_invoice: CanonicalInvoice | None,
    ai_invoice: CanonicalInvoice,
    profile: SupplierProfile | None,
) -> bool:
    """Compare the model's delivered output against the AI's, record the result,
    and write both outputs for human review. NEVER approves — only a human can
    approve a model. Returns True on an exact match.
    """
    if model_invoice is None:
        record_validation_failure(db, model.model_id, "model produced no output")
        return False

    diff = diff_invoices(ai_invoice, model_invoice, resolve_output_spec(profile))

    # Always surface both outputs side-by-side for review.
    _write_model_review_artifacts(db, intake_id, model, ai_invoice, model_invoice, profile)

    if diff.is_match:
        record = record_validation_success(db, model.model_id, intake_id)
        successes = record.validation_success_count if record else None
        crud.log_processing_step(
            db, intake_id=intake_id, step="model_validation", status="passed",
            message=(
                f"Model output is a 100% match to AI on this invoice "
                f"({successes} successful invoice(s) so far). A human can approve the model "
                f"once it has proven out across enough invoices."
            ),
            details={"model_id": model.model_id,
                     "validation_success_count": successes,
                     "auto_approve": False},
        )
    else:
        record = record_validation_failure(
            db, model.model_id, f"model output != AI output: {diff.summary()}"
        )
        failures = record.validation_failure_count if record else None
        crud.log_processing_step(
            db, intake_id=intake_id, step="model_validation", status="failed",
            message=f"Model output does NOT exactly match AI output — not credited: {diff.summary()}",
            details={"model_id": model.model_id,
                     "validation_failure_count": failures,
                     "diff": diff.to_report()},
        )
    return diff.is_match


def _write_model_review_artifacts(
    db: Session,
    intake_id: str,
    model: ExtractionModel,
    ai_invoice: CanonicalInvoice,
    model_invoice: CanonicalInvoice,
    profile: SupplierProfile | None,
) -> None:
    """Write the AI and model outputs side-by-side for human review.

    Best-effort: never breaks the pipeline. Files land in
    completed/{intake_id}/model_review/ and are served by the
    GET /invoices/{intake_id}/model-review/{filename} API.
    """
    try:
        written = write_model_review_artifacts(
            intake_id, ai_invoice, model_invoice, spec=resolve_output_spec(profile),
        )
        if not written:
            return
        crud.log_processing_step(
            db, intake_id=intake_id, step="model_review", status="completed",
            message=f"Model output written for review: {model.model_id}",
            details={
                "model_id": model.model_id,
                "files": [f"model_review/{name}" for name in written],
            },
        )
    except Exception as e:
        logger.warning("pipeline.model_review_write_failed", intake_id=intake_id, error=str(e))


def _validate_model_output_for_reuse(invoice: ExtractedDocument) -> tuple[bool, str]:
    # No confidence gate here on purpose. Interpreter confidence is derived from
    # exactly two conditions -- a missing invoice number and an empty item list --
    # and both are re-checked below with a message that names the actual cause.
    # A threshold over that number could only ever restate them, less clearly.
    if not invoice.header.invoice_number or invoice.header.invoice_number == "UNKNOWN":
        return False, "missing invoice number"
    if not invoice.line_items:
        return False, "no line items extracted"
    if all(item.amount == 0 for item in invoice.line_items):
        return False, "all extracted line items are zero amount"

    recon = reconcile(invoice, default_field_schema())
    if recon is not None and not recon.is_reconciled:
        return False, f"reconciliation failed: variance {recon.variance_pct:.4%}"
    return True, "validated"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _has_identifier(item) -> bool:
    """True when a line item carries a usable service_id or billing_reference
    (at least one alphanumeric character — a stray ',' or blank doesn't count)."""
    for value in (item.service_id, item.billing_reference):
        if value and re.search(r"[A-Za-z0-9]", str(value)):
            return True
    return False


def _flag_rows_missing_service_id(db: Session, intake_id: str, invoice, pdf_path: Path) -> None:
    """Route the invoice to Exceptions when a delivered row has no service id.

    The row is still delivered — dropping it would silently change row counts and
    totals, which is precisely how missing data used to go unnoticed. An operator
    sees the exception and decides.
    """
    blank = [
        item.line_number
        for item in invoice.line_items
        if item.charge_type not in {ChargeType.TAX, ChargeType.FEE, ChargeType.SURCHARGE}
        and not (item.service_id and re.search(r"[A-Za-z0-9]", str(item.service_id)))
    ]
    if not blank:
        return

    preview = ", ".join(str(n) for n in blank[:10])
    message = (
        f"{len(blank)} delivered row(s) have no service id (line numbers: {preview}"
        f"{', …' if len(blank) > 10 else ''})"
    )
    invoice.warnings.append(message)
    crud.log_processing_step(
        db, intake_id=intake_id, step="extraction", status="completed_with_warnings",
        message=message, details={"line_numbers": blank[:50]},
    )
    try:
        create_exception_package(
            intake_id=intake_id,
            reason=ExceptionReason.MISSING_SERVICE_ID,
            message=message,
            pdf_path=pdf_path,
            partial_invoice=invoice,
        )
    except Exception as exc:  # pragma: no cover - best effort, mirrors _flag_layout_drift
        logger.warning("pipeline.missing_service_id_exception_failed", intake_id=intake_id, error=str(exc))


def _normalize_line_items(
    invoice: ExtractedDocument,
    keep_zero_amount: bool = False,
    require_identifier: bool = False,
) -> ExtractedDocument:
    """Resequence line numbers; optionally drop zero-amount and/or no-identifier
    rows, in place."""
    kept = list(invoice.line_items)
    if not keep_zero_amount:
        kept = [item for item in kept if item.amount != 0]
    if require_identifier:
        kept = [item for item in kept if _has_identifier(item)]
    rows: list[dict] = []
    for new_number, item in enumerate(kept, start=1):
        row = item.model_dump(mode="python")
        row["line_number"] = new_number
        rows.append(row)
    invoice.rows = rows
    return invoice


def _effective_line_item_hints(profile: SupplierProfile | None) -> LineItemHints | None:
    return profile.line_item_hints if profile else None


def _keep_zero_line_items(profile: SupplierProfile | None) -> bool:
    """Whether the supplier profile opts to keep zero-amount line items."""
    return bool(profile and getattr(profile, "include_zero_amount_line_items", False))


def _require_identifier(profile: SupplierProfile | None) -> bool:
    """Whether the supplier profile drops line items that have no identifier."""
    return bool(profile and getattr(profile, "require_line_item_identifier", False))


def _classify_and_log(db: Session, intake_id: str, pdf_path: Path) -> ClassificationResult:
    from stencil.pricing import estimate_cost_usd

    try:
        classification = classify_invoice(pdf_path)
    except Exception as exc:
        crud.log_ai_call(
            db,
            intake_id=intake_id,
            call_type="classification",
            ai_model_name=runtime_settings.openai_model_classification(),
            status="error",
            error_message=str(exc),
        )
        raise

    crud.log_ai_call(
        db,
        intake_id=intake_id,
        call_type="classification",
        ai_model_name=runtime_settings.openai_model_classification(),
        tokens_input=classification.tokens_input,
        tokens_output=classification.tokens_output,
        estimated_cost_usd=float(estimate_cost_usd(
            runtime_settings.openai_model_classification(),
            classification.tokens_input,
            classification.tokens_output,
        )),
        duration_ms=classification.duration_ms,
    )

    profile = find_profile_by_supplier(classification.supplier_name)
    crud.log_processing_step(
        db, intake_id=intake_id, step="classification", status="completed",
        message=f"Classified as {classification.supplier_name} / {classification.output_type}",
        duration_ms=classification.duration_ms,
        details={
            "confidence": classification.confidence,
            "duration_ms": classification.duration_ms,
            **_profile_log_details(profile, source="active_supplier_lookup"),
        },
    )

    if classification.confidence < float(runtime_settings.runtime_value("confidence_threshold")):
        crud.log_processing_step(
            db, intake_id=intake_id, step="classification", status="low_confidence",
            message=f"Classification confidence {classification.confidence} below threshold",
        )

    return classification


def _resolve_output_dir(profile: SupplierProfile | None, account_label: str | None = None) -> Path | None:
    """Return the output directory for this account (one profile serves many),
    falling back to the profile's legacy single output_path."""
    if not profile:
        return None
    output_path = profile.output_path_for_account(account_label)
    if not output_path:
        return None
    return Path(output_path).expanduser()


def _profile_delivery_blockers(profile: SupplierProfile | None) -> list[str]:
    """Authoring evidence is advisory and never pauses production delivery."""
    return []


def _unverified_large_document_blockers(
    invoice: ExtractedDocument, recon: ReconciliationResult | None,
) -> list[str]:
    """Stop an unchecked large document before it reaches the customer.

    A six-page invoice that does not add up gets eyeballed by whoever opens it.
    A 656-page one does not -- nobody scans 3,400 rows looking for the row that
    went missing, so an unverified deliverable is worse than a missing one.

    Only an actual failed check blocks. A document that *cannot* be verified
    (no reconcilable totals in its schema) is let through: that is the normal
    state for a document type with no arithmetic, and blocking it would make
    the gate unusable the moment Stencil handles something other than invoices.

    The completed/ package is written either way, so nothing is lost -- this
    gates only the copy into the supplier's delivery folder.
    """
    threshold = settings.blocking_check_page_threshold
    pages = invoice.metadata.total_pages
    if threshold <= 0 or pages < threshold or recon is None or recon.is_reconciled:
        return []
    return [
        f"{pages}-page document failed its arithmetic check "
        f"(variance {recon.variance_pct:.2%}); too large to verify by hand"
    ]


def _deliver_without_clobbering(source: Path, dest_dir: Path) -> Path:
    """Copy a deliverable into a supplier folder without ever overwriting a
    different invoice's file.

    Same content -> atomic replace, so reprocessing the same PDF is idempotent.
    Different content under the same name -> a numbered sibling plus a warning,
    because two distinct invoices sharing a filename is a real ambiguity that
    must be visible rather than resolved by whichever ran last.
    """
    import hashlib
    import os
    import shutil

    def _digest(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()

    target = dest_dir / source.name
    if target.exists() and _digest(target) != _digest(source):
        stem, suffix = target.stem, target.suffix
        counter = 2
        while (candidate := dest_dir / f"{stem} ({counter}){suffix}").exists():
            if _digest(candidate) == _digest(source):
                target = candidate
                break
            counter += 1
        else:
            target = candidate
        logger.warning(
            "pipeline.output.name_collision",
            source=source.name, delivered_as=target.name, directory=str(dest_dir),
        )

    # Stage beside the target so the rename is atomic on the same filesystem;
    # a half-written deliverable must never look like a finished one.
    staging = dest_dir / f".{target.name}.partial"
    try:
        shutil.copy2(source, staging)
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)
    return target


def _generate_output(db: Session, intake_id: str,
                     invoice: CanonicalInvoice,
                     custom_output_dir: Path | None = None,
                     profile: SupplierProfile | None = None,
                     account_label: str | None = None,
                     duration_ms: int | None = None) -> tuple[Path, Path, Path, Path]:
    """Generate XLSX, canonical JSON, extraction log, and manifest."""
    # Always write to the standard completed directory
    output_dir = settings.completed_dir / intake_id
    output_dir.mkdir(parents=True, exist_ok=True)

    intake = crud.get_intake(db, intake_id)
    original_filename = intake.original_filename if intake else "invoice_output.pdf"
    # Name from the source PDF, per the delivery contract in README.md ("stem
    # matches the source PDF").  Naming by account_label meant every invoice for
    # an account produced the same filename and silently overwrote the last --
    # account 82824706 had 10 distinct invoices all delivered as 82824706.xlsx.
    xlsx_name = derive_output_xlsx_name(original_filename)
    xlsx_path = write_xlsx(
        invoice, output_dir / xlsx_name, resolve_output_spec(profile),
    )
    json_path = write_canonical_json(invoice, output_dir / "canonical_invoice.json")
    log_path = write_extraction_log(invoice, output_dir / "extraction_log.json")

    # Manifest written LAST — this is the ready signal
    manifest_path = write_manifest(intake_id, output_dir, invoice,
                                   xlsx_path, json_path, log_path)

    crud.log_processing_step(
        db, intake_id=intake_id, step="output", status="completed",
        message=f"Output package written to {output_dir}",
        duration_ms=duration_ms,
        details={"files": [xlsx_name, "canonical_invoice.json",
                           "extraction_log.json", "manifest.json"],
                 "duration_ms": duration_ms,
                 **_profile_log_details(profile, source="output_profile")},
    )

    # Copy output to custom supplier-specific directory if configured
    if custom_output_dir:
        try:
            custom_output_dir.mkdir(parents=True, exist_ok=True)
            copied = _deliver_without_clobbering(xlsx_path, custom_output_dir)
            crud.log_processing_step(
                db, intake_id=intake_id, step="output_copy", status="completed",
                message=f"Output copied to supplier directory: {custom_output_dir}",
                details={
                    "files": [copied.name],
                    **({"renamed_from": xlsx_path.name} if copied.name != xlsx_path.name else {}),
                    **_profile_log_details(profile, source="output_profile"),
                },
            )
            logger.info("pipeline.output.copied", intake_id=intake_id,
                        custom_dir=str(custom_output_dir), filename=copied.name)
        except Exception as e:
            logger.warning("pipeline.output.copy_failed", intake_id=intake_id,
                           custom_dir=str(custom_output_dir), error=str(e))
            crud.log_processing_step(
                db, intake_id=intake_id, step="output_copy", status="failed",
                message=f"Failed to copy to supplier directory: {e}",
            )
    elif profile:
        crud.log_processing_step(
            db, intake_id=intake_id, step="output_copy", status="failed",
            message="Supplier profile has no output_path; output package was not copied",
            details=_profile_log_details(profile, source="output_profile"),
        )

    logger.info("pipeline.output.completed", intake_id=intake_id,
                output_dir=str(output_dir))

    return xlsx_path, json_path, log_path, manifest_path
