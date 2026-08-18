"""Write canonical invoice JSON and manifest files for the completed package."""

import json
from datetime import datetime
from pathlib import Path

from stencil.output.formatting import format_deliverable_date
from stencil.validation.schema import ExtractedDocument

# Backward-compatible alias
CanonicalInvoice = ExtractedDocument


def write_canonical_json(invoice: ExtractedDocument, output_path: Path) -> Path:
    """Write the canonical invoice JSON (v2 ExtractedDocument shape).

    Dates are stored as ISO YYYY-MM-DD so ground truth reloads safely through
    strict validation. Deliverable MM/DD/YYYY formatting is applied only at
    export boundaries (XLSX, manifest, review artifacts).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(invoice.model_dump_json(indent=2), encoding="utf-8")
    return output_path


def write_extraction_log(invoice: ExtractedDocument, output_path: Path) -> Path:
    """Write an extraction log with metadata about the extraction process."""
    log_data = {
        "intake_id": invoice.intake_id,
        "extraction_path": invoice.metadata.extraction_path,
        "model_id": invoice.metadata.model_id,
        "ai_model_name": invoice.metadata.ai_model_name,
        "supplier": invoice.header.supplier_name,
        "invoice_number": invoice.header.invoice_number,
        "line_item_count": len(invoice.line_items),
        "overall_confidence": invoice.metadata.overall_confidence,
        "tokens_input": invoice.metadata.tokens_input,
        "tokens_output": invoice.metadata.tokens_output,
        "estimated_cost_usd": str(invoice.metadata.estimated_cost_usd),
        "extraction_duration_ms": invoice.metadata.extraction_duration_ms,
        "warnings": invoice.warnings,
        "exceptions": invoice.exceptions,
        "timestamp": datetime.now().isoformat(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(log_data, indent=2, default=str),
        encoding="utf-8",
    )
    return output_path


def write_manifest(intake_id: str, output_dir: Path, invoice: ExtractedDocument,
                   xlsx_path: Path, json_path: Path, log_path: Path) -> Path:
    """Write manifest.json LAST — this is the ready signal for Temforce Bot Agent."""
    manifest = {
        "intake_id": intake_id,
        "supplier": invoice.header.supplier_name,
        "invoice_number": invoice.header.invoice_number,
        "invoice_date": format_deliverable_date(invoice.header.invoice_date),
        "total_due": str(invoice.total_due) if invoice.total_due else None,
        "line_item_count": len(invoice.line_items),
        "output_type": invoice.output_type,
        "extraction_path": invoice.metadata.extraction_path,
        "overall_confidence": invoice.metadata.overall_confidence,
        "is_reconciled": (invoice.reconciliation.is_reconciled
                          if invoice.reconciliation else None),
        "reconciliation_status": (
            invoice.reconciliation.verification_status if invoice.reconciliation else None
        ),
        "files": {
            "xlsx": str(xlsx_path.name),
            "canonical_json": str(json_path.name),
            "extraction_log": str(log_path.name),
        },
        "created_at": datetime.now().isoformat(),
    }

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, default=str),
        encoding="utf-8",
    )
    return manifest_path
