"""Safe deterministic execution of a profile ExtractionPlan."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from stencil.extraction.layout import LayoutDocument, PDFTextScan, VisualRow
from stencil.fields.coercion import parse_date_value
from stencil.profiles.schema import ExtractionPlan, RegionRule, ValueExpression


@dataclass
class PlanExecutionResult:
    document_fields: dict[str, Any] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)
    selected_page_numbers: list[int] = field(default_factory=list)
    reconciliation_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Context:
    all_rows: list[VisualRow]
    row: VisualRow | None
    fields: dict[str, Any]
    row_values: dict[str, Any]
    column_positions: dict[str, float]


def resolve_plan_page_numbers(plan: ExtractionPlan, scan: PDFTextScan) -> list[int]:
    """Resolve line-item regions from cheap flat page text before layout parsing."""
    regions = [region for region in plan.regions if region.role == "line_items"]
    if not regions:
        return list(range(1, scan.page_count + 1))
    selected: set[int] = set()
    for region in regions:
        in_region = not region.start_markers
        start_page: int | None = None
        closed = False
        for page in scan.pages:
            text = page.text
            if closed and region.occurrence == "first":
                break
            if region.start_markers and _marker_hit(region.start_markers, text):
                in_region = True
                start_page = page.page_number
                if region.include_start:
                    selected.add(page.page_number)
            if not in_region:
                continue
            if start_page is None:
                start_page = page.page_number
            if region.max_pages and page.page_number - start_page + 1 > region.max_pages:
                in_region = False
                closed = True
                continue
            end_hit = bool(region.end_markers and _marker_hit(region.end_markers, text))
            if not end_hit or region.include_end:
                selected.add(page.page_number)
            if end_hit:
                in_region = False
                closed = True
    return sorted(selected)


def execute_extraction_plan(
    plan: ExtractionPlan,
    layout: LayoutDocument,
    *,
    initial_fields: dict[str, Any] | None = None,
) -> PlanExecutionResult:
    all_rows = [row for page in layout.pages for row in page.visual_rows]
    selected = select_plan_rows(plan, all_rows)
    positions = _column_positions(selected, plan)
    result = PlanExecutionResult(
        document_fields=dict(initial_fields or {}),
        selected_page_numbers=sorted({row.page for row in selected}),
    )

    document_context = _Context(all_rows, None, result.document_fields, {}, positions)
    for name, expression in plan.document_field_rules.items():
        value = evaluate_expression(expression, document_context)
        if value is not None:
            result.document_fields[name] = value

    for source_row in _selector_source_rows(selected, plan):
        if not _row_selected(source_row, plan):
            continue
        values: dict[str, Any] = {}
        context = _Context(all_rows, source_row, result.document_fields, values, positions)
        for name, expression in plan.row_field_rules.items():
            value = evaluate_expression(expression, context)
            if value is not None:
                values[name] = value
        if not values:
            continue
        values.setdefault("line_number", len(result.rows) + 1)
        values.setdefault("description", _clean_row_text(source_row.text))
        values.setdefault("source_page", source_row.page)
        values.setdefault("source_row_id", source_row.row_id)
        result.rows.append(values)
        result.reconciliation_failures.extend(_row_reconciliation_failures(plan, context, len(result.rows)))
    result.document_fields.setdefault(
        "line_items_amount_sum",
        sum((_decimal(row.get("amount")) or Decimal("0") for row in result.rows), Decimal("0")),
    )
    result.document_fields.setdefault(
        "line_items_tax_sum",
        sum((_decimal(row.get("tax_amount")) or Decimal("0") for row in result.rows), Decimal("0")),
    )
    document_context = _Context(all_rows, None, result.document_fields, {}, positions)
    result.reconciliation_failures.extend(_document_reconciliation_failures(plan, document_context))
    return result


def _selector_source_rows(rows: list[VisualRow], plan: ExtractionPlan) -> list[VisualRow]:
    if plan.row_selector.scope != "service_block" or not plan.row_selector.identifier_pattern:
        return rows
    blocks: list[list[VisualRow]] = []
    current: list[VisualRow] = []
    for row in rows:
        starts_block = _safe_search(plan.row_selector.identifier_pattern, _clean_row_text(row.text))
        if starts_block and current:
            blocks.append(current)
            current = []
        if starts_block or current:
            current.append(row)
    if current:
        blocks.append(current)
    combined: list[VisualRow] = []
    for index, block in enumerate(blocks):
        first = block[0]
        combined.append(first.model_copy(update={
            "row_id": f"{first.row_id}.block{index + 1}",
            "text": " ".join(_clean_row_text(row.text) for row in block),
            "cells": [cell for row in block for cell in row.cells],
        }))
    return combined


def select_plan_rows(plan: ExtractionPlan, rows: list[VisualRow]) -> list[VisualRow]:
    line_regions = [region for region in plan.regions if region.role == "line_items"]
    if not line_regions:
        return list(rows)
    selected: list[VisualRow] = []
    seen: set[str] = set()
    for region in line_regions:
        for row in _select_region(rows, region):
            if row.row_id not in seen:
                selected.append(row)
                seen.add(row.row_id)
    return selected


def evaluate_expression(expression: ValueExpression, context: _Context) -> Any:
    op = expression.op
    if op == "literal":
        return expression.value
    if op == "row_text":
        return _clean_row_text(context.row.text) if context.row else None
    if op == "field":
        if expression.field in context.row_values:
            return context.row_values.get(expression.field)
        return context.fields.get(expression.field or "")
    if op == "copy":
        return evaluate_expression(expression.args[0], context) if expression.args else None
    if op == "document_label":
        return _document_label_value(context.all_rows, expression.label or "")
    if op == "row_column":
        return _row_column_value(context.row, expression, context.column_positions)
    if op == "regex_extract":
        raw = evaluate_expression(expression.args[0], context) if expression.args else None
        if raw is None or not expression.pattern:
            return None
        try:
            match = re.search(expression.pattern, str(raw))
        except re.error:
            return None
        if not match:
            return None
        group = expression.group if expression.group <= (match.lastindex or 0) else 0
        return match.group(group).strip()
    if op == "coalesce":
        for arg in expression.args:
            value = evaluate_expression(arg, context)
            if value not in (None, ""):
                return value
        return None
    if op in {"sum", "subtract", "multiply", "abs"}:
        values = [_decimal(evaluate_expression(arg, context)) for arg in expression.args]
        if op == "abs":
            return abs(values[0]) if values and values[0] is not None else None
        if any(value is None for value in values):
            return None
        decimals = [value for value in values if value is not None]
        if op == "sum":
            return sum(decimals, Decimal("0"))
        if op == "subtract":
            return decimals[0] - sum(decimals[1:], Decimal("0")) if decimals else None
        product = Decimal("1")
        for value in decimals:
            product *= value
        return product
    if op in {"month_start", "month_end"}:
        raw = evaluate_expression(expression.args[0], context) if expression.args else None
        parsed = parse_date_value(raw)
        if parsed is None:
            return None
        if op == "month_start":
            return parsed.replace(day=1)
        return parsed.replace(day=calendar.monthrange(parsed.year, parsed.month)[1])
    return None


def _select_region(rows: list[VisualRow], region: RegionRule) -> list[VisualRow]:
    selected: list[VisualRow] = []
    in_region = not region.start_markers
    closed_once = False
    start_page: int | None = None
    for row in rows:
        if closed_once and region.occurrence == "first":
            break
        if region.start_markers and _marker_hit(region.start_markers, row.text):
            in_region = True
            start_page = row.page
            if region.include_start:
                selected.append(row)
            continue
        if not in_region:
            continue
        if start_page is not None and region.max_pages and row.page - start_page + 1 > region.max_pages:
            in_region = False
            closed_once = True
            continue
        if region.end_markers and _marker_hit(region.end_markers, row.text):
            if region.include_end:
                selected.append(row)
            in_region = False
            closed_once = True
            continue
        selected.append(row)
    return selected


def _row_selected(row: VisualRow, plan: ExtractionPlan) -> bool:
    text = _clean_row_text(row.text)
    selector = plan.row_selector
    if any(_safe_search(pattern, text) for pattern in selector.exclude_patterns):
        return False
    if selector.include_pattern and not _safe_search(selector.include_pattern, text):
        return False
    return True


def _column_positions(rows: list[VisualRow], plan: ExtractionPlan) -> dict[str, float]:
    labels = {
        expression.label
        for expression in [*plan.document_field_rules.values(), *plan.row_field_rules.values()]
        if expression.op == "row_column" and expression.label
    }
    positions: dict[str, float] = {}
    for label in labels:
        for row in rows:
            for cell in row.cells:
                if label.lower() in cell.text.lower():
                    bbox = cell.normalized_bbox or cell.bbox
                    positions[label] = (bbox.x0 + bbox.x1) / 2
                    break
            if label in positions:
                break
    return positions


def _row_column_value(
    row: VisualRow | None,
    expression: ValueExpression,
    positions: dict[str, float],
) -> Any:
    if row is None or not row.cells:
        return None
    if isinstance(expression.value, int) and 0 <= expression.value < len(row.cells):
        return _cell_value(row.cells[expression.value].text)
    target = float(expression.value) if isinstance(expression.value, (int, float)) else None
    if target is None and expression.label:
        target = positions.get(expression.label)
    if target is not None:
        cell = min(
            row.cells,
            key=lambda item: abs(
                (
                    (item.normalized_bbox or item.bbox).x0
                    + (item.normalized_bbox or item.bbox).x1
                ) / 2 - target
            ),
        )
        return _cell_value(cell.text)
    if expression.label:
        for cell in row.cells:
            if expression.label.lower() in cell.text.lower():
                return _cell_value(cell.text.split(expression.label, 1)[-1])
    return None


def _document_label_value(rows: list[VisualRow], label: str) -> str | None:
    if not label:
        return None
    for row in rows:
        clean = _clean_row_text(row.text)
        if label.lower() not in clean.lower():
            continue
        for index, cell in enumerate(row.cells):
            if label.lower() in cell.text.lower():
                suffix = cell.text.lower().split(label.lower(), 1)[-1].strip(" :")
                if suffix:
                    return suffix
                if index + 1 < len(row.cells):
                    return row.cells[index + 1].text.strip()
        suffix = re.split(re.escape(label), clean, maxsplit=1, flags=re.IGNORECASE)[-1].strip(" :")
        if suffix:
            return suffix
    return None


def _row_reconciliation_failures(plan: ExtractionPlan, context: _Context, row_number: int) -> list[str]:
    failures: list[str] = []
    for rule in plan.reconciliation_rules:
        if rule.scope != "row":
            continue
        left = _decimal(evaluate_expression(rule.left, context))
        right = _decimal(evaluate_expression(rule.right, context))
        if left is None or right is None:
            if rule.required:
                failures.append(f"row {row_number}: {rule.name} could not be evaluated")
            continue
        if abs(left - right) > Decimal(str(rule.tolerance)):
            failures.append(f"row {row_number}: {rule.name} variance {left - right}")
    return failures


def _document_reconciliation_failures(plan: ExtractionPlan, context: _Context) -> list[str]:
    failures: list[str] = []
    for rule in plan.reconciliation_rules:
        if rule.scope != "document":
            continue
        left = _decimal(evaluate_expression(rule.left, context))
        right = _decimal(evaluate_expression(rule.right, context))
        if left is None or right is None:
            if rule.required:
                failures.append(f"document: {rule.name} could not be evaluated")
            continue
        if abs(left - right) > Decimal(str(rule.tolerance)):
            failures.append(f"document: {rule.name} variance {left - right}")
    return failures


def _cell_value(value: str) -> Any:
    parsed = _decimal(value)
    return parsed if parsed is not None else value.strip()


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    raw = str(value).strip().replace("$", "").replace("€", "").replace("£", "")
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = f"-{raw[1:-1]}"
    raw = raw.replace(" ", "")
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        # French/European invoices print decimal commas; retain a lone
        # three-digit comma group as a conventional thousands separator.
        raw = raw.replace(",", "" if re.search(r",\d{3}$", raw) else ".")
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _safe_search(pattern: str, text: str) -> bool:
    try:
        return re.search(pattern, text, re.IGNORECASE) is not None
    except re.error:
        return False


def _marker_hit(markers: list[str], text: str) -> bool:
    text_lower = text.lower()
    return any(marker.lower() in text_lower for marker in markers if marker)


def _clean_row_text(text: str) -> str:
    text = re.sub(r"^p\d+\.r\d+\s+role=\w+\s+", "", str(text or "")).strip()
    text = re.sub(r"\[\s*p\d+\.r\d+\.c\d+\s+[^\]]+\]\s*", "", text)
    return re.sub(r"\s*\|\s*", " ", text).strip()
