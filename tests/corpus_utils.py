"""Golden corpus discovery and loading helpers.

The corpus lives in tests/fixtures/corpus/. Each subfolder is one supplier
layout ({supplier}.{layout}) containing:

    profile.json                supplier profile hints used for this layout
    invoices/*.pdf              2+ PDFs sharing the SAME layout
    expected/*.xlsx             the CORRECT output for each PDF (same stem)
    expected_line_items.json    optional summary expectations (count/total/ids)
    model.json                  committed snapshot of the authored ExtractionModel

Suites skip gracefully when a piece is missing (e.g. no model.json yet), so
fixtures can be added incrementally as the user supplies invoices + outputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from stencil.evals.dataset import read_expected_xlsx_rows  # noqa: F401  (re-exported for tests)

CORPUS_ROOT = Path(__file__).resolve().parent / "fixtures" / "corpus"


@dataclass
class CorpusLayout:
    """One supplier layout folder of the golden corpus."""

    layout_id: str  # folder name, e.g. "colt.standard"
    root: Path
    pdfs: list[Path] = field(default_factory=list)

    @property
    def profile_path(self) -> Path:
        return self.root / "profile.json"

    @property
    def model_path(self) -> Path:
        return self.root / "model.json"

    @property
    def summary_expectations_path(self) -> Path:
        return self.root / "expected_line_items.json"

    def expected_xlsx_for(self, pdf: Path) -> Path:
        return self.root / "expected" / f"{pdf.stem}.xlsx"

    def load_profile(self):
        from stencil.profiles.schema import SupplierProfile

        return SupplierProfile.model_validate_json(self.profile_path.read_text(encoding="utf-8"))

    def load_model(self):
        from stencil.models.schema import ExtractionModel

        return ExtractionModel.model_validate_json(self.model_path.read_text(encoding="utf-8"))

    def load_summary_expectations(self) -> dict:
        return json.loads(self.summary_expectations_path.read_text(encoding="utf-8"))


def load_corpus_profile(name: str):
    """Load a corpus profile snapshot (with schema migration) by layout folder name.

    Tests use this instead of ``get_profile(...)`` because the live
    ``supplier_profiles/`` registry holds only client data (not committed), so it
    seeds zero profiles in CI. The corpus snapshots are the canonical fixtures.
    """
    from stencil.profiles.migrate import migrate_profile_dict
    from stencil.profiles.schema import SupplierProfile

    path = CORPUS_ROOT / name / "profile.json"
    return SupplierProfile.model_validate(migrate_profile_dict(json.loads(path.read_text(encoding="utf-8"))))


def discover_layouts() -> list[CorpusLayout]:
    """Find every corpus layout folder that has at least one invoice PDF."""
    layouts: list[CorpusLayout] = []
    if not CORPUS_ROOT.is_dir():
        return layouts
    for folder in sorted(CORPUS_ROOT.iterdir()):
        invoices_dir = folder / "invoices"
        if not invoices_dir.is_dir():
            continue
        pdfs = sorted(invoices_dir.glob("*.pdf"))
        if pdfs:
            layouts.append(CorpusLayout(layout_id=folder.name, root=folder, pdfs=pdfs))
    return layouts


def layout_params() -> list:
    """pytest.param list (one per layout) with readable ids."""
    import pytest

    layouts = discover_layouts()
    if not layouts:
        return [pytest.param(None, id="no-corpus", marks=pytest.mark.skip(reason="no corpus fixtures found"))]
    return [pytest.param(layout, id=layout.layout_id) for layout in layouts]


def pdf_params() -> list:
    """pytest.param list (one per layout+pdf pair) with readable ids."""
    import pytest

    params = []
    for layout in discover_layouts():
        for pdf in layout.pdfs:
            params.append(pytest.param((layout, pdf), id=f"{layout.layout_id}/{pdf.name}"))
    if not params:
        return [pytest.param(None, id="no-corpus", marks=pytest.mark.skip(reason="no corpus fixtures found"))]
    return params


# read_expected_xlsx_rows is re-exported from stencil.evals.dataset (see import above).
