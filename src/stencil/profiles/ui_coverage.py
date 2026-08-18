"""Declared UI ownership for every persisted SupplierProfile setting.

This manifest is intentionally small and backend-owned so adding a profile field
cannot silently create a JSON-only capability.  Nested generated/workflow roots
are classified as a unit; editable Pydantic blocks are classified per leaf.
"""

from types import UnionType
from typing import get_args, get_origin

from pydantic import BaseModel

from stencil.profiles.schema import SupplierProfile

EDITABLE = "profile_editor"
WORKFLOW = "other_ui_workflow"
READ_ONLY = "generated_read_only"

PROFILE_UI_COVERAGE: dict[str, str] = {
    "profile_id": EDITABLE,
    "version": EDITABLE,
    "status": EDITABLE,
    "layout_description": EDITABLE,
    "identity.canonical_name": EDITABLE,
    "identity.aliases": EDITABLE,
    "classification.output_type": EDITABLE,
    "output_spec_id": EDITABLE,
    "field_schema_id": EDITABLE,
    "field_overrides": EDITABLE,
    "output_mapping_overrides": EDITABLE,
    "delivery": WORKFLOW,
    "notes": EDITABLE,
    "advanced.document_structure.detail_start_marker": EDITABLE,
    "advanced.document_structure.detail_end_marker": EDITABLE,
    "advanced.line_item_hints.subtotal_keywords": EDITABLE,
    "advanced.line_item_hints.tax_keywords": EDITABLE,
    "advanced.line_item_hints.detail_table_anchors": EDITABLE,
    "advanced.line_item_hints.line_item_granularity": EDITABLE,
    "advanced.line_item_hints.service_id_preference": EDITABLE,
    "advanced.line_item_hints.billing_reference_preference": EDITABLE,
    "advanced.line_item_hints.service_id_column_label": EDITABLE,
    "advanced.line_item_hints.billing_reference_column_label": EDITABLE,
    "advanced.line_item_hints.amount_column_label": EDITABLE,
    "advanced.line_item_hints.tax_amount_column_label": EDITABLE,
    "advanced.line_item_hints.amount_source": EDITABLE,
    "advanced.line_item_hints.tax_source": EDITABLE,
    "advanced.line_item_hints.tax_output_mode": EDITABLE,
    "advanced.line_item_hints.tax_rate_source": EDITABLE,
    "advanced.line_item_hints.service_id_value_pattern": EDITABLE,
    "advanced.line_item_hints.billing_reference_value_pattern": EDITABLE,
    "advanced.line_item_hints.skip_row_keywords": EDITABLE,
    "advanced.line_item_hints.table_column_labels": EDITABLE,
    "advanced.currency.default_code": EDITABLE,
    "advanced.currency.allowed_codes": EDITABLE,
    "advanced.currency.aliases": EDITABLE,
    "advanced.extraction_plan": READ_ONLY,
    "advanced.document_field_defaults": EDITABLE,
    "advanced.include_zero_amount_line_items": EDITABLE,
    "advanced.require_line_item_identifier": EDITABLE,
    "layout_fingerprint.summary_anchors": EDITABLE,
    "layout_fingerprint.currency_codes": EDITABLE,
    "layout_fingerprint.ignore_label_patterns": EDITABLE,
    "layout_fingerprint.optional_column_patterns": EDITABLE,
    "layout_fingerprint.exclude_span_patterns": EDITABLE,
    "training_config.min_validation_successes": EDITABLE,
    "training_config.require_reconciliation": EDITABLE,
    "authoring_evidence": READ_ONLY,
    "owner": EDITABLE,
    "created_date": READ_ONLY,
    "last_updated_date": READ_ONLY,
    "audit": READ_ONLY,
}


def _model_type(annotation):
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    origin = get_origin(annotation)
    if origin in (UnionType, type(None)) or origin is not None:
        for arg in get_args(annotation):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                return arg
    return None


def unclassified_profile_ui_paths() -> set[str]:
    """Return persisted profile paths that have no declared UI ownership."""
    missing: set[str] = set()

    def visit(model: type[BaseModel], prefix: str = "") -> None:
        for name, field in model.model_fields.items():
            path = f"{prefix}.{name}" if prefix else name
            if path in PROFILE_UI_COVERAGE:
                continue
            nested = _model_type(field.annotation)
            if nested is None:
                missing.add(path)
            else:
                visit(nested, path)

    visit(SupplierProfile)
    return missing
