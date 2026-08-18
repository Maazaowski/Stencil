"""Suite 3 — Model authoring end-to-end across the golden corpus.

AI-dependent (needs an OpenAI key), opt-in via `pytest -m authoring`, run on
demand / nightly — NOT on every commit:

- For each layout: AI-extract the first invoice -> author a declarative model
  through the grounded refine loop -> execute the authored model on ALL
  invoices in the folder -> compare to the expected outputs.
- On success the authored model is written to the layout's model.json so the
  Suite 2 snapshot can be refreshed (review + commit the diff).

This proves the generator GENERALIZES; Suites 1-2 prove nothing regressed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stencil.config import settings
from stencil.extraction.evidence import build_model_authoring_evidence
from stencil.extraction.extractor import build_canonical_invoice, extract_invoice
from stencil.extraction.layout import extract_layout_document, render_layout_text
from stencil.extraction.normalization import apply_layout_profile_hints
from stencil.fingerprint.fingerprinter import compute_layout_family_key, fingerprint_pdf
from stencil.models.authoring import author_extraction_model
from stencil.models.diff import diff_invoices
from stencil.models.interpreter import execute_model
from stencil.models.training import _normalize_line_items
from stencil.output.xlsx_writer import build_output_rows
from stencil.profiles import loader as profiles_loader
from tests.corpus_utils import CorpusLayout, layout_params
from tests.test_models.test_corpus_extraction import _compare_to_summary, _compare_to_xlsx

pytestmark = pytest.mark.authoring


@pytest.fixture(autouse=True)
def _require_openai_key():
    if not settings.openai_api_key:
        pytest.skip("authoring suite needs ST_OPENAI_API_KEY")


@pytest.fixture
def corpus_profile_registered():
    """Register a corpus profile in the loader cache so AI extraction sees its hints."""
    registered: list[str] = []

    def _register(profile):
        profiles_loader._profiles_cache[profile.profile_id] = profile
        registered.append(profile.profile_id)
        return profile

    yield _register
    for profile_id in registered:
        profiles_loader._profiles_cache.pop(profile_id, None)


def _ai_ground_truth(layout: CorpusLayout, profile, pdf: Path, fingerprint: str):
    """AI-extract one training invoice exactly like the training pipeline does."""
    extraction_result = extract_invoice(
        pdf,
        supplier_name=profile.identity.canonical_name,
        output_type=profile.classification.output_type,
        supplier_profile_id=profile.profile_id,
    )
    invoice = build_canonical_invoice(
        raw_data=extraction_result.raw_data,
        intake_id=f"corpus-authoring-{pdf.stem}",
        extraction_result=extraction_result,
        output_type=profile.classification.output_type,
        supplier_profile_id=profile.profile_id,
        layout_fingerprint=fingerprint,
    )
    document = extract_layout_document(pdf)
    apply_layout_profile_hints(invoice, document, profile, profile.line_item_hints)
    _normalize_line_items(invoice, keep_zero_amount=profile.include_zero_amount_line_items)
    return invoice, document


@pytest.mark.parametrize("layout", layout_params())
def test_authoring_generalizes_across_layout(layout: CorpusLayout, corpus_profile_registered):
    if not layout.profile_path.exists():
        pytest.skip(f"{layout.layout_id}: no profile.json in corpus fixture")

    profile = corpus_profile_registered(layout.load_profile())
    training_pdf = layout.pdfs[0]
    fingerprint, features = fingerprint_pdf(training_pdf)
    layout_family_key = compute_layout_family_key(
        features,
        supplier=profile.identity.canonical_name,
        output_type=profile.classification.output_type,
    )

    # 1. AI ground truth for the training invoice
    ai_invoice, document = _ai_ground_truth(layout, profile, training_pdf, fingerprint)
    assert ai_invoice.line_items, "AI extraction returned no line items"

    # 2. Grounded authoring loop: author -> execute -> diff -> refine
    page_texts = render_layout_text(document)
    layout_evidence = build_model_authoring_evidence(document, ai_invoice)
    max_attempts = max(settings.model_authoring_max_attempts, 1)

    feedback = None
    model = None
    last_diff = None
    for attempt in range(1, max_attempts + 1):
        auth = author_extraction_model(
            page_texts=page_texts,
            layout_evidence=layout_evidence,
            ai_invoice=ai_invoice,
            profile=profile,
            fingerprint=fingerprint,
            layout_family_key=layout_family_key,
            intake_id=f"corpus-authoring-{training_pdf.stem}",
            feedback=feedback,
        )
        model = auth.model
        try:
            model_invoice = execute_model(model, training_pdf, ai_invoice.intake_id, document=document)
        except Exception as exc:
            feedback = f"EXECUTION ERROR: the rules failed to run: {exc}"
            last_diff = None
            continue
        _normalize_line_items(model_invoice, keep_zero_amount=profile.include_zero_amount_line_items)
        last_diff = diff_invoices(ai_invoice, model_invoice)
        if last_diff.is_match:
            break
        feedback = last_diff.feedback()

    assert model is not None, "authoring produced no model"
    assert last_diff is not None and last_diff.is_match, (
        f"{layout.layout_id}: authoring did not converge after {max_attempts} attempt(s):\n"
        + (last_diff.summary() if last_diff else feedback or "unknown error")
    )

    # 3. The authored model must generalize to EVERY invoice in the layout
    summary = (
        layout.load_summary_expectations().get("invoices", {})
        if layout.summary_expectations_path.exists()
        else {}
    )
    failures: list[str] = []
    for pdf in layout.pdfs:
        invoice = execute_model(model, pdf, intake_id=f"corpus-{pdf.stem}")
        _normalize_line_items(invoice, keep_zero_amount=profile.include_zero_amount_line_items)
        rows = build_output_rows(invoice)
        if layout.expected_xlsx_for(pdf).exists():
            errors = _compare_to_xlsx(layout, pdf, rows)
        elif pdf.name in summary:
            errors = _compare_to_summary(summary[pdf.name], rows)
        else:
            continue
        if errors:
            failures.append(f"{pdf.name}:\n  " + "\n  ".join(errors))

    assert not failures, (
        f"Layout '{layout.layout_id}': authored model does not generalize:\n" + "\n".join(failures)
    )

    # 4. Refresh the committed snapshot for Suite 2 (review + commit the diff)
    layout.model_path.write_text(
        json.dumps(model.model_dump(mode="json"), indent=2, default=str) + "\n",
        encoding="utf-8",
    )
