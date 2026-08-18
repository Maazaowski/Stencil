"""Celery worker tasks for async invoice processing."""

from datetime import UTC, datetime
from pathlib import Path

import structlog
from celery import Celery
from celery.signals import worker_ready

from stencil import runtime_settings
from stencil.config import settings

logger = structlog.get_logger()

app = Celery("stencil", broker=settings.redis_url)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)


@worker_ready.connect
def _sweep_orphaned_training_runs(**_kwargs):
    """A worker restart kills any in-flight Train task — finalize orphaned runs
    so the workbench stops polling and re-enables the Train button."""
    from stencil.db import crud
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        swept = crud.fail_stale_training_runs(db)
        if swept:
            logger.info("worker.swept_orphaned_training_runs", count=swept)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("worker.sweep_failed", error=str(exc))
    finally:
        db.close()


@app.task(bind=True, max_retries=2, default_retry_delay=30)
def process_invoice_task(self, pdf_path: str):
    """Async task: process a single invoice PDF through the full pipeline."""
    from stencil.db import crud
    from stencil.db.session import SessionLocal
    from stencil.intake.service import process_new_pdf
    from stencil.pipeline.events import publish_event
    from stencil.pipeline.processor import NonRetryablePipelineError, continue_pipeline
    from stencil.profiles.loader import load_all_profiles

    db = SessionLocal()
    intake_id = None
    try:
        load_all_profiles()
        intake_id = process_new_pdf(db, Path(pdf_path), intake_source="watcher")
        crud.set_intake_celery_task(db, intake_id, self.request.id)
        publish_event(intake_id, "intake", "completed", "PDF registered and archived")
        continue_pipeline(db, intake_id)
        return {"status": "completed", "intake_id": intake_id}
    except NonRetryablePipelineError as exc:
        db.rollback()
        return {"status": "failed", "intake_id": intake_id, "error": str(exc)}
    except Exception as exc:
        db.rollback()
        raise self.retry(exc=exc)
    finally:
        db.close()


@app.task(bind=True, max_retries=2, default_retry_delay=30)
def continue_pipeline_task(self, intake_id: str, profile_id: str | None = None):
    """Async task: continue processing an existing intake record.

    If profile_id is provided (from the multi-directory watcher), classification
    is skipped — the supplier is already known from the watched directory.
    """
    from stencil.db import crud
    from stencil.db.session import SessionLocal
    from stencil.pipeline.processor import NonRetryablePipelineError, continue_pipeline
    from stencil.profiles.loader import load_all_profiles

    db = SessionLocal()
    try:
        load_all_profiles()
        crud.set_intake_celery_task(db, intake_id, self.request.id)
        continue_pipeline(db, intake_id, profile_id=profile_id)
        return {"status": "completed", "intake_id": intake_id}
    except NonRetryablePipelineError as exc:
        db.rollback()
        return {"status": "failed", "intake_id": intake_id, "error": str(exc)}
    except Exception as exc:
        db.rollback()
        raise self.retry(exc=exc)
    finally:
        db.close()


@app.task(bind=True, max_retries=2, default_retry_delay=30)
def process_invoice_for_profile_task(self, pdf_path: str, profile_id: str, account_label: str | None = None):
    """Async task: process a PDF from an active supplier profile's watched directory.

    ``account_label`` identifies which billing account's folder the PDF arrived in
    (one profile can serve many accounts) and routes the delivered output."""
    from stencil.db import crud
    from stencil.db.session import SessionLocal
    from stencil.intake.service import process_new_pdf
    from stencil.pipeline.events import publish_event
    from stencil.pipeline.processor import NonRetryablePipelineError, continue_pipeline
    from stencil.profiles.loader import load_all_profiles

    db = SessionLocal()
    intake_id = None
    try:
        load_all_profiles()
        intake_id = process_new_pdf(
            db, Path(pdf_path), intake_source="watcher", supplier_profile_id=profile_id,
            account_label=account_label,
        )
        crud.set_intake_celery_task(db, intake_id, self.request.id)
        publish_event(intake_id, "intake", "completed", "PDF registered and archived")
        continue_pipeline(db, intake_id, profile_id=profile_id)
        return {"status": "completed", "intake_id": intake_id, "profile_id": profile_id}
    except NonRetryablePipelineError as exc:
        db.rollback()
        return {"status": "failed", "intake_id": intake_id, "profile_id": profile_id, "error": str(exc)}
    except Exception as exc:
        db.rollback()
        raise self.retry(exc=exc)
    finally:
        db.close()


@app.task(bind=True, max_retries=0)
def train_model_task(self, model_id: str, intake_ids: list):
    """Async task: (re)author a model from an explicit training set.

    Used by the model UI's 'Add training invoices' / 'Retrain' actions. Loads
    each invoice's PDF + stored AI ground truth from disk, authors one model
    from the whole set, and saves it only if it reproduces every member.
    """
    from stencil.db import crud
    from stencil.db.session import SessionLocal
    from stencil.fingerprint.fingerprinter import compute_layout_family_key, fingerprint_pdf
    from stencil.models.schema import ExtractionModel
    from stencil.models.training import compute_training_step_budget, train_model_on_set
    from stencil.profiles.loader import find_profile_by_supplier, get_profile, load_all_profiles

    db = SessionLocal()
    run = None
    try:
        load_all_profiles()
        record = crud.get_extraction_model(db, model_id)
        if record is None:
            return {"status": "failed", "model_id": model_id, "error": "model not found"}
        model = ExtractionModel.model_validate(record.model_json)
        profile = (get_profile(record.supplier_profile_id) if record.supplier_profile_id else None) \
            or find_profile_by_supplier(record.supplier, active_only=False)
        if profile is None:
            return {"status": "failed", "model_id": model_id, "error": "owning profile not found"}

        fingerprint = model.layout_fingerprint
        layout_family_key = model.layout_family_key
        if not fingerprint and intake_ids:
            first_pdf = settings.archive_dir / intake_ids[0] / "original.pdf"
            if first_pdf.exists():
                fingerprint, features = fingerprint_pdf(first_pdf, profile=profile)
                layout_family_key = compute_layout_family_key(
                    features, supplier=profile.identity.canonical_name,
                    output_type=profile.classification.output_type,
                )

        # Carry the holdout set forward across re-authoring (authoring never
        # sees these; they are the generalization test for the approval gate).
        holdout_intake_ids = [i for i in (model.holdout_intake_ids or []) if i not in set(intake_ids)]

        total_steps = compute_training_step_budget(
            db, profile, list(intake_ids), holdout_intake_ids,
        )

        # A model-training run is its OWN pipeline (separate from invoice
        # processing) so the workbench can show live progress by polling.
        run = crud.create_training_run(
            db, model_id=model_id, supplier_profile_id=record.supplier_profile_id,
            total_steps=total_steps,
            message="Training started",
        )

        from copy import deepcopy

        from stencil.pricing import estimate_cost_usd

        # progress_detail keeps an authoritative per-invoice map (train+holdout)
        # that the workbench renders. acc holds cumulative EXTRACTION tokens so
        # cost climbs live during the (paid) extraction phase.
        progress_detail: dict = {"invoices": {}}
        acc = {"ti": 0, "to": 0}

        def on_progress(step, done, total, message, detail):
            detail = dict(detail or {})
            ti = detail.pop("tokens_input", None)
            to = detail.pop("tokens_output", None)
            if "invoices" in detail:
                progress_detail["invoices"] = detail.pop("invoices")
            upd = detail.pop("invoice_update", None)
            if upd and upd.get("intake_id"):
                inv_map = progress_detail.setdefault("invoices", {})
                # Merge (not replace) so a state-only update from validation keeps
                # the filename/role seeded during extraction.
                entry = dict(inv_map.get(upd["intake_id"]) or {})
                entry.update({k: v for k, v in upd.items() if k != "intake_id"})
                inv_map[upd["intake_id"]] = entry
            progress_detail.update(detail)
            fields: dict = dict(
                step=step, completed_steps=done, total_steps=total,
                message=message, detail=deepcopy(progress_detail),
            )
            if ti is not None or to is not None:
                acc["ti"], acc["to"] = int(ti or 0), int(to or 0)
                fields["tokens_input"] = acc["ti"]
                fields["tokens_output"] = acc["to"]
                fields["estimated_cost_usd"] = float(estimate_cost_usd(
                    runtime_settings.openai_model_model_generation(), acc["ti"], acc["to"]))
            # Progress writes use their own session so parallel extraction threads
            # can commit intake updates without breaking this session's commits.
            progress_db = SessionLocal()
            try:
                crud.update_training_run(progress_db, run.id, **fields)
            except Exception:
                progress_db.rollback()
                raise
            finally:
                progress_db.close()

        result = train_model_on_set(
            db, profile=profile, fingerprint=fingerprint, layout_family_key=layout_family_key,
            intake_ids=list(intake_ids), log_intake_id=(intake_ids[0] if intake_ids else None),
            holdout_intake_ids=holdout_intake_ids, on_progress=on_progress,
        )

        from datetime import datetime

        # Total training cost = extraction (acc) + authoring (result.tokens_*).
        final_ti = acc["ti"] + result.tokens_input
        final_to = acc["to"] + result.tokens_output
        crud.update_training_run(
            db, run.id,
            state="success" if result.success else "failed",
            step="done", finished_at=datetime.now(UTC),
            tokens_input=final_ti, tokens_output=final_to,
            estimated_cost_usd=float(estimate_cost_usd(
                runtime_settings.openai_model_model_generation(), final_ti, final_to)),
            error_message=None if result.success else "; ".join(result.errors[:3]) or None,
            detail={**progress_detail,
                    "per_invoice": result.per_invoice_detail or result.per_invoice,
                    "attempts": result.attempts, "warnings": result.warnings[:5],
                    "errors": result.errors[:5],
                    "training_intake_ids": result.training_intake_ids},
            message=(
                "Model trained and saved as candidate" if result.success
                else (
                    "Training did not converge - last attempted rules saved for editing"
                    if result.model_id
                    else "Training stopped before authoring - fix the listed invoice issue and retry"
                )
            ),
        )
        return {"status": "completed" if result.success else "failed",
                "model_id": model_id, "run_id": run.id,
                "training_intake_ids": result.training_intake_ids,
                "errors": result.errors[:3]}
    except Exception as exc:
        db.rollback()
        # Never leave the run stuck "running" — that would make the workbench
        # poll forever and keep the Train button disabled.
        if run is not None:
            try:
                from datetime import datetime
                crud.update_training_run(
                    db, run.id, state="failed", step="done",
                    finished_at=datetime.now(UTC),
                    message="Training crashed", error_message=str(exc),
                )
            except Exception:
                db.rollback()
        return {"status": "error", "model_id": model_id, "error": str(exc)}
    finally:
        db.close()


@app.task(bind=True, max_retries=0)
def preview_profile_task(self, job_id: str, profile_id: str, pdf_path: str):
    """Async task: AI-extract a sample PDF under a profile and write its preview
    payload to a result file the API polls. Runs in the worker (not the request
    handler) because the OpenAI extraction can take minutes — far longer than any
    HTTP proxy will hold a connection open."""
    import json

    from stencil.extraction.extractor import build_extracted_document, extract_invoice
    from stencil.fields.loader import resolve_merged_field_schema
    from stencil.output.preview import preview_for_profile
    from stencil.profiles.loader import get_profile, load_all_profiles

    previews_dir = settings.work_dir / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    result_path = previews_dir / f"{job_id}.json"
    pdf = Path(pdf_path)

    def _write(payload: dict) -> None:
        result_path.write_text(json.dumps(payload), encoding="utf-8")

    try:
        load_all_profiles()
        profile = get_profile(profile_id)
        if profile is None:
            _write({"status": "error", "detail": "Profile not found"})
            return {"status": "error", "job_id": job_id}
        output_type = profile.classification.output_type
        result = extract_invoice(pdf, supplier_profile_id=profile_id, output_type=output_type)
        schema = resolve_merged_field_schema(profile)
        doc = build_extracted_document(
            result.raw_data, intake_id="preview", extraction_result=result,
            output_type=output_type, supplier_profile_id=profile_id, field_schema=schema,
        )
        payload = preview_for_profile(doc, profile, extraction_path="ai")
        _write({"status": "done", "preview": payload})
        return {"status": "done", "job_id": job_id}
    except Exception as exc:
        logger.error("profile.preview.task_failed", job_id=job_id, error=str(exc))
        try:
            _write({"status": "error", "detail": str(exc)})
        except Exception:  # pragma: no cover - best effort
            pass
        return {"status": "error", "job_id": job_id, "error": str(exc)}
    finally:
        pdf.unlink(missing_ok=True)


def _run_authoring_queue(db, session_id: str) -> dict:
    from stencil.db import crud
    from stencil.profiles.authoring_runtime import run_discovery, run_reextraction, run_turn

    if not crud.try_start_authoring_queue(db, session_id):
        return {"status": "busy", "session_id": session_id}

    processed = 0
    try:
        while True:
            job = crud.get_next_queued_authoring_job(db, session_id)
            if job is None:
                session = crud.get_authoring_session(db, session_id)
                if session is not None and session.status == "running":
                    crud.update_authoring_session(db, session_id, status="active")
                # Close the race where a new job is queued while the runner is
                # ending. If that happened, this runner immediately resumes it.
                if crud.get_next_queued_authoring_job(db, session_id) is not None:
                    if crud.try_start_authoring_queue(db, session_id):
                        continue
                return {"status": "done", "session_id": session_id, "processed": processed}

            crud.update_authoring_job(db, job.id, status="running", started_at=datetime.now())
            try:
                if job.kind == "turn":
                    payload = run_turn(db, session_id=session_id, user_message=job.message or "")
                elif job.kind == "reextract":
                    payload = run_reextraction(db, session_id=session_id, invoice_id=job.invoice_id)
                elif job.kind == "discover":
                    payload = {"discovery": run_discovery(db, session_id=session_id)}
                else:
                    raise ValueError(f"unknown authoring job kind: {job.kind}")
                # The API may have cancelled this task while the provider response
                # was crossing the wire. Do not let a late result resurrect it.
                db.expire_all()
                current_job = crud.get_authoring_job(db, job.id)
                if current_job is not None and current_job.status != "cancelled":
                    crud.update_authoring_job(
                        db, job.id,
                        status="done",
                        result_payload=payload,
                        error_message=None,
                        finished_at=datetime.now(),
                    )
            except Exception as exc:
                db.rollback()
                db.expire_all()
                current_job = crud.get_authoring_job(db, job.id)
                if current_job is not None and current_job.status != "cancelled":
                    crud.update_authoring_job(
                        db, job.id,
                        status="error",
                        error_message=str(exc),
                        finished_at=datetime.now(),
                    )
            processed += 1
    except Exception:
        db.rollback()
        session = crud.get_authoring_session(db, session_id)
        if session is not None and session.status == "running":
            crud.update_authoring_session(db, session_id, status="active")
        raise


@app.task(bind=True, max_retries=0)
def authoring_turn_task(self, session_id: str, job_id: str, user_message: str):
    """Async task: run one profile-authoring chat turn (author + preview) and write
    the result to a poll file the API serves. Mirrors ``preview_profile_task``."""
    from stencil.db.session import SessionLocal
    from stencil.profiles.loader import load_all_profiles

    db = SessionLocal()
    try:
        load_all_profiles()
        return {**_run_authoring_queue(db, session_id), "job_id": job_id}
    except Exception as exc:
        logger.exception("authoring.turn.task_failed", session_id=session_id, job_id=job_id, error=str(exc))
        return {"status": "error", "job_id": job_id, "error": str(exc)}
    finally:
        db.close()


@app.task(bind=True, max_retries=0)
def authoring_reextract_task(self, session_id: str, job_id: str, invoice_id: str | None = None):
    """Async task: re-run AI extraction on the cached samples with the current draft
    profile, then re-render previews. Writes to the same poll file as a turn."""
    from stencil.db.session import SessionLocal
    from stencil.profiles.loader import load_all_profiles

    db = SessionLocal()
    try:
        load_all_profiles()
        return {**_run_authoring_queue(db, session_id), "job_id": job_id}
    except Exception as exc:
        logger.exception("authoring.reextract.task_failed", session_id=session_id, job_id=job_id, error=str(exc))
        return {"status": "error", "job_id": job_id, "error": str(exc)}
    finally:
        db.close()


@app.task(bind=True, max_retries=1, default_retry_delay=30)
def train_profile_model_task(self, intake_id: str, profile_id: str):
    """Async task: run the training pipeline for an explicit training upload.

    AI extracts the invoice (ground truth) and the grounded authoring loop
    authors/validates the profile's extraction model.
    """
    from stencil.db import crud
    from stencil.db.session import SessionLocal
    from stencil.pipeline.processor import NonRetryablePipelineError, continue_pipeline
    from stencil.profiles.loader import load_all_profiles

    db = SessionLocal()
    try:
        load_all_profiles()
        crud.set_intake_celery_task(db, intake_id, self.request.id)
        continue_pipeline(db, intake_id, profile_id=profile_id, mode="training")
        return {"status": "completed", "intake_id": intake_id, "profile_id": profile_id}
    except NonRetryablePipelineError as exc:
        db.rollback()
        return {"status": "failed", "intake_id": intake_id, "profile_id": profile_id, "error": str(exc)}
    except Exception as exc:
        db.rollback()
        raise self.retry(exc=exc)
    finally:
        db.close()


@app.task(bind=True, max_retries=0)
def accounts_sync_task(self):
    """Scan the invoices tree and persist the accounts snapshot, streaming progress
    to work_dir/accounts/sync_status.json. Runs in the worker (not a request) because
    the walk over the bind mount takes ~a minute for thousands of accounts."""
    from stencil.accounts.sync import run_sync

    try:
        run_sync()
        return {"status": "done"}
    except Exception as exc:
        logger.error("accounts.sync_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}


@app.task(bind=True, max_retries=0)
def eval_run_task(
    self,
    run_id: str,
    call_types: list,
    label: str,
    case_ids: list | None = None,
    concurrency: int = 1,
    run_kind: str = "current",
    baseline_run_id: str | None = None,
):
    """Run a prompt-evaluation suite (live AI) over the labeled cases and persist
    the scored artifacts under work_dir/evals/<run_id>/."""
    from stencil.evals.report import run_suite, write_status
    from stencil.profiles.loader import load_all_profiles

    try:
        load_all_profiles()
        run_suite(
            run_id,
            call_types=list(call_types),
            label=label,
            case_ids=case_ids,
            concurrency=concurrency,
            run_kind=run_kind,
            baseline_run_id=baseline_run_id,
        )
        return {"status": "done", "run_id": run_id}
    except Exception as exc:
        logger.error("eval.run_failed", run_id=run_id, error=str(exc))
        try:
            write_status(run_id, {"status": "error", "detail": str(exc)})
        except Exception:  # pragma: no cover - best effort
            pass
        return {"status": "error", "run_id": run_id, "error": str(exc)}
