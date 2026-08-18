"""Service logic for interactive profile authoring sessions.

Stateless helpers driven by the Celery tasks (and unit-testable on their own):
- ``run_extraction`` — AI-extract one uploaded sample once and cache its
  artifacts (canonical doc, layout, page texts, parsed expected rows) on disk.
- ``run_turn`` — one chat turn: author a fresh draft profile, then render a
  per-invoice deliverable preview and (when an expected file was attached) a diff.

The heavy artifacts live under ``work_dir/authoring/{session_id}/{invoice_id}/``;
the conversation, current draft, and latest previews live in the session DB row.
"""

from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from stencil.config import settings
from stencil.db import crud
from stencil.extraction.extractor import build_extracted_document, extract_invoice
from stencil.extraction.layout import (
    LayoutDocument,
    extract_layout_document,
    render_layout_text,
    scan_pdf_pages,
)
from stencil.extraction.normalization import apply_layout_profile_hints
from stencil.fields.loader import (
    default_field_schema,
    get_field_schema,
    merge_field_schema,
    resolve_merged_field_schema,
)
from stencil.models.authoring import _build_target
from stencil.models.diff import diff_output_rows
from stencil.output.expected_loader import load_blueprint_context
from stencil.output.mapper import output_spec_to_columns
from stencil.output.preview import preview_for_profile
from stencil.output.xlsx_writer import build_output_rows
from stencil.pricing import estimate_cost_usd
from stencil.profiles.authoring import (
    InvoiceEvidence,
    author_profile,
    draft_to_supplier_profile_dict,
    merge_source_profile_config,
)
from stencil.profiles.discovery import ENGINE_VERSION, DiscoveryInput, discover_profile
from stencil.profiles.loader import get_profile
from stencil.profiles.schema import SupplierProfile
from stencil.profiles.validation import grade_authoring_evidence
from stencil.specs.loader import get_output_spec, load_all_output_specs, resolve_output_spec
from stencil.validation.schema import ExtractedDocument

logger = structlog.get_logger()

_DRAFT_PROFILE_ID = "draft.authoring.preview"  # placeholder id for preview-only profiles


def _add_compatible_spec_suggestions(context: dict) -> dict:
    if context.get("contract_compatible") is not False:
        return context
    raw = [str(header).strip().casefold() for header in context.get("raw_headers") or []]
    context["compatible_output_spec_ids"] = [
        spec.spec_id
        for spec in load_all_output_specs().values()
        if [column.header.strip().casefold() for column in spec.columns] == raw
    ]
    return context


def session_artifact_dir(session_id: str, invoice_id: str) -> Path:
    return settings.work_dir / "authoring" / session_id / invoice_id


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_and_cache(
    db: Session,
    *,
    session,
    invoice_id: str,
    pdf: Path,
    artifact_dir: Path,
    call_type: str,
    profile: SupplierProfile | None,
    field_schema,
) -> None:
    """AI-extract one sample PDF and (re)write its cached canonical/layout artifacts.

    Shared by the one-time upload extraction (``profile=None``, default schema)
    and on-demand re-extraction with the current draft profile's hints.
    """
    schema = field_schema or default_field_schema()
    output_type = (
        session.category_override
        or session.inferred_category
        or ("time_and_material" if "time_and_material" in session.output_spec_id else "standard")
    )
    result = extract_invoice(
        pdf, supplier_name=session.supplier_name, output_type=output_type,
        field_schema=schema, supplier_profile=profile, artifact_dir=artifact_dir,
    )
    doc = build_extracted_document(
        result.raw_data, intake_id=f"authoring-{invoice_id}", extraction_result=result,
        output_type=output_type, field_schema=schema,
    )
    cached_layout_path = artifact_dir / "layout.json"
    layout = (
        LayoutDocument.model_validate(_read_json(cached_layout_path))
        if cached_layout_path.exists()
        else extract_layout_document(pdf)
    )
    page_texts = render_layout_text(layout)

    _write_json(artifact_dir / "extracted.json", doc.model_dump(mode="json"))
    _write_json(artifact_dir / "layout.json", layout.model_dump(mode="json"))
    _write_json(artifact_dir / "page_texts.json", page_texts)

    # Track AI cost for the session.
    cost = float(estimate_cost_usd(result.ai_model_name, result.tokens_input, result.tokens_output))
    crud.log_ai_call(
        db, intake_id=f"authoring-{session.id}", job_id=None, call_type=call_type,
        ai_model_name=result.ai_model_name, tokens_input=result.tokens_input,
        tokens_output=result.tokens_output, estimated_cost_usd=cost, duration_ms=result.duration_ms,
    )
    crud.update_authoring_session(
        db, session.id,
        tokens_input=(session.tokens_input or 0) + result.tokens_input,
        tokens_output=(session.tokens_output or 0) + result.tokens_output,
        estimated_cost_usd=float(session.estimated_cost_usd or 0) + cost,
    )


def stage_sample(session_id: str, invoice_id: str, *, pdf_path: str, expected_xlsx_path: str | None) -> Path:
    """Persist an uploaded sample (PDF + optional blueprint XLS/XLSX) into the artifact dir.

    Extraction is deferred to the first authoring turn, so the file just needs to be
    saved here; the temp upload paths are consumed. Returns the artifact directory.
    """
    artifact_dir = session_artifact_dir(session_id, invoice_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf_path, artifact_dir / "source.pdf")
    Path(pdf_path).unlink(missing_ok=True)
    if expected_xlsx_path:
        source = Path(expected_xlsx_path)
        suffix = source.suffix.lower() if source.suffix.lower() in {".xls", ".xlsx"} else ".xlsx"
        shutil.copy2(expected_xlsx_path, artifact_dir / f"blueprint{suffix}")
        Path(expected_xlsx_path).unlink(missing_ok=True)
    return artifact_dir


def stage_standalone_blueprint(
    session_id: str, blueprint_id: str, *, source_path: str, filename: str,
) -> Path:
    artifact_dir = settings.work_dir / "authoring" / session_id / "blueprints"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix.lower()
    target = artifact_dir / f"{blueprint_id}{suffix}"
    shutil.copy2(source_path, target)
    Path(source_path).unlink(missing_ok=True)
    return target


def delete_sample_artifacts(*, session_id: str, invoice_id: str, artifact_dir: str | None) -> None:
    """Delete one staged sample directory, constrained to this session's artifact root."""
    if not artifact_dir:
        return
    base = (settings.work_dir / "authoring" / session_id).resolve()
    target = Path(artifact_dir).resolve()
    if target == base or base not in target.parents:
        raise ValueError("authoring sample artifact path is outside the session directory")
    shutil.rmtree(target, ignore_errors=True)


def delete_blueprint_artifact(*, session_id: str, artifact_path: str) -> None:
    base = (settings.work_dir / "authoring" / session_id / "blueprints").resolve()
    target = Path(artifact_path).resolve()
    if base not in target.parents:
        raise ValueError("authoring blueprint path is outside the session directory")
    target.unlink(missing_ok=True)


def run_discovery(db: Session, *, session_id: str) -> dict:
    """Flat-scan all samples and blueprints without spending extraction tokens."""
    session = crud.get_authoring_session(db, session_id)
    if session is None:
        raise ValueError("authoring session not found")
    invoices = [
        invoice for invoice in crud.list_authoring_invoices(db, session.id)
        if invoice.extraction_status in {"uploaded", "pending", "done"}
    ]
    if not invoices:
        raise ValueError("upload at least one sample invoice before discovery")
    spec = get_output_spec(session.output_spec_id)
    contexts: list[dict] = []
    for invoice in invoices:
        artifact_dir = Path(invoice.artifact_dir)
        blueprint = next(iter(artifact_dir.glob("blueprint.xls*")), None)
        if blueprint is not None:
            context = _add_compatible_spec_suggestions(
                load_blueprint_context(str(blueprint), spec)
            )
            context["filename"] = blueprint.name
            context["_paired_invoice"] = invoice.filename
            contexts.append(context)
            _write_json(artifact_dir / "blueprint_context.json", context)
            _write_json(artifact_dir / "expected_rows.json", context["aligned_rows"])
    for blueprint in crud.list_authoring_blueprints(db, session.id):
        context = _add_compatible_spec_suggestions(
            load_blueprint_context(blueprint.artifact_path, spec)
        )
        context["filename"] = blueprint.filename
        contexts.append(context)

    crud.update_authoring_session(db, session.id, phase="scanning")
    report = discover_profile(
        invoices=[
            DiscoveryInput(label=invoice.filename, pdf_path=Path(invoice.artifact_dir) / "source.pdf")
            for invoice in invoices
        ],
        blueprint_contexts=contexts,
        output_spec=spec,
        supplier_name=session.supplier_name,
        document_family_override=session.category_override,
    )
    payload = report.model_dump(mode="json")
    for invoice, invoice_report in zip(invoices, report.invoices, strict=True):
        crud.update_authoring_invoice(
            db,
            invoice.id,
            scan_metadata=invoice_report.model_dump(mode="json"),
        )
    for blueprint, signature in zip(
        crud.list_authoring_blueprints(db, session.id),
        report.blueprints[-len(crud.list_authoring_blueprints(db, session.id)):] or [],
    ):
        crud.update_authoring_blueprint(
            db,
            blueprint.id,
            matching_metadata={
                "linked_invoice": signature.linked_invoice,
                "match_kind": signature.match_kind,
                "identifiers": len(signature.identifiers),
            },
        )
    crud.update_authoring_session(
        db,
        session.id,
        phase="discovered",
        inferred_category=report.inferred_document_family,
        discovery_report=payload,
        validation_summary=report.evidence.model_dump(mode="json"),
        engine_version=report.engine_version,
    )
    return payload


def _parse_expected_blueprint(session, invoice, artifact_dir: Path) -> None:
    blueprint_path = next(iter(artifact_dir.glob("blueprint.xls*")), artifact_dir / "expected.xlsx")
    if invoice.has_expected and blueprint_path.exists():
        spec = get_output_spec(session.output_spec_id)
        blueprint_context = _add_compatible_spec_suggestions(
            load_blueprint_context(str(blueprint_path), spec)
        )
        _write_json(artifact_dir / "blueprint_context.json", blueprint_context)
        _write_json(artifact_dir / "expected_rows.json", blueprint_context["aligned_rows"])


def run_extraction(db: Session, *, session_id: str, invoice_id: str) -> None:
    """AI-extract one staged sample's baseline evidence + layout and cache artifacts.

    Reads the ``source.pdf`` (and optional blueprint workbook) staged at upload time,
    so this is safe to run lazily on the first turn. Idempotent per sample: callers
    only invoke it for invoices not yet ``done``.
    """
    session = crud.get_authoring_session(db, session_id)
    invoice = crud.get_authoring_invoice(db, invoice_id)
    if session is None or invoice is None:
        logger.warning("authoring.extract.missing", session_id=session_id, invoice_id=invoice_id)
        return

    artifact_dir = Path(invoice.artifact_dir) if invoice.artifact_dir else session_artifact_dir(session_id, invoice_id)
    source_pdf = artifact_dir / "source.pdf"
    if not source_pdf.exists():
        crud.update_authoring_invoice(
            db, invoice_id, extraction_status="error", error_message="Staged sample PDF is missing")
        return
    crud.update_authoring_invoice(db, invoice_id, extraction_status="pending", error_message=None)
    try:
        profile = get_profile(session.source_profile_id) if session.source_profile_id else None
        field_schema = (
            resolve_merged_field_schema(profile)
            if profile is not None
            else get_field_schema(session.field_schema_id)
        )
        _extract_and_cache(
            db, session=session, invoice_id=invoice_id, pdf=source_pdf,
            artifact_dir=artifact_dir, call_type="profile_authoring_extract",
            profile=profile, field_schema=field_schema,
        )

        _parse_expected_blueprint(session, invoice, artifact_dir)

        crud.update_authoring_invoice(db, invoice_id, extraction_status="done", artifact_dir=str(artifact_dir))
        logger.info("authoring.extract.done", session_id=session_id, invoice_id=invoice_id)
    except Exception as exc:
        logger.error("authoring.extract.failed", session_id=session_id, invoice_id=invoice_id, error=str(exc))
        crud.update_authoring_invoice(db, invoice_id, extraction_status="error", error_message=str(exc))


def _preview_profile_from_draft(session, draft: dict) -> SupplierProfile:
    source = get_profile(session.source_profile_id) if session.source_profile_id else None
    schema = resolve_merged_field_schema(source) if source else get_field_schema(session.field_schema_id)
    spec = get_output_spec(session.output_spec_id)
    profile_dict = draft_to_supplier_profile_dict(
        draft, output_spec_id=session.output_spec_id,
        field_schema_id=session.field_schema_id, field_schema=schema, output_spec=spec,
    )
    if source is not None:
        profile_dict = merge_source_profile_config(source, profile_dict)
    profile_dict["profile_id"] = _DRAFT_PROFILE_ID
    profile_dict["status"] = "draft"
    return SupplierProfile.model_validate(profile_dict)


def _invoice_evidence(db: Session, session) -> list[InvoiceEvidence]:
    """Build per-invoice grounding from cached artifacts (+ last turn's diff)."""
    invoices = [
        i for i in crud.list_authoring_invoices(db, session.id)
        if i.extraction_status in {"done", "uploaded"}
    ]
    prior_previews = session.previews or {}
    evidence: list[InvoiceEvidence] = []
    for inv in invoices:
        adir = Path(inv.artifact_dir)
        if (adir / "extracted.json").exists():
            doc = ExtractedDocument.model_validate(_read_json(adir / "extracted.json"))
            target = _build_target(doc, max_rows=settings.model_authoring_sample_rows)
            page_texts = _read_json(adir / "page_texts.json")
        else:
            target = {"header": {}, "totals": {}, "line_items": []}
            scan = scan_pdf_pages(adir / "source.pdf")
            page_texts = [page.text for page in scan.pages]
        expected_rows = None
        blueprint_context = None
        if (adir / "expected_rows.json").exists():
            expected_rows = _read_json(adir / "expected_rows.json")
        if (adir / "blueprint_context.json").exists():
            blueprint_context = _read_json(adir / "blueprint_context.json")
            if settings.profile_discovery_engine_enabled:
                blueprint_context = {
                    key: blueprint_context.get(key)
                    for key in (
                        "filename",
                        "aligned_headers",
                        "raw_headers",
                        "row_count",
                        "sample_rows",
                        "totals",
                        "warnings",
                    )
                }
        diff_feedback = (prior_previews.get(inv.id) or {}).get("diff_feedback")
        if settings.profile_discovery_engine_enabled and inv.scan_metadata:
            selected = set(inv.scan_metadata.get("selected_page_numbers") or [])
            selected.update(range(1, min(5, len(page_texts)) + 1))
            selected.update(range(max(1, len(page_texts) - 1), len(page_texts) + 1))
            page_texts = [
                text for page_number, text in enumerate(page_texts, start=1)
                if page_number in selected
            ]
        evidence.append(InvoiceEvidence(
            label=inv.filename,
            page_texts=page_texts,
            target=target,
            expected_rows=(None if settings.profile_discovery_engine_enabled else expected_rows),
            blueprint_context=blueprint_context,
            diff_feedback=diff_feedback,
        ))
    return evidence


def _previews_for_draft(db: Session, session, draft: dict) -> dict:
    """Render a deliverable preview (and diff vs expected) for each sample invoice."""
    profile = _preview_profile_from_draft(session, draft)
    spec = resolve_output_spec(profile)
    columns = output_spec_to_columns(spec)
    out: dict[str, dict] = {}
    for inv in crud.list_authoring_invoices(db, session.id):
        if inv.extraction_status != "done":
            out[inv.id] = {
                "filename": inv.filename,
                "error": inv.error_message or "Sample extraction did not complete.",
            }
            continue
        adir = Path(inv.artifact_dir)
        try:
            doc = ExtractedDocument.model_validate(_read_json(adir / "extracted.json"))
            layout = LayoutDocument.model_validate(_read_json(adir / "layout.json"))
        except Exception as exc:
            out[inv.id] = {"filename": inv.filename, "error": str(exc)}
            continue
        apply_layout_profile_hints(doc, layout, profile)
        preview = preview_for_profile(doc, profile, extraction_path="ai")
        entry: dict = {"filename": inv.filename, "preview": preview}
        if (adir / "expected_rows.json").exists():
            expected = _read_json(adir / "expected_rows.json")
            actual = build_output_rows(doc, spec)
            diff = diff_output_rows(expected, actual, columns)
            entry["diff"] = diff.to_report()
            if not diff.is_match:
                entry["diff_feedback"] = diff.feedback()
        out[inv.id] = entry
    return out


def _grade_previews(discovery_report: dict, previews: dict) -> dict:
    discovered_by_label = {
        item.get("label"): item for item in discovery_report.get("invoices") or []
    }
    sample_results: list[dict] = []
    for preview_entry in previews.values():
        preview = preview_entry.get("preview") or {}
        reconciliation = preview.get("reconciliation") or {}
        diff = preview_entry.get("diff")
        diff_count = None
        if diff is not None:
            diff_count = (
                len(diff.get("missing_rows") or [])
                + len(diff.get("extra_rows") or [])
                + len(diff.get("cell_diffs") or [])
            )
        discovered = discovered_by_label.get(preview_entry.get("filename")) or {}
        denominator = int(discovered.get("blueprint_identifier_count") or 0)
        coverage = (
            int(discovered.get("matched_identifier_count") or 0) / denominator
            if denominator else None
        )
        preview_messages = [
            str(message)
            for message in [
                *(preview.get("warnings") or []),
                *(preview.get("exceptions") or []),
            ]
        ]
        hard_message_tokens = (
            "rejected ",
            "ungrounded",
            "possible missing line items",
            "configured line-item region was not found",
        )
        hard_messages = [
            message for message in preview_messages
            if any(token in message.lower() for token in hard_message_tokens)
        ]
        required_missing = [] if preview.get("rows") else ["line_items"]
        if hard_messages:
            required_missing.append("grounded complete line items")
        sample_results.append({
            "output_diff_count": diff_count,
            "identifier_coverage": coverage,
            "reconciled": bool(reconciliation.get("is_reconciled")),
            "reconciliation_variance": reconciliation.get("variance"),
            "required_fields_missing": required_missing,
        })
    evidence = discovery_report.get("evidence") or {}
    graded = grade_authoring_evidence(
        evidence_level=evidence.get("evidence_level", "invoice_only"),
        sample_results=sample_results,
        category_confidence=float(discovery_report.get("category_confidence") or 0),
        engine_version=discovery_report.get("engine_version") or ENGINE_VERSION,
        historical_coverage_threshold=settings.profile_discovery_historical_id_coverage,
    )
    base_metrics = dict(evidence.get("metrics") or {})
    graded.metrics = {**base_metrics, **graded.metrics}
    graded.hard_blockers = list(dict.fromkeys([
        *(evidence.get("hard_blockers") or []),
        *graded.hard_blockers,
    ]))
    graded.review_warnings = list(dict.fromkeys([
        *(evidence.get("review_warnings") or []),
        *graded.review_warnings,
    ]))
    graded.unresolved_risks = list(dict.fromkeys([
        *graded.hard_blockers,
        *graded.review_warnings,
    ]))
    if graded.hard_blockers:
        graded.status = "failed"
    return graded.model_dump(mode="json")


def _reextract_done_invoices_with_draft(
    db: Session,
    *,
    session_id: str,
    draft: dict,
    invoice_id: str | None = None,
    continue_on_error: bool = False,
) -> list[str]:
    """Re-read staged/extracted sample PDFs using the supplied draft profile's hints."""
    session = crud.get_authoring_session(db, session_id)
    if session is None:
        raise ValueError("authoring session not found")

    profile = _preview_profile_from_draft(session, draft)
    schema = resolve_merged_field_schema(profile)

    invoices = [
        i for i in crud.list_authoring_invoices(db, session.id)
        if i.extraction_status in {"uploaded", "done"}
    ]
    if invoice_id is not None:
        invoices = [i for i in invoices if i.id == invoice_id]
    if not invoices:
        raise ValueError("no uploaded or extracted invoices to re-extract")

    reextracted: list[str] = []
    for inv in invoices:
        source_pdf = Path(inv.artifact_dir) / "source.pdf"
        if not source_pdf.exists():
            logger.warning("authoring.reextract.missing_pdf", session_id=session_id, invoice_id=inv.id)
            continue
        crud.update_authoring_invoice(db, inv.id, extraction_status="pending", error_message=None)
        # Refresh the session row so the running cost/token totals accumulate.
        session = crud.get_authoring_session(db, session_id)
        artifact_dir = Path(inv.artifact_dir)
        try:
            _extract_and_cache(
                db, session=session, invoice_id=inv.id, pdf=source_pdf,
                artifact_dir=artifact_dir, call_type="profile_authoring_reextract",
                profile=profile, field_schema=schema,
            )
            _parse_expected_blueprint(session, inv, artifact_dir)
        except Exception as exc:
            crud.update_authoring_invoice(db, inv.id, extraction_status="error", error_message=str(exc))
            logger.exception(
                "authoring.reextract.sample_failed",
                session_id=session_id,
                invoice_id=inv.id,
                filename=inv.filename,
            )
            if continue_on_error:
                continue
            raise
        crud.update_authoring_invoice(db, inv.id, extraction_status="done", artifact_dir=str(artifact_dir))
        reextracted.append(inv.id)
    return reextracted


def _reextract_isolated(session_id: str, draft: dict, invoice_id: str) -> list[str]:
    from stencil.db.session import SessionLocal

    isolated = SessionLocal()
    try:
        return _reextract_done_invoices_with_draft(
            isolated,
            session_id=session_id,
            draft=draft,
            invoice_id=invoice_id,
            continue_on_error=True,
        )
    finally:
        isolated.close()


def run_turn(db: Session, *, session_id: str, user_message: str) -> dict:
    """Author a new draft from the conversation + evidence, then preview it.

    Returns the turn payload (assistant message, draft, per-invoice previews).
    Appends the user message only when this queued turn actually runs, so queued
    future turns do not leak into the current prompt.
    """
    session = crud.get_authoring_session(db, session_id)
    if session is None:
        raise ValueError("authoring session not found")
    previous_draft = session.draft_profile
    discovery_report = run_discovery(db, session_id=session_id)
    bounded_chunks = sum(
        int(invoice.get("predicted_bounded_chunks") or 0)
        for invoice in discovery_report.get("invoices") or []
    )
    if bounded_chunks > settings.profile_discovery_max_ai_chunks:
        raise ValueError(
            f"Discovery predicts {bounded_chunks} AI chunks, above the "
            f"{settings.profile_discovery_max_ai_chunks}-chunk safeguard. "
            "Add structural guidance or a matching blueprint before authoring."
        )
    session = crud.get_authoring_session(db, session_id)

    field_schema = get_field_schema(session.field_schema_id)
    if session.source_profile_id:
        source = get_profile(session.source_profile_id)
        if source is not None:
            field_schema = resolve_merged_field_schema(source)
    output_spec = get_output_spec(session.output_spec_id)
    # Re-resolve label hints if the (placeholder) draft already exists.
    if session.draft_profile:
        try:
            preview_profile = _preview_profile_from_draft(session, session.draft_profile)
            field_schema = merge_field_schema(field_schema, preview_profile.field_overrides)
        except Exception:
            pass

    evidence = _invoice_evidence(db, session)
    if not evidence:
        raise ValueError("no usable samples — upload at least one sample invoice that extracts cleanly")

    result = author_profile(
        field_schema=field_schema,
        output_spec=output_spec,
        invoices=evidence,
        conversation=[*list(session.conversation or []), {"role": "user", "content": user_message}],
        current_draft=session.draft_profile,
        discovery_report={
            "inferred_document_family": discovery_report["inferred_document_family"],
            "category_confidence": discovery_report["category_confidence"],
            "category_reasons": discovery_report["category_reasons"],
            "candidate_plan": discovery_report["candidate_plan"],
            "inferred_date_formats": discovery_report.get("inferred_date_formats") or {},
            "evidence": discovery_report["evidence"],
            "invoices": discovery_report["invoices"],
        },
    )
    authored_profile = result.draft.setdefault("profile", {})
    inherited_by_header = {column.header: column for column in output_spec.columns}
    normalized_mappings = []
    for mapping in authored_profile.get("output_mapping_overrides") or []:
        mapping["transforms"] = list(mapping.get("transforms") or [])
        inherited = inherited_by_header.get(mapping.get("output_header"))
        if (
            inherited is not None
            and mapping.get("source") == inherited.source
            and (mapping.get("fallback") or None) == (inherited.fallback or None)
            and not mapping["transforms"]
        ):
            continue
        normalized_mappings.append(mapping)
    authored_profile["output_mapping_overrides"] = normalized_mappings
    field_overrides = authored_profile.setdefault("field_overrides", [])
    overrides_by_path = {
        item.get("field_path"): item for item in field_overrides if item.get("field_path")
    }
    for field_path, date_format in (discovery_report.get("inferred_date_formats") or {}).items():
        existing = overrides_by_path.get(field_path)
        if existing is None:
            field_overrides.append({
                "field_path": field_path,
                "label_hint": None,
                "date_format": date_format,
            })
        elif not existing.get("date_format"):
            existing["date_format"] = date_format
    if result.selected_plan_id == "deterministic.v1":
        authored_profile["extraction_plan"] = discovery_report["candidate_plan"]
    authored_profile["authoring_evidence"] = discovery_report["evidence"]

    reextracted_invoice_ids: list[str] = []
    candidate_invoice_ids = [
        i.id for i in crud.list_authoring_invoices(db, session.id)
        if i.extraction_status in {"uploaded", "done"}
    ]
    if previous_draft and candidate_invoice_ids:
        reextracted_invoice_ids = _reextract_done_invoices_with_draft(
            db,
            session_id=session_id,
            draft=result.draft,
            continue_on_error=True,
        )
        session = crud.get_authoring_session(db, session_id)
    elif candidate_invoice_ids:
        # Every authored draft needs sample output. Plan selection only chooses
        # the extraction strategy: a selected deterministic plan is embedded in
        # the draft above; otherwise extraction safely processes the full PDF.
        concurrency = max(1, int(settings.profile_discovery_invoice_concurrency))
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            batches = list(pool.map(
                lambda invoice_id: _reextract_isolated(session_id, result.draft, invoice_id),
                candidate_invoice_ids,
            ))
        reextracted_invoice_ids = [invoice_id for batch in batches for invoice_id in batch]
        session = crud.get_authoring_session(db, session_id)

    previews = _previews_for_draft(db, session, result.draft)
    validation_summary = _grade_previews(discovery_report, previews)
    if (
        result.selected_plan_id == "deterministic.v1"
        and validation_summary.get("hard_blockers")
        and authored_profile.pop("extraction_plan", None) is not None
    ):
        # Candidate plans are generated evidence, not trusted configuration. If
        # restricting extraction fails validation, rerun once without the plan so
        # the saved draft cannot silently omit pages in production.
        reextracted_invoice_ids.extend(_reextract_done_invoices_with_draft(
            db,
            session_id=session_id,
            draft=result.draft,
            continue_on_error=True,
        ))
        session = crud.get_authoring_session(db, session.id)
        previews = _previews_for_draft(db, session, result.draft)
        validation_summary = _grade_previews(discovery_report, previews)
        warning = "Generated extraction plan failed validation and was removed; full-document extraction is used."
        validation_summary.setdefault("review_warnings", []).append(warning)
        validation_summary.setdefault("unresolved_risks", []).append(warning)
    authored_profile["authoring_evidence"] = validation_summary

    conversation = [*list(session.conversation or []), {"role": "user", "content": user_message}]
    conversation.append({
        "role": "assistant",
        "content": result.assistant_message,
        "open_questions": result.open_questions,
    })

    cost = float(estimate_cost_usd(result.ai_model_name, result.tokens_input, result.tokens_output))
    crud.log_ai_call(
        db, intake_id=f"authoring-{session_id}", job_id=None, call_type="profile_authoring_turn",
        ai_model_name=result.ai_model_name, tokens_input=result.tokens_input,
        tokens_output=result.tokens_output, estimated_cost_usd=cost, duration_ms=result.duration_ms,
    )
    crud.update_authoring_session(
        db, session_id,
        conversation=conversation,
        draft_profile=result.draft,
        previews=previews,
        validation_summary=validation_summary,
        tokens_input=(session.tokens_input or 0) + result.tokens_input,
        tokens_output=(session.tokens_output or 0) + result.tokens_output,
        estimated_cost_usd=float(session.estimated_cost_usd or 0) + cost,
    )
    return {
        "assistant_message": result.assistant_message,
        "open_questions": result.open_questions,
        "confidence": result.confidence,
        "draft_profile": result.draft,
        "previews": previews,
        "reextracted_invoice_ids": reextracted_invoice_ids,
    }


def run_reextraction(db: Session, *, session_id: str, invoice_id: str | None = None) -> dict:
    """Re-run AI extraction on the cached sample PDFs using the current draft profile.

    Unlike a turn (deterministic re-application over the first extraction), this
    re-reads the PDF so refinements that need the model to look again — header
    scalars, fields the first pass missed — take effect. Re-extracts the one
    given invoice, or all extracted samples when ``invoice_id`` is None, then
    re-renders the previews and persists them on the session.
    """
    session = crud.get_authoring_session(db, session_id)
    if session is None:
        raise ValueError("authoring session not found")
    if not session.draft_profile:
        raise ValueError("nothing authored yet — chat with the assistant first")

    reextracted = _reextract_done_invoices_with_draft(
        db, session_id=session_id, draft=session.draft_profile, invoice_id=invoice_id)

    session = crud.get_authoring_session(db, session_id)
    previews = _previews_for_draft(db, session, session.draft_profile)
    crud.update_authoring_session(db, session_id, previews=previews)
    logger.info("authoring.reextract.done", session_id=session_id, count=len(reextracted))
    return {
        "reextracted_invoice_ids": reextracted,
        "draft_profile": session.draft_profile,
        "previews": previews,
        "estimated_cost_usd": float(session.estimated_cost_usd or 0),
    }
