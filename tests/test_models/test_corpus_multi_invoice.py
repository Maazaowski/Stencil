"""Suite 3b — Multi-invoice authoring generalizes where single-invoice fails.

AI-dependent (needs an OpenAI key), opt-in via `pytest -m authoring`.

Concrete, reproducible criterion (euNetworks corpus): a model authored from ONE
invoice misses the one-off "Per Task / Remote Hands" rows on IE-SI54403, but a
model authored from a SET that includes IE-SI54403 reproduces every invoice in
the layout. AI extraction is the ground-truth oracle for each invoice.
"""

from __future__ import annotations

import pytest

from stencil.config import settings
from stencil.fingerprint.fingerprinter import compute_layout_family_key, fingerprint_pdf
from stencil.models.authoring import AuthoringExample, author_extraction_model
from stencil.models.diff import diff_invoices
from stencil.models.interpreter import execute_model
from stencil.models.training import _normalize_line_items
from stencil.profiles import loader as profiles_loader
from tests.corpus_utils import discover_layouts
from tests.test_models.test_corpus_authoring import _ai_ground_truth

pytestmark = pytest.mark.authoring

LAYOUT_ID = "eunetworks.standard"
PRIMARY_STEM = "A837737 - IE-SI53564"   # 4-page, richest
ONEOFF_STEM = "A837737 - IE-SI54403"    # has one-off rows without billing periods


@pytest.fixture(autouse=True)
def _require_openai_key():
    if not settings.openai_api_key:
        pytest.skip("authoring suite needs ST_OPENAI_API_KEY")


@pytest.fixture
def eunetworks_layout():
    layout = next((lo for lo in discover_layouts() if lo.layout_id == LAYOUT_ID), None)
    if layout is None or not layout.profile_path.exists():
        pytest.skip(f"{LAYOUT_ID} corpus fixture not present")
    return layout


def _example(layout, profile, pdf, fingerprint) -> AuthoringExample:
    invoice, document = _ai_ground_truth(layout, profile, pdf, fingerprint)
    from stencil.extraction.evidence import build_model_authoring_evidence
    from stencil.extraction.layout import render_layout_text

    return AuthoringExample(
        page_texts=render_layout_text(document),
        layout_evidence=build_model_authoring_evidence(document, invoice),
        ai_invoice=invoice,
        intake_id=pdf.stem,
    )


def test_adding_failing_invoices_to_the_set_improves_coverage(eunetworks_layout):
    """Mirror the product loop: author from one invoice, find what it fails,
    add those to the training set, re-author, and prove the previously-failing
    invoices are now covered and total coverage strictly improves."""
    layout = eunetworks_layout
    profile = layout.load_profile()
    profiles_loader._profiles_cache[profile.profile_id] = profile
    try:
        primary_pdf = next(p for p in layout.pdfs if p.stem == PRIMARY_STEM)
        fingerprint, features = fingerprint_pdf(primary_pdf)
        family = compute_layout_family_key(
            features, supplier=profile.identity.canonical_name,
            output_type=profile.classification.output_type,
        )

        # Ground truth for every invoice (the oracle).
        examples = {p.stem: _example(layout, profile, p, fingerprint) for p in layout.pdfs}
        cap = max(settings.model_training_max_set_size, 1)

        def author(set_stems):
            primary = examples[set_stems[0]]
            extras = [examples[s] for s in set_stems[1:]]
            return author_extraction_model(
                page_texts=primary.page_texts, layout_evidence=primary.layout_evidence,
                ai_invoice=primary.ai_invoice, profile=profile, fingerprint=fingerprint,
                layout_family_key=family, intake_id=primary.intake_id, extra_examples=extras,
            ).model

        def covered(model) -> set[str]:
            ok = set()
            for stem, ex in examples.items():
                try:
                    out = execute_model(model, None, stem, document=ex.document)
                except Exception:  # noqa: BLE001
                    continue
                _normalize_line_items(out, keep_zero_amount=profile.include_zero_amount_line_items)
                if diff_invoices(ex.ai_invoice, out).is_match:
                    ok.add(stem)
            return ok

        # 1. Single-invoice model: measure its coverage and what it misses.
        single = author([PRIMARY_STEM])
        single_cov = covered(single)
        misses = [s for s in examples if s not in single_cov]

        # 2. Add the (capped) missed invoices to the set and re-author.
        set_stems = [PRIMARY_STEM] + [s for s in misses if s != PRIMARY_STEM][: cap - 1]
        retrained = author(set_stems)
        retrained_cov = covered(retrained)

        # The invoices we added to the training set must now be reproduced...
        still_failing = [s for s in set_stems if s not in retrained_cov]
        assert not still_failing, (
            "re-authored model does not reproduce its own training set: " + ", ".join(still_failing)
        )
        # ...and overall coverage must strictly improve.
        assert len(retrained_cov) > len(single_cov), (
            f"coverage did not improve: single={len(single_cov)}/9 set={len(retrained_cov)}/9"
        )
    finally:
        profiles_loader._profiles_cache.pop(profile.profile_id, None)
