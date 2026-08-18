"""Suite 1 — Fingerprint correctness across the golden corpus.

Deterministic, no AI, runs on every change:
- Stability: every PDF in the same {supplier}.{layout} folder computes the
  SAME layout_fingerprint (this is what makes production routing safe).
- Uniqueness: PDFs from different layout folders compute DIFFERENT
  fingerprints (no cross-layout collisions).
- Determinism: fingerprinting the same PDF twice yields an identical hash.

Failures print a feature-level diff (which labels/headers/font sizes
differed) so fingerprint tuning is debuggable.
"""

from __future__ import annotations

import pytest

from stencil.fingerprint.fingerprinter import (
    LayoutFeatures,
    _legacy_structural_tokens,
    fingerprint_pdf,
)
from tests.corpus_utils import discover_layouts, layout_params, pdf_params


def _feature_diff(name_a: str, a: LayoutFeatures, name_b: str, b: LayoutFeatures) -> str:
    """Human-readable diff of the structural features that feed the hash."""
    lines = []
    for label, values_a, values_b in [
        ("labels", _legacy_structural_tokens(a.first_page_labels), _legacy_structural_tokens(b.first_page_labels)),
        (
            "table_headers",
            _legacy_structural_tokens(a.table_header_texts),
            _legacy_structural_tokens(b.table_header_texts),
        ),
        ("font_sizes", a.font_sizes_used, b.font_sizes_used),
    ]:
        only_a = sorted(set(values_a) - set(values_b))
        only_b = sorted(set(values_b) - set(values_a))
        if only_a:
            lines.append(f"  {label} only in {name_a}: {only_a}")
        if only_b:
            lines.append(f"  {label} only in {name_b}: {only_b}")
    return "\n".join(lines) or "  (no structural feature differences found)"


@pytest.mark.parametrize("layout", layout_params())
def test_fingerprint_stability_within_layout(layout):
    """Every invoice of the same layout must route to the same model."""
    reference_pdf = layout.pdfs[0]
    reference_fp, reference_features = fingerprint_pdf(reference_pdf)

    failures = []
    for pdf in layout.pdfs[1:]:
        fp, features = fingerprint_pdf(pdf)
        if fp != reference_fp:
            failures.append(
                f"{pdf.name} -> {fp}\n"
                + _feature_diff(reference_pdf.name, reference_features, pdf.name, features)
            )

    assert not failures, (
        f"Layout '{layout.layout_id}': fingerprints differ from {reference_pdf.name} ({reference_fp}):\n"
        + "\n".join(failures)
    )


def test_fingerprint_uniqueness_across_layouts():
    """Different layouts must never collide (a collision would route the wrong model)."""
    layouts = discover_layouts()
    if len(layouts) < 2:
        pytest.skip("uniqueness needs at least two corpus layouts")

    fingerprints: dict[str, str] = {}
    for layout in layouts:
        fp, _ = fingerprint_pdf(layout.pdfs[0])
        fingerprints[layout.layout_id] = fp

    seen: dict[str, str] = {}
    for layout_id, fp in fingerprints.items():
        assert fp not in seen, (
            f"Fingerprint collision between layouts '{seen[fp]}' and '{layout_id}': {fp}"
        )
        seen[fp] = layout_id


@pytest.mark.parametrize("layout_pdf", pdf_params())
def test_fingerprint_determinism(layout_pdf):
    """Fingerprinting the same PDF twice yields an identical hash."""
    _, pdf = layout_pdf
    fp1, _ = fingerprint_pdf(pdf)
    fp2, _ = fingerprint_pdf(pdf)
    assert fp1 == fp2
