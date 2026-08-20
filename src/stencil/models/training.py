"""Model training service — corpus-grounded authoring of one declarative model.

AI extraction is the ground truth. A model is authored from a SET of
same-fingerprint invoices (one AI authoring call) and must reproduce EVERY
invoice in the set before it is saved as a candidate — so the rules generalize
instead of overfitting a single invoice's row count/fields. The set grows as new
invoices reveal new row shapes; re-authoring never saves a model that fails any
member, so the candidate never regresses.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy.orm import Session, sessionmaker

from stencil import runtime_settings
from stencil.config import settings
from stencil.db import crud
from stencil.extraction.evidence import build_model_authoring_evidence
from stencil.extraction.layout import LayoutDocument, extract_layout_document, render_layout_text
from stencil.models.authoring import AuthoringExample, author_extraction_model
from stencil.models.diff import OutputDiff, diff_invoices
from stencil.models.interpreter import ModelExecutionError, execute_model
from stencil.models.registry import save_failed_model, save_model
from stencil.models.schema import ExtractionModel
from stencil.output.review_artifacts import write_model_review_artifacts
from stencil.profiles.schema import SupplierProfile
from stencil.specs.loader import resolve_output_spec
from stencil.validation.schema import CanonicalInvoice

logger = structlog.get_logger()

# Sentinel: a training sample is staged but has no ground truth yet (not an
# error). The gate reports it as pending instead of extracting on the spot.
_PENDING = "__pending__"


@dataclass
class TrainingExample:
    """One invoice in a training set: its layout document + AI ground truth."""

    intake_id: str
    ai_invoice: CanonicalInvoice
    document: LayoutDocument
    pdf_path: Path | None = None
    page_texts: list[str] = field(default_factory=list)
    layout_evidence: dict = field(default_factory=dict)
    #: 1-based page numbers, most informative first. Used only when the prompt
    #: must be trimmed, to decide which pages survive.
    page_priority: list[int] = field(default_factory=list)

    def prepared(self) -> TrainingExample:
        if not self.page_texts:
            self.page_texts = render_layout_text(self.document)
        if not self.page_priority:
            from stencil.extraction.page_roles import classify_pages

            self.page_priority = classify_pages(self.document).ranked_pages()
        if not self.layout_evidence:
            self.layout_evidence = build_model_authoring_evidence(
                self.document, self.ai_invoice, max_rows=settings.model_authoring_sample_rows,
            )
        return self


@dataclass
class TrainingResult:
    success: bool
    model: ExtractionModel | None = None
    model_id: str | None = None
    model_invoice: CanonicalInvoice | None = None
    attempts: int = 0
    diff: OutputDiff | None = None
    diff_report_path: str | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    training_intake_ids: list[str] = field(default_factory=list)
    per_invoice: dict[str, bool] = field(default_factory=dict)
    # Richer per-invoice reproduction info for the workbench (read back from the
    # run record so the UI never re-executes the model): intake_id -> {ok, reason,
    # expected_lines, actual_lines}.
    per_invoice_detail: dict[str, dict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def author_and_save_profile_model(
    db: Session,
    *,
    intake_id: str,
    pdf_path: Path | None,
    ai_invoice: CanonicalInvoice,
    profile: SupplierProfile,
    fingerprint: str,
    layout_family_key: str | None,
    job_id: str | None = None,
    document: LayoutDocument | None = None,
) -> TrainingResult:
    """Author a model from a single invoice (first-invoice path).

    Thin wrapper over the corpus-grounded core: the primary example is the
    invoice already extracted in this run, so no disk reload is needed.
    """
    if document is None:
        document = extract_layout_document(pdf_path)
    primary = TrainingExample(
        intake_id=intake_id, ai_invoice=ai_invoice, document=document, pdf_path=pdf_path,
    ).prepared()
    return _train(
        db, profile=profile, fingerprint=fingerprint, layout_family_key=layout_family_key,
        primary=primary, extras=[], job_id=job_id, log_intake_id=intake_id,
    )


def compute_training_step_budget(
    db: Session,
    profile: SupplierProfile,
    intake_ids: list[str],
    holdout_intake_ids: list[str] | None = None,
) -> int:
    """Step count for the model-training progress bar (must match train_model_on_set)."""
    holdout_ids = [i for i in (holdout_intake_ids or []) if i and i not in set(intake_ids)]
    capped = _cap_training_set(db, profile, intake_ids)
    to_load = list(dict.fromkeys(capped + holdout_ids))
    return len(to_load) + 1 + len(capped) + len(holdout_ids) + 1


def train_model_on_set(
    db: Session,
    *,
    profile: SupplierProfile,
    fingerprint: str,
    layout_family_key: str | None,
    intake_ids: list[str],
    job_id: str | None = None,
    log_intake_id: str | None = None,
    preloaded: list[TrainingExample] | None = None,
    holdout_intake_ids: list[str] | None = None,
    on_progress=None,
) -> TrainingResult:
    """Author one model from a SET of same-fingerprint invoices.

    Loads each invoice's PDF + AI ground truth from disk (re-extracting only
    when ground truth is absent), authors a single model from the richest one
    plus the rest as additional ground truths, and saves it only if it
    reproduces EVERY invoice in the set. ``preloaded`` supplies in-memory
    examples (e.g. the invoice just extracted in this pipeline run) so they are
    not reloaded/re-extracted from disk. ``on_progress(step, done, total, msg,
    detail)`` reports model-training-pipeline progress for the workbench.
    """
    holdout_ids = [i for i in (holdout_intake_ids or []) if i and i not in set(intake_ids)]
    preloaded_by_id = {ex.intake_id: ex for ex in (preloaded or [])}
    capped = _cap_training_set(db, profile, intake_ids)
    # Train + holdout both need ground truth (holdout for the generalization
    # check); authoring uses only the train members.
    to_load = list(dict.fromkeys(capped + holdout_ids))
    # Granular step budget so the bar advances through every sub-step:
    # extract each invoice + author + test each train invoice + validate each
    # holdout + save.
    total_steps = len(to_load) + 1 + len(capped) + len(holdout_ids) + 1

    # Seed an authoritative per-invoice state map (train + holdout) so the
    # workbench shows real per-file status — queued -> extracting -> extracted.
    def _filename(iid: str) -> str | None:
        rec = crud.get_intake(db, iid)
        return rec.original_filename if rec else None

    invoices_state = {
        iid: {"filename": _filename(iid), "role": "train" if iid in set(capped) else "holdout",
              "state": "queued"}
        for iid in to_load
    }
    _emit(on_progress, "extracting", 0, total_steps, f"Preparing {len(to_load)} invoice(s)…",
          {"invoices": dict(invoices_state)})

    examples: list[TrainingExample] = []
    holdout_examples: list[TrainingExample] = []
    errors: list[str] = []
    blocking_errors: list[str] = []
    tok_in = tok_out = 0
    done = 0
    capped_set = set(capped)

    def _record_result(iid: str, example, error, ti: int, to: int) -> str:
        nonlocal tok_in, tok_out
        is_train = iid in capped_set
        tok_in += ti
        tok_out += to
        if example is not None:
            (examples if is_train else holdout_examples).append(example)
            invoices_state[iid]["state"] = "extracted"
        else:
            if error and error != _PENDING:
                errors.append(error)
                if is_train or "layout fingerprint mismatch" in error:
                    blocking_errors.append(error)
            invoices_state[iid].update(state="failed", reason=error)
        return "train" if is_train else "holdout"

    def _emit_done(iid: str, role: str) -> None:
        nonlocal done
        done += 1
        state = invoices_state[iid]["state"]
        _emit(on_progress, "extracting", done, total_steps,
              f"{'Extracted' if state == 'extracted' else 'Failed'} "
              f"{invoices_state[iid]['filename'] or iid} ({role}) ({done}/{len(to_load)})",
              {"invoice_update": {"intake_id": iid, **invoices_state[iid]},
               "tokens_input": tok_in, "tokens_output": tok_out})

    # Preloaded examples (already extracted this run) need no AI call.
    to_extract: list[str] = []
    for iid in to_load:
        if iid in preloaded_by_id:
            ex = preloaded_by_id[iid].prepared()
            (examples if iid in capped_set else holdout_examples).append(ex)
            invoices_state[iid]["state"] = "extracted"
            _emit_done(iid, "train" if iid in capped_set else "holdout")
        else:
            invoices_state[iid]["state"] = "extracting"
            to_extract.append(iid)

    # The per-invoice ground-truth extraction is I/O-bound (one OpenAI call
    # each), so run it across a bounded thread pool. Each worker gets its own
    # DB session (Sessions are not thread-safe); crud writes commit per call.
    if to_extract:
        workers = min(len(to_extract), max(1, settings.model_training_extract_concurrency))
        # Workers update intake rows on their own sessions; drop any intakes
        # prefetched above so progress commits on the main session don't autoflush stale rows.
        db.expire_all()
        _emit(on_progress, "extracting", done, total_steps,
              f"Extracting {len(to_extract)} invoice(s)"
              + (f" ({workers} in parallel)…" if workers > 1 else "…"),
              {"invoices": dict(invoices_state), "tokens_input": tok_in, "tokens_output": tok_out})
        if workers > 1:
            session_factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="train-extract") as pool:
                futures = {
                    pool.submit(_extract_example_threadsafe, session_factory, iid, profile, fingerprint): iid
                    for iid in to_extract
                }
                for fut in as_completed(futures):
                    iid = futures[fut]
                    example, error, ti, to = fut.result()
                    role = _record_result(iid, example, error, ti, to)
                    _emit_done(iid, role)
        else:
            for iid in to_extract:
                example, error, ti, to = _load_training_example(db, iid, profile, fingerprint)
                role = _record_result(iid, example, error, ti, to)
                _emit_done(iid, role)

    log_iid = log_intake_id or (examples[0].intake_id if examples else (capped[0] if capped else "training"))
    if blocking_errors:
        result = TrainingResult(
            success=False,
            training_intake_ids=list(capped),
            errors=blocking_errors,
        )
        crud.log_processing_step(
            db, intake_id=log_iid, step="model_generation", status="failed",
            message=f"Training set could not be loaded: {'; '.join(blocking_errors)}",
            details={"model_id": profile.profile_id, "intake_ids": capped, "errors": blocking_errors},
        )
        _emit(on_progress, "done", total_steps, total_steps,
              "Training stopped before authoring because the training set could not be loaded",
              {"success": False, "errors": blocking_errors, "invoices": dict(invoices_state)})
        return result
    if not examples:
        result = TrainingResult(success=False, errors=errors or ["no usable training invoices"])
        crud.log_processing_step(
            db, intake_id=log_iid, step="model_generation", status="failed",
            message=f"Training set could not be loaded: {'; '.join(result.errors)}",
            details={"model_id": profile.profile_id, "intake_ids": capped},
        )
        return result

    # Richest invoice (most line items) drives the prompt's full layout + the
    # deterministic header/total repair.
    examples.sort(key=lambda e: len(e.ai_invoice.line_items), reverse=True)
    primary, extras = examples[0], examples[1:]
    result = _train(
        db, profile=profile, fingerprint=fingerprint, layout_family_key=layout_family_key,
        primary=primary, extras=extras, job_id=job_id, log_intake_id=log_iid,
        holdout_intake_ids=holdout_ids, holdout_examples=holdout_examples,
        on_progress=on_progress,
        progress_base=len(to_load), total_steps=total_steps,
    )
    result.errors.extend(errors)
    return result


def _emit(on_progress, step: str, done: int, total: int, message: str, detail: dict | None) -> None:
    if on_progress is None:
        return
    try:
        on_progress(step, done, total, message, detail)
    except Exception as exc:  # pragma: no cover - progress must never break training
        logger.warning("training.progress_callback_failed", step=step, error=str(exc), exc_info=True)


def _extract_example_threadsafe(
    session_factory, intake_id: str, profile: SupplierProfile, fingerprint: str,
) -> tuple[TrainingExample | None, str | None, int, int]:
    """Load+extract one training example on its OWN DB session (thread-safe).

    Used by the parallel extraction pool. SQLAlchemy Sessions are not
    thread-safe, so each worker opens an independent session bound to the same
    engine as the caller; the crud writes inside commit per call. A worker
    exception is turned into a per-invoice failure so it never kills the pool.
    """
    with session_factory() as thread_db:
        try:
            return _load_training_example(thread_db, intake_id, profile, fingerprint)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("training.extract_worker_failed", intake_id=intake_id, error=str(exc))
            return None, f"{intake_id}: extraction crashed ({exc})", 0, 0


def _train(
    db: Session,
    *,
    profile: SupplierProfile,
    fingerprint: str,
    layout_family_key: str | None,
    primary: TrainingExample,
    extras: list[TrainingExample],
    job_id: str | None,
    log_intake_id: str,
    holdout_intake_ids: list[str] | None = None,
    holdout_examples: list[TrainingExample] | None = None,
    on_progress=None,
    progress_base: int = 0,
    total_steps: int = 0,
) -> TrainingResult:
    """Author once from primary + extras, validate against all, save if all pass.

    ``holdout_intake_ids`` are never passed to the authoring AI; they are simply
    carried onto the saved model so the approval gate can later test the model
    against invoices it was not authored from (the generalization signal).
    """
    from stencil.pricing import estimate_cost_usd

    all_examples = [primary, *extras]
    holdout_examples = holdout_examples or []
    set_size = len(all_examples)
    total = total_steps or (progress_base + 1 + set_size + len(holdout_examples) + 1)
    max_attempts = max(1, settings.model_authoring_max_attempts)
    result = TrainingResult(
        success=False, attempts=0,
        training_intake_ids=[ex.intake_id for ex in all_examples],
    )

    model: ExtractionModel | None = None
    feedback: str | None = None
    failures: list[str] = []
    per_invoice_detail: dict[str, dict] = {}

    # Refine loop: author -> validate the WHOLE set -> if any invoice fails, feed
    # the structured diffs back and re-author, until the model reproduces every
    # invoice or the attempt budget is spent.
    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        suffix = f" (attempt {attempt}/{max_attempts})" if max_attempts > 1 else ""

        _emit(on_progress, "authoring", progress_base, total,
              f"Authoring rules from {set_size} invoice(s){suffix}…", None)
        try:
            auth = author_extraction_model(
                page_texts=primary.page_texts,
                page_priority=primary.page_priority,
                layout_evidence=primary.layout_evidence,
                ai_invoice=primary.ai_invoice,
                profile=profile,
                fingerprint=fingerprint,
                layout_family_key=layout_family_key,
                intake_id=primary.intake_id,
                feedback=feedback,
                extra_examples=[
                    AuthoringExample(
                        page_texts=ex.page_texts, layout_evidence=ex.layout_evidence,
                        ai_invoice=ex.ai_invoice, intake_id=ex.intake_id,
                    )
                    for ex in extras
                ],
            )
        except Exception as exc:
            crud.log_ai_call(
                db, intake_id=log_intake_id, job_id=job_id, call_type="model_authoring",
                ai_model_name=runtime_settings.openai_model_model_generation(),
                status="error", error_message=str(exc),
            )
            result.errors.append(f"authoring call failed: {exc}")
            crud.log_processing_step(
                db, intake_id=log_intake_id, step="model_generation", status="failed",
                message=f"Model authoring call failed: {exc}",
                details={"model_id": profile.profile_id},
            )
            return result

        crud.log_ai_call(
            db, intake_id=log_intake_id, job_id=job_id, call_type="model_authoring",
            ai_model_name=auth.ai_model_name,
            tokens_input=auth.tokens_input, tokens_output=auth.tokens_output,
            estimated_cost_usd=float(estimate_cost_usd(
                auth.ai_model_name, auth.tokens_input, auth.tokens_output)),
            duration_ms=auth.duration_ms,
        )
        result.tokens_input += auth.tokens_input
        result.tokens_output += auth.tokens_output
        model = auth.model
        # Carry the holdout set forward — authoring never sees these, but the
        # saved model must remember them so the gate can test generalization.
        train_ids = set(model.training_intake_ids or [])
        model.holdout_intake_ids = [
            i for i in (holdout_intake_ids or []) if i and i not in train_ids
        ]
        result.model = model

        # Test the one model against EVERY training invoice, emitting live
        # per-invoice progress (testing -> reproduced/mismatch). Stream the
        # cumulative authoring token spend so the workbench cost updates live.
        failures = []
        per_invoice_detail = {}
        feedback_parts: list[str] = []
        warnings_seen = set(result.warnings)
        # Per-invoice (model_invoice, exec_error) for THIS attempt, so the final
        # attempt's outputs can be written for human inspection after the loop.
        attempt_outputs = {}
        test_base = progress_base + 1  # authoring counts as one step
        for tested, ex in enumerate(all_examples):
            _emit(on_progress, "testing", test_base + tested, total,
                  f"Testing training invoices ({tested + 1}/{set_size}){suffix}…",
                  {"invoice_update": {"intake_id": ex.intake_id, "state": "testing"},
                   "tokens_input": result.tokens_input, "tokens_output": result.tokens_output})
            model_invoice, diff, exec_error = _validate_example(model, ex, profile)
            ok = exec_error is None and diff is not None and diff.is_match
            reason = ("reproduced exactly" if ok
                      else (exec_error or (diff.summary() if diff else "no model output")))
            result.per_invoice[ex.intake_id] = ok
            per_invoice_detail[ex.intake_id] = {
                "ok": ok,
                "reason": reason,
                "expected_lines": len(ex.ai_invoice.line_items),
                "actual_lines": len(model_invoice.line_items) if model_invoice else 0,
            }
            tax_warning = _tax_strategy_warning(model, ex.ai_invoice, model_invoice)
            if tax_warning:
                per_invoice_detail[ex.intake_id]["warning"] = tax_warning
                if tax_warning not in warnings_seen:
                    warnings_seen.add(tax_warning)
                    result.warnings.append(tax_warning)
            attempt_outputs[ex.intake_id] = (model_invoice, exec_error)
            if ex is primary:
                result.model_invoice = model_invoice
                result.diff = diff
            if not ok:
                failures.append(f"{ex.intake_id}: {reason}")
                detail_fb = (diff.feedback() if diff is not None else exec_error) or reason
                feedback_parts.append(f"INVOICE {ex.intake_id}:\n{detail_fb}")
            _emit(on_progress, "testing", test_base + tested + 1, total,
                  f"Tested {tested + 1}/{set_size} training invoice(s){suffix}",
                  {"invoice_update": {"intake_id": ex.intake_id,
                                      "state": "passed" if ok else "failed",
                                      "reason": None if ok else reason},
                   "tokens_input": result.tokens_input, "tokens_output": result.tokens_output})
        result.per_invoice_detail = per_invoice_detail

        if not failures:
            break

        # Build feedback for the next authoring attempt from the failing diffs.
        feedback = "\n\n".join(feedback_parts)
        if attempt < max_attempts:
            crud.log_processing_step(
                db, intake_id=log_intake_id, step="model_generation", status="running",
                message=(f"Attempt {attempt}/{max_attempts} reproduced "
                         f"{set_size - len(failures)}/{set_size}; refining rules…"),
                details={"model_id": profile.profile_id, "failures": failures[:5]},
            )
            _emit(on_progress, "authoring", progress_base, total,
                  f"Attempt {attempt} missed {len(failures)}/{set_size}; refining…", None)

    # Side-by-side AI-vs-model artifacts for EVERY training invoice (final
    # attempt), so the operator can open each invoice's model output and see
    # exactly what the rules produced vs the ground truth.
    examples_by_id = {ex.intake_id: ex for ex in all_examples}
    for intake_id, (model_invoice, exec_error) in attempt_outputs.items():
        ex = examples_by_id.get(intake_id)
        if ex is None:
            continue
        write_model_review_artifacts(
            intake_id, ex.ai_invoice, model_invoice, execution_error=exec_error,
            spec=resolve_output_spec(profile),
        )

    # Log the final per-invoice validation outcomes.
    for ex in all_examples:
        info = per_invoice_detail.get(ex.intake_id, {})
        ok = bool(info.get("ok"))
        crud.log_processing_step(
            db, intake_id=log_intake_id, step="model_validation",
            status="passed" if ok else "failed",
            message=(f"Training invoice {ex.intake_id}: "
                     + ("reproduced exactly" if ok else info.get("reason", "not reproduced"))),
            details={"model_id": profile.profile_id, "intake_id": ex.intake_id,
                     "attempts": result.attempts},
        )

    holdout_base = progress_base + 1 + set_size
    if not failures and model is not None and holdout_examples:
        holdout_n = len(holdout_examples)

        def _holdout_progress(index: int, intake_id: str, state: str, reason: str | None) -> None:
            step_done = holdout_base + index + (0 if state == "validating" else 1)
            _emit(on_progress, "holdout", step_done, total,
                  f"Validating holdout invoices ({index + 1}/{holdout_n})…",
                  {"invoice_update": {"intake_id": intake_id, "state": state, "reason": reason},
                   "tokens_input": result.tokens_input, "tokens_output": result.tokens_output})

        holdout_detail = _validate_holdout_examples(
            db, model=model, profile=profile, examples=holdout_examples,
            log_intake_id=log_intake_id, attempts=result.attempts,
            on_each=_holdout_progress,
        )
        result.per_invoice_detail.update(holdout_detail)
        for intake_id, info in holdout_detail.items():
            ok = bool(info.get("ok"))
            result.per_invoice[intake_id] = ok
        if any(not info.get("ok") for info in holdout_detail.values()):
            failed = [iid for iid, info in holdout_detail.items() if not info.get("ok")]
            result.warnings.append(
                f"Holdout validation failed for {len(failed)} invoice(s): {', '.join(failed[:3])}"
            )

    # Save only if the model reproduces the WHOLE set.
    if not failures and model is not None:
        _emit(on_progress, "saving", holdout_base + len(holdout_examples), total,
              "Saving model…",
              {"tokens_input": result.tokens_input, "tokens_output": result.tokens_output})
        save_result = save_model(db, model)
        if save_result.action in ("created", "updated_candidate"):
            result.success = True
            result.model_id = save_result.model_id
            crud.log_processing_step(
                db, intake_id=log_intake_id, step="model_generation", status="completed",
                message=(
                    f"Model saved as candidate: {save_result.model_id} "
                    f"({save_result.action}; reproduces all {set_size} training invoice(s) "
                    f"in {result.attempts} attempt(s))"
                ),
                details={"model_id": save_result.model_id, "action": save_result.action,
                         "attempts": result.attempts,
                         "training_intake_ids": result.training_intake_ids},
            )
        else:
            result.errors.append(f"model not saved: {save_result.action}")
            crud.log_processing_step(
                db, intake_id=log_intake_id, step="model_generation", status="failed",
                message=f"Model not saved ({save_result.action}) — an approved model already exists",
                details={"model_id": save_result.model_id, "action": save_result.action},
            )
    else:
        result.errors.extend(failures)
        if model is not None:
            reason = failures[0] if failures else "model did not reproduce training set"
            save_result = save_failed_model(db, model, reason)
            result.model_id = save_result.model_id
            crud.log_processing_step(
                db, intake_id=log_intake_id, step="model_generation", status="failed_validation",
                message=(
                    f"Saved last attempted model as failed_validation: {save_result.model_id} "
                    f"({save_result.action})"
                ),
                details={"model_id": save_result.model_id, "action": save_result.action},
            )
        result.diff_report_path = _persist_training_report(primary.intake_id, profile, result)
        crud.log_processing_step(
            db, intake_id=log_intake_id, step="model_generation", status="failed",
            message=(
                f"Model not saved after {result.attempts} attempt(s) — does not reproduce "
                f"{len(failures)}/{set_size} training invoice(s): "
                f"{failures[0] if failures else 'unknown error'}"
            ),
            details={"model_id": profile.profile_id, "failures": failures,
                     "attempts": result.attempts,
                     "diff_report": result.diff_report_path},
        )

    done_msg = (
        f"Trained on {set_size} invoice(s); model reproduces all "
        f"(in {result.attempts} attempt(s))"
        if result.success
        else (f"Training did not converge ({len(failures)}/{set_size} not reproduced "
              f"after {result.attempts} attempt(s))")
    )
    _emit(on_progress, "done", total, total, done_msg,
          {"per_invoice": result.per_invoice_detail, "success": result.success,
           "attempts": result.attempts,
           "warnings": result.warnings,
           "tokens_input": result.tokens_input, "tokens_output": result.tokens_output})
    return result


def _validate_holdout_examples(
    db: Session,
    *,
    model: ExtractionModel,
    profile: SupplierProfile,
    examples: list[TrainingExample],
    log_intake_id: str,
    attempts: int,
    on_each=None,
) -> dict[str, dict]:
    """Evaluate holdout invoices as an advisory generalization signal.

    ``on_each(index, intake_id, state, reason)`` (optional) reports live
    per-invoice progress: ``"validating"`` as each starts, then
    ``"passed"``/``"failed"`` as it finishes.
    """
    detail: dict[str, dict] = {}
    for index, ex in enumerate(examples):
        if on_each is not None:
            on_each(index, ex.intake_id, "validating", None)
        model_invoice, diff, exec_error = _validate_example(model, ex, profile)
        ok = exec_error is None and diff is not None and diff.is_match
        reason = ("reproduced exactly" if ok
                  else (exec_error or (diff.summary() if diff else "no model output")))
        if on_each is not None:
            on_each(index, ex.intake_id, "passed" if ok else "failed", None if ok else reason)
        detail[ex.intake_id] = {
            "ok": ok,
            "reason": reason,
            "expected_lines": len(ex.ai_invoice.line_items),
            "actual_lines": len(model_invoice.line_items) if model_invoice else 0,
        }
        write_model_review_artifacts(
            ex.intake_id, ex.ai_invoice, model_invoice, execution_error=exec_error,
            spec=resolve_output_spec(profile),
        )
        crud.log_processing_step(
            db, intake_id=log_intake_id, step="model_holdout_validation",
            status="passed" if ok else "failed",
            message=(f"Holdout invoice {ex.intake_id}: "
                     + ("reproduced exactly" if ok else reason)),
            details={"model_id": profile.profile_id, "intake_id": ex.intake_id,
                     "attempts": attempts},
        )
    return detail


def model_training_set_ids(model: ExtractionModel) -> list[str]:
    """The invoices a model is authored to reproduce (with legacy fallback)."""
    ids = list(model.training_intake_ids or [])
    if not ids and model.created_from_intake:
        ids = [model.created_from_intake]
    return ids


def model_holdout_set_ids(model: ExtractionModel) -> list[str]:
    """The holdout invoices a model must reproduce but was never authored from."""
    train = set(model_training_set_ids(model))
    return [i for i in (model.holdout_intake_ids or []) if i and i not in train]


def build_training_set_view(db: Session, model: ExtractionModel) -> dict:
    """Cheap, read-only view of a model's training set for the workbench.

    Returns the same shape as :func:`evaluate_model_training_set` (membership +
    per-invoice rows + approvability summary) but performs NO model execution and
    NO disk ground-truth load: per-invoice reproduction is read back from the
    LATEST training run's ``detail``. Safe to poll on every upload/transfer/
    refresh — the heavy, authoritative validation runs only at the approve gate.
    """
    set_ids = model_training_set_ids(model)
    holdout_ids = model_holdout_set_ids(model)

    last_run = crud.get_latest_training_run(db, model.model_id)
    run_detail = (last_run.detail or {}) if last_run else {}
    per_invoice = run_detail.get("per_invoice") or {}

    def _row(iid: str) -> dict:
        record = crud.get_intake(db, iid)
        filename = record.original_filename if record else None
        staged = bool(record and record.status == "staged")
        info = per_invoice.get(iid)
        if isinstance(info, dict):
            ok = bool(info.get("ok"))
            reason = info.get("reason") or ("reproduced exactly" if ok else "not reproduced")
            line_count = info.get("expected_lines")
            trained = True
        elif isinstance(info, bool):
            ok = info
            reason = "reproduced exactly" if ok else "not reproduced"
            line_count = None
            trained = True
        else:
            ok = False
            reason = "Not trained yet — click Train"
            line_count = None
            trained = False
        return {"intake_id": iid, "filename": filename, "staged": staged,
                "pages": record.page_count if record else None,
                "size_bytes": record.file_size_bytes if record else None,
                "line_count": line_count, "ok": ok, "reason": reason, "trained": trained}

    invoices = [_row(i) for i in set_ids]
    holdout = [_row(i) for i in holdout_ids]

    all_trained = bool(invoices) and all(r["trained"] for r in invoices)
    reproduces_all = all_trained and all(r["ok"] for r in invoices)
    trained_holdout = [r for r in holdout if r["trained"]]
    holdout_reproduces_all = all(r["ok"] for r in trained_holdout)  # vacuous when empty
    required = int(runtime_settings.runtime_value("model_validation_required_successes"))
    has_min_count = len(set_ids) >= required
    approvable = reproduces_all and has_min_count

    reasons: list[str] = []
    if not invoices:
        reasons.append("the model has no training invoices")
    elif not all_trained:
        reasons.append("some invoices have not been trained yet — click Train")
    elif not reproduces_all:
        failing = [r["intake_id"] for r in invoices if not r["ok"]]
        reasons.append(f"does not reproduce {len(failing)} training invoice(s)")
    if invoices and not has_min_count:
        reasons.append(f"needs at least {required} training invoices (has {len(set_ids)})")

    warnings: list[str] = []
    if trained_holdout and not holdout_reproduces_all:
        failing = [r["intake_id"] for r in trained_holdout if not r["ok"]]
        warnings.append(
            f"fails {len(failing)} of {len(trained_holdout)} holdout invoice(s) — the model may "
            "not generalize to unseen invoices of this layout"
        )

    last_run_summary = None
    if last_run is not None:
        last_run_summary = {
            "id": last_run.id,
            "state": last_run.state,
            "attempts": run_detail.get("attempts"),
            "finished_at": last_run.finished_at.isoformat() if last_run.finished_at else None,
        }

    return {
        "training_intake_ids": set_ids,
        "holdout_intake_ids": holdout_ids,
        "required_count": required,
        "reproduces_all": reproduces_all,
        "holdout_reproduces_all": holdout_reproduces_all,
        "approvable": approvable,
        "reasons": reasons,
        "warnings": warnings,
        "invoices": invoices,
        "holdout": holdout,
        "last_run": last_run_summary,
    }


def evaluate_model_training_set(
    db: Session,
    model: ExtractionModel,
    profile: SupplierProfile,
) -> dict:
    """Re-execute a model against its whole training set and report approvability.

    Used by the approval gate. Each member is loaded from disk and the model must
    reproduce it exactly (self-checks ON). This is HEAVY (one model execution per
    invoice); the polled workbench view uses :func:`build_training_set_view`.
    """
    set_ids = model_training_set_ids(model)
    invoices = [_evaluate_one(db, model, profile, iid) for iid in set_ids]

    holdout_ids = model_holdout_set_ids(model)
    holdout = [_evaluate_one(db, model, profile, iid) for iid in holdout_ids]

    reproduces_all = bool(invoices) and all(i["ok"] for i in invoices)
    holdout_reproduces_all = all(i["ok"] for i in holdout)  # vacuously true when empty
    required = int(runtime_settings.runtime_value("model_validation_required_successes"))
    has_min_count = len(set_ids) >= required
    # Holdout is a warn-only trust signal: it never blocks approval.
    approvable = reproduces_all and has_min_count
    reasons: list[str] = []
    if not invoices:
        reasons.append("the model has no training invoices")
    if not reproduces_all:
        failing = [i["intake_id"] for i in invoices if not i["ok"]]
        reasons.append(f"does not reproduce {len(failing)} training invoice(s)")
    if not has_min_count:
        reasons.append(f"needs at least {required} training invoices (has {len(set_ids)})")

    warnings: list[str] = []
    if holdout and not holdout_reproduces_all:
        failing = [i["intake_id"] for i in holdout if not i["ok"]]
        warnings.append(
            f"fails {len(failing)} of {len(holdout)} holdout invoice(s) — the model may "
            "not generalize to unseen invoices of this layout"
        )

    return {
        "training_intake_ids": set_ids,
        "holdout_intake_ids": holdout_ids,
        "required_count": required,
        "reproduces_all": reproduces_all,
        "holdout_reproduces_all": holdout_reproduces_all,
        "approvable": approvable,
        "reasons": reasons,
        "warnings": warnings,
        "invoices": invoices,
        "holdout": holdout,
    }


def _evaluate_one(db: Session, model: ExtractionModel, profile: SupplierProfile, iid: str) -> dict:
    """Execute the model against one intake and report a per-invoice pass/fail row.

    EVALUATION ONLY — never triggers AI extraction. The training-set gate is
    polled by the UI and called on approval (both in the web request thread), so
    extracting here would hammer OpenAI and block the server. Samples that have
    not been processed yet (no stored ground truth) report a 'pending' row;
    their ground truth is produced by the explicit Train step.
    """
    record = crud.get_intake(db, iid)
    filename = record.original_filename if record else None
    example, error, _ti, _to = _load_training_example(
        db, iid, profile, model.layout_fingerprint, extract_if_missing=False,
    )
    if example is None:
        if error == _PENDING:
            return {"intake_id": iid, "filename": filename, "line_count": None,
                    "ok": False, "reason": "Not processed yet — click Train"}
        return {"intake_id": iid, "filename": filename, "line_count": None,
                "ok": False, "reason": error or "not loadable"}
    _inv, diff, exec_error = _validate_example(model, example, profile)
    ok = exec_error is None and diff is not None and diff.is_match
    return {
        "intake_id": iid,
        "filename": filename,
        "line_count": len(example.ai_invoice.line_items),
        "ok": ok,
        "reason": "reproduced exactly" if ok else (exec_error or (diff.summary() if diff else "no output")),
    }


def _validate_example(
    model: ExtractionModel,
    example: TrainingExample,
    profile: SupplierProfile,
) -> tuple[CanonicalInvoice | None, OutputDiff | None, str | None]:
    """Execute the model against one example. Returns (model_invoice, diff, exec_error).

    Self-checks are ON: a totals rule that's wrong on this invoice would raise
    on every future one, so it must fail validation. On a self-check failure we
    still re-run with checks off to surface the model's output in the diff.
    """
    try:
        model_invoice = execute_model(model, example.pdf_path, example.intake_id, document=example.document)
    except ModelExecutionError as exc:
        try:
            model_invoice = execute_model(
                model, example.pdf_path, example.intake_id,
                document=example.document, skip_self_checks=True,
            )
        except ModelExecutionError:
            return None, None, str(exc)
        _normalize_line_items(
            model_invoice,
            keep_zero_amount=profile.include_zero_amount_line_items,
            require_identifier=profile.require_line_item_identifier,
        )
        diff = diff_invoices(example.ai_invoice, model_invoice, resolve_output_spec(profile))
        return model_invoice, diff, str(exc)

    _normalize_line_items(
        model_invoice,
        keep_zero_amount=profile.include_zero_amount_line_items,
        require_identifier=profile.require_line_item_identifier,
    )
    diff = diff_invoices(example.ai_invoice, model_invoice, resolve_output_spec(profile))
    return model_invoice, diff, None


def _tax_strategy_warning(
    model: ExtractionModel,
    ai_invoice: CanonicalInvoice,
    model_invoice: CanonicalInvoice | None,
) -> str | None:
    """Warn when a deterministic grouped model cannot carry per-line tax literally."""
    if model.line_item_strategy is None or model_invoice is None:
        return None

    from stencil.output.xlsx_writer import resolve_tax_rate, service_items_have_per_line_tax

    if not service_items_have_per_line_tax(ai_invoice):
        return None
    if service_items_have_per_line_tax(model_invoice):
        return None
    if resolve_tax_rate(ai_invoice) is None:
        return None
    if resolve_tax_rate(model_invoice) is None:
        return (
            "AI ground truth has per-line tax, but the deterministic grouped model "
            "does not emit per-line tax and has no invoice tax_rate fallback."
        )
    return (
        "AI ground truth has per-line tax; the deterministic grouped model "
        "reproduces EXT_TAX through invoice tax_rate instead."
    )


def _load_training_example(
    db: Session,
    intake_id: str,
    profile: SupplierProfile,
    fingerprint: str,
    extract_if_missing: bool = True,
) -> tuple[TrainingExample | None, str | None, int, int]:
    """Load a processed invoice's PDF + AI ground truth from disk.

    Returns (example, error, tokens_in, tokens_out) — tokens are non-zero only
    when this call performed a fresh AI extraction.

    PDF: archive/{id}/original.pdf (durable) -> processing fallback.
    Ground truth: completed/{id}/canonical_invoice.json.

    ``extract_if_missing`` controls cost: the explicit Train step passes True
    (extract + persist ground truth on demand); the training-set GATE passes
    False so polling/approval NEVER triggers AI extraction — a sample without
    ground truth simply reports as pending.
    """
    pdf_path = settings.archive_dir / intake_id / "original.pdf"
    if not pdf_path.exists():
        pdf_path = settings.processing_dir / intake_id / "original.pdf"
    if not pdf_path.exists():
        return None, f"{intake_id}: source PDF not found", 0, 0

    ground_truth_path = settings.completed_dir / intake_id / "canonical_invoice.json"
    if not ground_truth_path.exists() and not extract_if_missing:
        return None, _PENDING, 0, 0  # never parse PDFs from the gate / a poll

    from stencil.fingerprint.fingerprinter import fingerprint_pdf

    actual_fingerprint, _features = fingerprint_pdf(pdf_path, profile=profile)
    crud.update_intake_fingerprint(db, intake_id, actual_fingerprint)
    if fingerprint and actual_fingerprint != fingerprint:
        return (
            None,
            (
                f"{intake_id}: layout fingerprint mismatch "
                f"(expected {fingerprint[:16]}..., got {actual_fingerprint[:16]}...)"
            ),
            0,
            0,
        )

    tokens_in = tokens_out = 0
    if ground_truth_path.exists():
        try:
            ai_invoice = CanonicalInvoice.model_validate_json(
                ground_truth_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            return None, f"{intake_id}: unreadable ground truth ({exc})", 0, 0
        document = extract_layout_document(pdf_path)
    else:
        document = extract_layout_document(pdf_path)
        ai_invoice, tokens_in, tokens_out = _extract_ground_truth(
            db, intake_id, pdf_path, document, profile, fingerprint,
        )
        if ai_invoice is None:
            return None, f"{intake_id}: AI ground-truth extraction failed", 0, 0
        # Persist ground truth so re-training / holdout-eval reuse it (no re-pay),
        # and mark the staged sample ready.
        try:
            from stencil.output.json_writer import write_canonical_json
            write_canonical_json(ai_invoice, ground_truth_path)
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("training.ground_truth_persist_failed", intake_id=intake_id, error=str(exc))
        crud.update_intake_status(db, intake_id, "ground_truth_ready")

    example = TrainingExample(
        intake_id=intake_id, ai_invoice=ai_invoice, document=document, pdf_path=pdf_path,
    ).prepared()
    return example, None, tokens_in, tokens_out


def _extract_ground_truth(
    db: Session,
    intake_id: str,
    pdf_path: Path,
    document: LayoutDocument,
    profile: SupplierProfile,
    fingerprint: str,
) -> tuple[CanonicalInvoice | None, int, int]:
    """Run AI extraction for ground truth. Returns (invoice, tokens_in, tokens_out)."""
    from stencil.extraction.extractor import build_canonical_invoice, extract_invoice
    from stencil.extraction.normalization import apply_layout_profile_hints
    from stencil.pricing import estimate_cost_usd

    try:
        extraction = extract_invoice(
            pdf_path,
            supplier_name=profile.identity.canonical_name,
            output_type=profile.classification.output_type,
            supplier_profile_id=profile.profile_id,
        )
    except Exception as exc:
        crud.log_ai_call(
            db, intake_id=intake_id, call_type="extraction",
            ai_model_name=runtime_settings.openai_model_extraction(),
            status="error", error_message=str(exc),
        )
        return None, 0, 0
    # Record the training-sample extraction cost (intake_source="training" lets
    # the dashboard attribute it to model training, not production extraction).
    crud.log_ai_call(
        db, intake_id=intake_id, call_type="extraction",
        ai_model_name=extraction.ai_model_name,
        tokens_input=extraction.tokens_input, tokens_output=extraction.tokens_output,
        estimated_cost_usd=float(estimate_cost_usd(
            extraction.ai_model_name, extraction.tokens_input, extraction.tokens_output)),
        duration_ms=extraction.duration_ms,
    )
    invoice = build_canonical_invoice(
        raw_data=extraction.raw_data, intake_id=intake_id, extraction_result=extraction,
        output_type=profile.classification.output_type,
        supplier_profile_id=profile.profile_id, layout_fingerprint=fingerprint,
    )
    apply_layout_profile_hints(invoice, document, profile, profile.line_item_hints)
    _normalize_line_items(
        invoice,
        keep_zero_amount=profile.include_zero_amount_line_items,
        require_identifier=profile.require_line_item_identifier,
    )
    return invoice, extraction.tokens_input, extraction.tokens_output


def _cap_training_set(db: Session, profile: SupplierProfile, intake_ids: list[str]) -> list[str]:
    """Bound the set to model_training_max_set_size.

    Keeps previously-failing invoices (the ones that drove a retrain) and the
    most line-item-rich, since those exercise the most row shapes. Cheap proxy
    for line-item richness: the stored ground-truth file size.
    """
    deduped = list(dict.fromkeys(i for i in intake_ids if i))
    cap = max(settings.model_training_max_set_size, 1)
    if len(deduped) <= cap:
        return deduped

    def richness(iid: str) -> int:
        gt = settings.completed_dir / iid / "canonical_invoice.json"
        try:
            return gt.stat().st_size
        except OSError:
            return 0

    # Always keep the most recently added (last in list) — it triggered this
    # retrain — then fill the rest by richness.
    must_keep = deduped[-1:]
    rest = sorted(deduped[:-1], key=richness, reverse=True)
    return list(dict.fromkeys(must_keep + rest))[:cap]


def _persist_training_report(intake_id: str, profile: SupplierProfile, result: TrainingResult) -> str | None:
    """Write the failure diff report next to the intake's review artifacts."""
    try:
        review_root = settings.completed_dir / intake_id / "model_review"
        review_root.mkdir(parents=True, exist_ok=True)
        report = {
            "profile_id": profile.profile_id,
            "intake_id": intake_id,
            "training_intake_ids": result.training_intake_ids,
            "per_invoice": result.per_invoice,
            "per_invoice_detail": result.per_invoice_detail,
            "attempts": result.attempts,
            "warnings": result.warnings,
            "errors": result.errors,
            "diff": result.diff.to_report() if result.diff else None,
            "model_rules": result.model.model_dump(mode="json") if result.model else None,
        }
        path = review_root / "training_report.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return str(path)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("training.report_write_failed", intake_id=intake_id, error=str(exc))
        return None


def _li_has_identifier(item) -> bool:
    """True when a line item has a usable service_id or billing_reference."""
    import re as _re

    for value in (item.service_id, item.billing_reference):
        if value and _re.search(r"[A-Za-z0-9]", str(value)):
            return True
    return False


def _normalize_line_items(
    invoice: CanonicalInvoice,
    keep_zero_amount: bool = False,
    require_identifier: bool = False,
) -> CanonicalInvoice:
    """Resequence line numbers; optionally drop zero-amount and/or no-identifier
    rows, in place."""
    kept = list(invoice.line_items)
    if not keep_zero_amount:
        kept = [item for item in kept if item.amount != 0]
    if require_identifier:
        kept = [item for item in kept if _li_has_identifier(item)]
    for new_number, item in enumerate(kept, start=1):
        item.line_number = new_number
    invoice.line_items = kept
    return invoice
