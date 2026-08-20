"""Read a little of a long document, then replay rules over all of it.

The problem
-----------
Full AI extraction of a 656-page invoice in the eval corpus sends 2,871,958
characters across 123 sequential calls, costs about $1.58, takes six minutes --
and returned between 0 and 384 rows across four runs of the identical file. It is
not merely slow and expensive; it is *unstable*, and an unstable answer on a
document nobody can check by hand is worse than no answer.

The observation
---------------
Structure is small; data is large. That document's entire 384-row deliverable
comes from 13 of its 656 pages, and the rules describing those rows are a few
hundred characters of JSON. So ask the model for the rules, not the data:

    sample ~12 pages  ->  AI extracts just those  ->  author rules from them
                      ->  interpreter replays the rules over all 656 pages

Grounding without a ground truth
--------------------------------
The ordinary authoring loop (``models/training``) grounds itself by diffing
authored rules against a full AI extraction of the same invoice. That is exactly
what we cannot afford here, and on a long document it is not trustworthy anyway.

The substitute is the document's own arithmetic: rules are accepted only if the
rows they produce across *every* page reconcile against the totals the document
states about itself. A model that silently drops page 400 fails that check,
because the sum no longer matches. Internal consistency replaces an external
answer key.

Failure is cheap and always safe: when anything does not hold, the caller falls
back to ordinary full extraction, so this path can only ever save work, never
lose data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

from stencil.config import settings
from stencil.extraction.layout import extract_layout_document, pdf_page_count
from stencil.extraction.page_roles import DocumentPageMap, classify_pages
from stencil.models.schema import ExtractionModel
from stencil.profiles.schema import SupplierProfile
from stencil.validation.schema import CanonicalInvoice

logger = structlog.get_logger()


@dataclass
class SampleAuthoringOutcome:
    """What the sample pass produced, and why it stopped where it did."""

    status: str
    """``authored`` (usable), ``rejected`` (verification failed), or ``skipped``."""

    reason: str = ""
    invoice: CanonicalInvoice | None = None
    model: ExtractionModel | None = None
    page_map: DocumentPageMap | None = None
    sample_pages: list[int] = field(default_factory=list)
    tokens_input: int = 0
    tokens_output: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.status == "authored" and self.invoice is not None


def should_author_from_sample(
    page_count: int,
    profile: SupplierProfile | None,
    *,
    enabled: bool | None = None,
    min_pages: int | None = None,
) -> bool:
    """Is this document worth authoring rules for instead of reading in full?

    Deliberately narrow. A short document is cheap and reliable to extract
    directly, and a document with no profile has no home to save a model into --
    in both cases ordinary extraction is the right answer.
    """
    if not (settings.sample_authoring_enabled if enabled is None else enabled):
        return False
    if profile is None:
        return False
    threshold = settings.sample_authoring_min_pages if min_pages is None else min_pages
    return threshold > 0 and page_count >= threshold


def plan_sample(
    pdf_path: Path,
    *,
    max_sample_pages: int | None = None,
    document=None,
) -> tuple[DocumentPageMap, list[int]]:
    """Classify the document and choose the pages the AI pass will read.

    Deterministic and AI-free: the choice is made from cell geometry, so the
    same document always yields the same sample.
    """
    if document is None:
        document = extract_layout_document(pdf_path, include_markdown=False)
    page_map = classify_pages(document)
    limit = (
        settings.sample_authoring_max_sample_pages
        if max_sample_pages is None
        else max_sample_pages
    )
    return page_map, page_map.sample_pages(max_pages=limit)


def verify_against_stated_totals(
    invoice: CanonicalInvoice,
    *,
    max_variance: float | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Do the replayed rows agree with what the document says about itself?

    This is the whole safety argument for skipping most of the pages. A rule set
    that misses a page produces a sum that no longer matches the stated total, so
    silent loss -- the failure mode that makes long documents dangerous -- cannot
    pass unnoticed.

    Returns ``(ok, reason, metrics)``. A document that states no total is *not*
    ok: unverifiable is not the same as verified, and here we are deciding
    whether to skip reading it at all.
    """
    from stencil.fields.loader import default_field_schema
    from stencil.validation.reconciler import reconcile

    limit = settings.sample_authoring_max_variance if max_variance is None else max_variance

    if not invoice.rows:
        return False, "replay produced no rows", {"row_count": 0}

    recon = reconcile(invoice, default_field_schema())
    metrics: dict[str, Any] = {"row_count": len(invoice.rows)}
    if recon is None:
        return False, "document states no total to verify against", metrics

    metrics.update(
        line_items_sum=str(recon.line_items_sum),
        stated_total=str(recon.stated_total) if recon.stated_total is not None else None,
        variance=str(recon.variance),
        variance_pct=recon.variance_pct,
    )
    if recon.stated_total is None:
        return False, "document states no total to verify against", metrics
    if abs(recon.variance_pct) > limit:
        return False, (
            f"replayed rows are off by {recon.variance_pct:.3%}, "
            f"above the {limit:.3%} the sample path requires"
        ), metrics
    return True, "reconciled against stated totals", metrics


def replay_and_verify(
    model: ExtractionModel,
    pdf_path: Path,
    intake_id: str,
    *,
    document=None,
    max_variance: float | None = None,
) -> SampleAuthoringOutcome:
    """Run an existing rule set over the whole document and check the arithmetic.

    Cheaper than authoring and far cheaper than reading every page, so it is the
    first thing to try when a rule set already exists for this layout -- even an
    unapproved one, because the totals check is what decides whether to trust it,
    not its approval status.
    """
    from stencil.models.interpreter import ModelExecutionError, execute_model

    try:
        invoice = execute_model(
            model, pdf_path, intake_id, document=document, skip_self_checks=True,
        )
    except ModelExecutionError as exc:
        return SampleAuthoringOutcome(
            status="rejected", reason=f"replay failed: {exc}", model=model,
        )

    ok, reason, metrics = verify_against_stated_totals(invoice, max_variance=max_variance)
    return SampleAuthoringOutcome(
        status="authored" if ok else "rejected",
        reason=reason, invoice=invoice if ok else None, model=model, metrics=metrics,
    )


def author_from_sample(
    db,
    *,
    intake_id: str,
    pdf_path: Path,
    profile: SupplierProfile,
    fingerprint: str,
    layout_family_key: str | None = None,
    field_schema=None,
    artifact_dir: Path | None = None,
) -> SampleAuthoringOutcome:
    """Author rules from a page sample and replay them over the whole document.

    Never raises for an ordinary failure: every unhappy path returns an outcome
    whose ``usable`` is False, because the caller's fallback (full extraction) is
    always correct and must not be skipped because of a bug here.
    """
    from stencil.extraction.evidence import build_model_authoring_evidence
    from stencil.extraction.extractor import build_extracted_document, extract_invoice
    from stencil.fields.loader import resolve_merged_field_schema
    from stencil.models.authoring import author_extraction_model
    from stencil.models.interpreter import ModelExecutionError, execute_model

    page_count = pdf_page_count(pdf_path)
    schema = field_schema or resolve_merged_field_schema(profile)

    try:
        document = extract_layout_document(pdf_path, include_markdown=False)
        page_map, sample_pages = plan_sample(pdf_path, document=document)
    except Exception as exc:  # pragma: no cover - defensive
        return SampleAuthoringOutcome(status="skipped", reason=f"layout failed: {exc}")

    if not sample_pages:
        return SampleAuthoringOutcome(
            status="skipped", reason="no pages selected", page_map=page_map,
        )

    log = logger.bind(intake_id=intake_id, pages=page_count, sample=len(sample_pages))
    log.info("sample_authoring.started", sample_pages=sample_pages)

    # 1. AI reads only the sample. This is the one paid step.
    try:
        result = extract_invoice(
            pdf_path,
            supplier_name=profile.identity.canonical_name,
            output_type=profile.classification.output_type,
            field_schema=schema,
            supplier_profile=profile,
            artifact_dir=artifact_dir,
            page_numbers=sample_pages,
        )
        sample_invoice = build_extracted_document(
            result.raw_data,
            intake_id=intake_id,
            extraction_result=result,
            output_type=profile.classification.output_type,
            field_schema=schema,
        )
    except Exception as exc:
        log.warning("sample_authoring.sample_extraction_failed", error=str(exc))
        return SampleAuthoringOutcome(
            status="skipped", reason=f"sample extraction failed: {exc}",
            page_map=page_map, sample_pages=sample_pages,
        )

    tokens_in, tokens_out = result.tokens_input, result.tokens_output

    # 2. Author rules from the sample. The sample layout is what the author sees,
    #    so its row ids line up with the ground truth extracted from it.
    try:
        sample_document = extract_layout_document(
            pdf_path, include_markdown=False, page_numbers=sample_pages,
        )
        from stencil.extraction.layout import render_layout_text

        authored = author_extraction_model(
            page_texts=render_layout_text(sample_document),
            page_priority=list(sample_pages),
            layout_evidence=build_model_authoring_evidence(
                sample_document, sample_invoice,
                max_rows=settings.model_authoring_sample_rows,
            ),
            ai_invoice=sample_invoice,
            profile=profile,
            fingerprint=fingerprint,
            layout_family_key=layout_family_key,
            intake_id=intake_id,
        )
    except Exception as exc:
        log.warning("sample_authoring.authoring_failed", error=str(exc))
        return SampleAuthoringOutcome(
            status="skipped", reason=f"authoring failed: {exc}",
            page_map=page_map, sample_pages=sample_pages,
            tokens_input=tokens_in, tokens_output=tokens_out,
        )

    tokens_in += authored.tokens_input
    tokens_out += authored.tokens_output

    # 3. Replay over EVERY page, deterministically and for free.
    try:
        invoice = execute_model(
            authored.model, pdf_path, intake_id, document=document, skip_self_checks=True,
        )
    except ModelExecutionError as exc:
        log.warning("sample_authoring.replay_failed", error=str(exc))
        return SampleAuthoringOutcome(
            status="rejected", reason=f"replay failed: {exc}",
            model=authored.model, page_map=page_map, sample_pages=sample_pages,
            tokens_input=tokens_in, tokens_output=tokens_out,
        )

    # 4. Verify against the document's own arithmetic, not against an AI answer.
    ok, reason, metrics = verify_against_stated_totals(invoice)
    metrics.update(
        page_count=page_count,
        sample_page_count=len(sample_pages),
        sampled_fraction=round(len(sample_pages) / page_count, 4) if page_count else None,
        sample_row_count=len(sample_invoice.rows),
    )
    if not ok:
        log.info("sample_authoring.rejected", reason=reason, **_loggable(metrics))
        return SampleAuthoringOutcome(
            status="rejected", reason=reason, model=authored.model, invoice=invoice,
            page_map=page_map, sample_pages=sample_pages,
            tokens_input=tokens_in, tokens_output=tokens_out, metrics=metrics,
        )

    invoice.warnings.append(
        f"Rules authored from {len(sample_pages)} of {page_count} pages and replayed "
        f"across the whole document; {len(invoice.rows)} rows reconcile to the stated total."
    )
    log.info("sample_authoring.authored", **_loggable(metrics))
    return SampleAuthoringOutcome(
        status="authored", reason=reason, invoice=invoice, model=authored.model,
        page_map=page_map, sample_pages=sample_pages,
        tokens_input=tokens_in, tokens_output=tokens_out, metrics=metrics,
    )


def _loggable(metrics: dict[str, Any]) -> dict[str, Any]:
    """structlog renders Decimals poorly; keep the log line flat and printable."""
    return {
        key: (str(value) if isinstance(value, Decimal) else value)
        for key, value in metrics.items()
        if value is not None
    }
