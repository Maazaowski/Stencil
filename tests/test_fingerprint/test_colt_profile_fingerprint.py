"""Colt profile-scoped fingerprint stability across sample PDF folders."""

from __future__ import annotations

from pathlib import Path

import pytest

from stencil.fingerprint.fingerprinter import fingerprint_pdf

_COLT_SAMPLE_ROOT = Path(r"C:\Stencil\Sample Data\Colt")
_COLT_FOLDERS = (
    "5-HLBHFCGL",
    "5-KBGXG31P",
    "0205324441",
    "0205324323",
)


def _discover_pdfs(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.pdf"))


@pytest.fixture()
def colt_profile(corpus_profile):
    profile = corpus_profile("colt.standard")
    assert profile.layout_fingerprint is not None
    return profile


def test_colt_account_variants_share_one_fingerprint(colt_profile):
    samples: list[tuple[str, Path]] = []
    for folder_name in _COLT_FOLDERS:
        pdfs = _discover_pdfs(_COLT_SAMPLE_ROOT / folder_name / "pdf")
        if pdfs:
            samples.append((folder_name, pdfs[0]))
    if len(samples) < len(_COLT_FOLDERS):
        pytest.skip("Colt sample PDFs not found under C:\\Stencil\\Sample Data\\Colt")

    fingerprints = {
        folder: fingerprint_pdf(pdf, profile=colt_profile)[0]
        for folder, pdf in samples
    }
    unique = set(fingerprints.values())
    assert len(unique) == 1, (
        "Expected one profile fingerprint across all Colt account folders; got:\n"
        + "\n".join(f"  {folder}: {fp[:24]}..." for folder, fp in sorted(fingerprints.items()))
    )


def test_colt_standard_layout_pdfs_share_one_fingerprint(colt_profile):
    """All PDFs under standard Colt account folders (*/pdf/) must share one fingerprint."""
    pdfs: list[Path] = []
    for folder_name in _COLT_FOLDERS:
        pdfs.extend(_discover_pdfs(_COLT_SAMPLE_ROOT / folder_name / "pdf"))
    if not pdfs:
        pytest.skip("Colt sample PDFs not found under C:\\Stencil\\Sample Data\\Colt")

    reference_fp, _ = fingerprint_pdf(pdfs[0], profile=colt_profile)
    mismatches = []
    for pdf in pdfs[1:]:
        fp, _ = fingerprint_pdf(pdf, profile=colt_profile)
        if fp != reference_fp:
            mismatches.append(f"{pdf.relative_to(_COLT_SAMPLE_ROOT)} -> {fp[:24]}...")

    assert not mismatches, (
        f"Expected one fingerprint across {len(pdfs)} standard-layout Colt PDFs; "
        f"reference {reference_fp[:24]}...; mismatches:\n  "
        + "\n  ".join(mismatches[:20])
    )
