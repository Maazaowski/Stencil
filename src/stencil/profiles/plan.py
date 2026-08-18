"""Extraction-plan compatibility and validation helpers."""

from __future__ import annotations

from stencil.profiles.schema import (
    ExtractionPlan,
    RegionRule,
    RowSelectorRule,
    SupplierProfile,
    ValueExpression,
)


def effective_extraction_plan(profile: SupplierProfile) -> ExtractionPlan:
    """Return the persisted v1 plan or compile legacy hints without mutation."""
    if profile.advanced.extraction_plan is not None:
        return profile.advanced.extraction_plan

    structure = profile.document_structure
    hints = profile.line_item_hints
    scope = {
        "per_total_group": "group_footer",
        "per_service_group": "service_block",
    }.get(hints.line_item_granularity or "", "row")
    regions: list[RegionRule] = []
    if structure.detail_start_marker or structure.detail_end_marker:
        regions.append(RegionRule(
            name="legacy_line_items",
            start_markers=_marker_list(structure.detail_start_marker),
            end_markers=_marker_list(structure.detail_end_marker),
            occurrence="all",
            include_start=True,
            include_end=hints.line_item_granularity in {"per_total_group", "per_service_group"},
        ))

    row_rules: dict[str, ValueExpression] = {}
    if hints.service_id_value_pattern:
        row_rules["service_id"] = ValueExpression(
            op="regex_extract",
            pattern=hints.service_id_value_pattern,
            args=[ValueExpression(op="row_text")],
        )
    if hints.billing_reference_preference == "same_as_service_id":
        row_rules["billing_reference"] = ValueExpression(
            op="copy", args=[ValueExpression(op="field", field="service_id")])
    elif hints.billing_reference_value_pattern:
        row_rules["billing_reference"] = ValueExpression(
            op="regex_extract",
            pattern=hints.billing_reference_value_pattern,
            args=[ValueExpression(op="row_text")],
        )
    if hints.amount_column_label:
        amount = ValueExpression(op="row_column", label=hints.amount_column_label)
        if hints.amount_source == "label_amount_minus_tax":
            amount = ValueExpression(op="subtract", args=[
                amount,
                ValueExpression(op="field", field="tax_amount"),
            ])
        row_rules["amount"] = amount
    if hints.tax_amount_column_label:
        row_rules["tax_amount"] = ValueExpression(
            op="row_column", label=hints.tax_amount_column_label)

    return ExtractionPlan(
        document_family=_family(profile.classification.output_type),
        regions=regions,
        row_selector=RowSelectorRule(
            scope=scope,
            include_pattern=hints.service_id_value_pattern,
            exclude_patterns=list(hints.skip_row_keywords or []),
            identifier_pattern=hints.service_id_value_pattern,
        ),
        row_field_rules=row_rules,
    )


def validate_plan_fields(
    plan: ExtractionPlan,
    *,
    document_fields: set[str],
    row_fields: set[str],
) -> list[str]:
    errors: list[str] = []
    unknown_doc = sorted(set(plan.document_field_rules) - document_fields)
    unknown_row = sorted(set(plan.row_field_rules) - row_fields)
    if unknown_doc:
        errors.append("Unknown document field rule(s): " + ", ".join(unknown_doc))
    if unknown_row:
        errors.append("Unknown row field rule(s): " + ", ".join(unknown_row))
    return errors


def _marker_list(value: str | None) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _family(value: str | None) -> str:
    value = str(value or "standard").strip().lower()
    return value if value in {"standard", "wireless", "time_and_material"} else "standard"
