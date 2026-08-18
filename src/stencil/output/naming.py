"""Output file naming helpers."""

from __future__ import annotations

import re
from pathlib import Path

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"|?*\\]')


def derive_output_xlsx_name(original_filename: str) -> str:
    """Derive the Temforce XLSX filename from the source PDF name.

    Examples:
        Colt_Invoice.pdf -> Colt_Invoice.xlsx
        path/to/invoice.PDF -> invoice.xlsx
    """
    base = Path(original_filename).name.strip()
    if not base:
        return "invoice_output.xlsx"

    stem = Path(base).stem
    if not stem:
        stem = "invoice_output"

    safe_stem = _INVALID_FILENAME_CHARS.sub("_", stem).strip(" .")
    if not safe_stem:
        safe_stem = "invoice_output"

    return f"{safe_stem}.xlsx"
