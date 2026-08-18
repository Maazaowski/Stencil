"""Generic interpreter for declarative ExtractionModel documents.

This module knows NOTHING about any supplier. It only understands a small set
of orthogonal primitives:

1. locate region rows between the model's anchors (multi-page)
2. classify each row using the model's own classifiers
3. group rows by the model's grouping rule
4. pick cell text by the model's column x-ranges (refined by regex capture)
5. transform raw strings (currency, date with format, integer, value map)
6. run the model's self-checks

All layout knowledge lives in the model JSON; covering a new supplier means
authoring a different rule document — never editing this file.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import structlog

from stencil.extraction.layout import (
    LayoutDocument,
    VisualRow,
    extract_layout_document,
    render_layout_text,
)
from stencil.models.schema import (
    ExtractionModel,
    FieldSource,
    FieldTransform,
    HeaderFieldRule,
    ItemFieldRule,
    RowMatch,
    ValueOperand,
)
from stencil.validation.schema import (
    CanonicalInvoice,
    ChargeType,
    ExtractionMetadata,
    ExtractionPath,
    LineItem,
    OutputType,
)

logger = structlog.get_logger()


class ModelExecutionError(Exception):
    """The model could not produce a valid invoice from this PDF."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def execute_model(
    model: ExtractionModel,
    pdf_path: Path,
    intake_id: str,
    document: LayoutDocument | None = None,
    *,
    skip_self_checks: bool = False,
) -> CanonicalInvoice:
    invoice, _ = execute_model_with_trace(
        model, pdf_path, intake_id, document=document, skip_self_checks=skip_self_checks,
    )
    return invoice


def execute_model_with_trace(
    model: ExtractionModel,
    pdf_path: Path,
    intake_id: str,
    document: LayoutDocument | None = None,
    *,
    skip_self_checks: bool = False,
    allow_empty: bool = False,
) -> tuple[CanonicalInvoice, dict]:
    """Execute the model and return (invoice, execution trace).

    The trace records which rows were selected, how they were classified and
    grouped, and per-field extraction results — used for authoring feedback
    and the model test API. ``trace["dropped_items"]`` lists groups that looked
    like items but were dropped for a missing required field, with the reason.

    ``allow_empty`` returns an item-less invoice instead of raising when nothing
    matched — the builder needs the trace (and the drop reasons) precisely when a
    model produces nothing, which is when it is most in need of debugging.
    """
    if document is None:
        document = extract_layout_document(
            pdf_path, column_split_x=model.region.column_split_x or None
        )
    page_texts = render_layout_text(document)

    trace: dict = {"region_rows": [], "groups": [], "header": {}, "totals": {}, "dropped_items": []}
    known_labels = _model_known_labels(model)

    # 1. Header fields
    header_values: dict[str, object] = {}
    for name, rule in model.header_fields.items():
        raw, value = find_header_value(name, rule, page_texts, known_labels=known_labels)
        header_values[name] = value
        trace["header"][name] = {"raw": raw, "value": _jsonable(value)}
        if rule.required and value is None:
            raise ModelExecutionError(f"required header field '{name}' not found (label: {rule.label!r})")

    # 2. Totals
    total_values: dict[str, Decimal | None] = {}
    for name, rule in model.totals.items():
        raw, amount = find_total_value(name, rule, page_texts, known_labels=known_labels)
        total_values[name] = amount
        trace["totals"][name] = {"raw": raw, "value": _jsonable(amount)}

    # 3–6. Line items.
    if model.line_item_strategy is not None:
        # Deterministic grouping strategy (declared-policy layouts): replay the
        # SAME layout grouping the AI ground truth uses, so output matches by
        # construction. The AI row_classifier/grouping/item_fields recipe is
        # ignored in this mode.
        from stencil.extraction.normalization import build_grouped_line_items

        strat = model.line_item_strategy
        line_items = build_grouped_line_items(
            document,
            granularity=strat.granularity,
            service_id_preference=strat.service_id_preference,
            billing_reference_preference=strat.billing_reference_preference,
            anchors=strat.anchors,
            skip_keywords=strat.skip_keywords,
        )
        trace["line_item_strategy"] = strat.model_dump()
    else:
        region_rows = _locate_region_rows(document, model)
        trace["region_rows"] = [_trace_row(row, "") for row in region_rows]
        classified = _classify_rows(region_rows, model)
        classified = [(row, role) for row, role in classified if role != "skip"]
        trace["region_rows"] = [_trace_row(row, role) for row, role in classified]
        groups = _group_rows(classified, model)
        line_items = []
        for group in groups:
            emitted = _emit_items_from_group(group, model, line_items, trace, total_values)
            line_items.extend(emitted)

    if not line_items and not allow_empty:
        # Name the actual cause when candidates were found but dropped — a bare
        # "no line items" sends the author hunting through every rule.
        dropped = trace.get("dropped_items") or []
        detail = ""
        if dropped:
            reasons = {d["reason"] for d in dropped}
            detail = f" — {len(dropped)} candidate item(s) dropped: {'; '.join(sorted(reasons))}"
        raise ModelExecutionError(f"model produced no line items{detail}")

    for number, item in enumerate(line_items, start=1):
        item.line_number = number

    # 7. Build canonical invoice
    invoice = _build_document(model, intake_id, header_values, total_values, line_items, document)

    # 8. Self checks (optional — skipped when producing review artifacts after a check failure)
    if not skip_self_checks:
        for check in model.self_checks:
            _run_self_check(check, invoice)

    return invoice, trace


# ---------------------------------------------------------------------------
# Header field lookup (label-relative, cell-aware)
# ---------------------------------------------------------------------------


def _page_plain_texts(document: LayoutDocument) -> list[str]:
    pages = []
    for page in document.pages:
        pages.append("\n".join(row.text for row in page.visual_rows))
    return pages


def _model_known_labels(model: ExtractionModel) -> frozenset[str]:
    """All labels the model anchors on — used to reject label text as a value."""
    labels = {rule.label for rule in model.header_fields.values() if rule.label}
    labels |= {rule.label for rule in model.totals.values() if rule.label}
    return frozenset(labels)


def find_header_value(
    name: str,
    rule: HeaderFieldRule,
    page_texts: list[str],
    known_labels: frozenset[str] = frozenset(),
) -> tuple[str | None, object]:
    """Resolve one header field exactly as the runtime does. Returns (raw, value).

    Shared by execute_model and the authoring verify/repair pass so authoring
    judges rules with the same semantics they will have in production.
    """
    if rule.literal is not None:
        raw: str | None = rule.literal
    else:
        validator = _date_validator(rule) if _is_date_field(name, rule) else None
        raw = _find_value_by_label(rule, page_texts, known_labels=known_labels, validator=validator)
    return raw, _convert_header_value(name, raw, rule)


def find_total_value(
    name: str,
    rule: HeaderFieldRule,
    page_texts: list[str],
    known_labels: frozenset[str] = frozenset(),
) -> tuple[str | None, Decimal | None]:
    """Resolve one total exactly as the runtime does. Returns (raw, amount).

    Currency totals always skip percent tokens (a subtotal label printed near
    'VAT 23.00 %' must never yield 23.00); tax_rate keeps percents because the
    rate IS the percent token.
    """
    skip_percent = name != "tax_rate"
    if rule.literal is not None:
        raw: str | None = rule.literal
    else:
        validator = _amount_validator(skip_percent=skip_percent)
        raw = _find_value_by_label(rule, page_texts, known_labels=known_labels, validator=validator)
        if name == "tax_rate":
            # Colt-style layouts often print the rate inside the total label
            # itself, e.g. "Total VAT 25.00%" with the tax amount in the value
            # cell to the right. In that case the ordinary label-relative value
            # is the tax amount, not the rate.
            embedded_rate = _find_embedded_tax_rate(rule, page_texts)
            if embedded_rate is not None:
                raw = embedded_rate
    if not raw:
        return raw, None
    amount = _parse_decimal(raw, ignore_percent=rule.ignore_percent or skip_percent)
    if name == "tax_rate" and amount is not None and amount > 1:
        amount = amount / Decimal("100")
    return raw, amount


def _find_embedded_tax_rate(rule: HeaderFieldRule, page_texts: list[str]) -> str | None:
    """Find a percent token printed in the tax/VAT label cell or row."""
    label = (rule.label or "").strip().lower()
    if not label:
        return None
    aliases = [label]
    tax_words = ["vat", "tax", "moms", "dph", "btw"]
    if any(word in label for word in [*tax_words, "rate"]):
        aliases.extend(tax_words)

    page_index = max(rule.page - 1, 0)
    candidate_pages = [page_index] if page_index < len(page_texts) else []
    candidate_pages += [i for i in range(len(page_texts)) if i not in candidate_pages]

    for idx in candidate_pages:
        for line in page_texts[idx].splitlines():
            lowered = line.lower()
            if not any(alias in lowered for alias in aliases):
                continue
            cells = _line_cells(line)
            for text, _x0, _x1 in cells:
                cell_lower = text.lower()
                if any(alias in cell_lower for alias in aliases):
                    match = re.search(r"\b\d+(?:[.,]\d+)?\s*%", text)
                    if match:
                        return match.group(0)
            match = re.search(r"\b\d+(?:[.,]\d+)?\s*%", line)
            if match:
                return match.group(0)
    return None


def _is_date_field(name: str, rule: HeaderFieldRule) -> bool:
    return "date" in name or bool(rule.date_format)


def _date_validator(rule: HeaderFieldRule):
    def _valid(text: str) -> bool:
        return _parse_date(text, rule.date_format) is not None
    return _valid


def _amount_validator(skip_percent: bool):
    def _valid(text: str) -> bool:
        return _parse_decimal(text, ignore_percent=skip_percent) is not None
    return _valid


def _find_value_by_label(
    rule: HeaderFieldRule,
    page_texts: list[str],
    known_labels: frozenset[str] = frozenset(),
    validator=None,
) -> str | None:
    """Locate a labelled value in the rendered layout text, cell-aware.

    For each label occurrence the positional candidates are tried in order;
    a candidate is accepted only if it survives value_pattern/percent rules
    (_select_value), is not another known label, and passes the optional
    type validator (date/amount). This lets table-style layouts (labels row
    above values row) and same-line label:value pairs coexist without
    guessing which one the page uses.
    """
    if validator is None and rule.date_format:
        # The rule declares its value type — gate candidates accordingly even
        # when called without an explicit validator.
        validator = _date_validator(rule)

    page_index = max(rule.page - 1, 0)
    candidate_pages = [page_index] if page_index < len(page_texts) else []
    # Fall back to scanning every page if the value isn't on the stated one.
    candidate_pages += [i for i in range(len(page_texts)) if i not in candidate_pages]

    label_lower = rule.label.lower()
    for idx in candidate_pages:
        lines = page_texts[idx].splitlines()
        matches = [i for i, line in enumerate(lines) if label_lower in line.lower()]
        if not matches:
            continue
        ordered = matches if rule.occurrence != "last" else list(reversed(matches))
        for line_idx in ordered:
            for candidate in _value_candidates(lines, line_idx, rule):
                selected = _select_value(candidate, rule)
                if selected is None:
                    continue
                if _is_other_label(selected, rule.label, known_labels):
                    continue
                if validator is not None and not validator(selected):
                    continue
                return selected
    return None


# One rendered cell: "[p1.r3.c0 c0 text x=36-120] Invoice Date"
_CELL_RE = re.compile(r"\[[^\]]*?x=(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)[^\]]*?\]\s*(.*?)(?=\s*\|\s*\[|$)")


def _line_cells(line: str) -> list[tuple[str, float | None, float | None]]:
    """Split a rendered layout row into (text, x0, x1) cells.

    Plain lines (no cell markers) become a single span-less pseudo-cell so the
    lookup still works on non-coordinate text.
    """
    cells = [
        (match.group(3).strip(), float(match.group(1)), float(match.group(2)))
        for match in _CELL_RE.finditer(line)
    ]
    if cells:
        return cells
    stripped = line.strip()
    return [(stripped, None, None)] if stripped else []


def _value_candidates(lines: list[str], line_idx: int, rule: HeaderFieldRule) -> list[str]:
    """Ordered candidate value texts for one label occurrence."""
    label_lower = rule.label.lower()
    cells = _line_cells(lines[line_idx])
    label_idx = next(
        (i for i, (text, _x0, _x1) in enumerate(cells) if label_lower in text.lower()),
        None,
    )
    if label_idx is None:
        return []
    label_text, label_x0, label_x1 = cells[label_idx]

    def remainder_after_label() -> str | None:
        pos = label_text.lower().find(label_lower)
        tail = label_text[pos + len(rule.label):].strip().strip(":#").strip()
        return tail or None

    def prefix_before_label() -> str | None:
        pos = label_text.lower().find(label_lower)
        head = label_text[:pos].strip().rstrip(":#").strip()
        return head or None

    def aligned(line: str | None) -> str | None:
        if not line:
            return None
        return _aligned_cell_text(_line_cells(line), label_x0, label_x1, label_idx)

    def sole_cell(line: str | None) -> str | None:
        if not line:
            return None
        line_cells = [c for c in _line_cells(line) if c[0]]
        return line_cells[0][0] if len(line_cells) == 1 else None

    below_line = _next_non_empty(lines, line_idx)
    above_line = _prev_non_empty(lines, line_idx)
    right_cell = next((text for text, _x0, _x1 in cells[label_idx + 1:] if text), None)
    left_cell = next((text for text, _x0, _x1 in reversed(cells[:label_idx]) if text), None)

    # On a header-classified row (a row of column labels), the value lives in
    # the x-aligned cell of the row below — its same-row neighbour is just the
    # next column label. On ordinary rows the same-row neighbour IS the value.
    header_row = _rendered_row_role(lines[line_idx]) == "header"

    position = rule.value_position
    candidates: list[str | None] = []
    if position == "right":
        if header_row:
            candidates = [remainder_after_label(), aligned(below_line), right_cell]
        else:
            candidates = [remainder_after_label(), right_cell, aligned(below_line)]
    elif position == "left":
        candidates = [prefix_before_label(), left_cell]
    elif position == "below":
        candidates = [aligned(below_line), sole_cell(below_line)]
    elif position == "above":
        candidates = [aligned(above_line), sole_cell(above_line)]
    elif header_row:
        candidates = [remainder_after_label(), aligned(below_line), right_cell]
    else:
        candidates = [remainder_after_label(), right_cell, aligned(below_line)]
    return [c for c in candidates if c]


_ROW_ROLE_RE = re.compile(r"^\S+\s+role=(\S+)")


def _rendered_row_role(line: str) -> str | None:
    match = _ROW_ROLE_RE.match(line.strip())
    return match.group(1) if match else None


def _aligned_cell_text(
    cells: list[tuple[str, float | None, float | None]],
    label_x0: float | None,
    label_x1: float | None,
    fallback_index: int,
) -> str | None:
    """Pick the cell horizontally aligned with the label's x-span."""
    if not cells:
        return None
    if label_x0 is not None and label_x1 is not None:
        best_text, best_overlap = None, 0.0
        for text, x0, x1 in cells:
            if not text or x0 is None or x1 is None:
                continue
            overlap = min(label_x1, x1) - max(label_x0, x0)
            if overlap > best_overlap:
                best_text, best_overlap = text, overlap
        if best_text:
            return best_text
        label_center = (label_x0 + label_x1) / 2
        scored = [
            (abs((x0 + x1) / 2 - label_center), text)
            for text, x0, x1 in cells
            if text and x0 is not None and x1 is not None
        ]
        if scored:
            return min(scored)[1]
    if 0 <= fallback_index < len(cells):
        return cells[fallback_index][0] or None
    return None


def _is_other_label(text: str, own_label: str, known_labels: frozenset[str]) -> bool:
    """True when a candidate value is really one of the model's other labels."""
    stripped = text.strip().lower()
    own = own_label.lower()
    for label in known_labels:
        candidate = label.lower()
        if candidate == own:
            continue
        if candidate in stripped and len(stripped) <= len(candidate) + 12:
            return True
    return False


def _select_value(text: str, rule: HeaderFieldRule) -> str | None:
    if not text:
        return None
    if rule.ignore_percent:
        text = re.sub(r"\(?-?\d[\d,.]*\s*%\)?", " ", text).strip()
    if rule.value_pattern:
        try:
            match = re.search(rule.value_pattern, text)
        except re.error:
            match = None
        if match:
            return (match.group(1) if match.groups() else match.group(0)).strip()
        return None
    return text or None


def _next_non_empty(lines: list[str], idx: int) -> str | None:
    for line in lines[idx + 1:]:
        if line.strip():
            return line
    return None


def _prev_non_empty(lines: list[str], idx: int) -> str | None:
    for line in reversed(lines[:idx]):
        if line.strip():
            return line
    return None


def _convert_header_value(name: str, raw: str | None, rule: HeaderFieldRule):
    if raw is None:
        return None
    if "date" in name or rule.date_format:
        return _parse_date(raw, rule.date_format)
    return raw


# ---------------------------------------------------------------------------
# Region location
# ---------------------------------------------------------------------------


def _locate_region_rows(document: LayoutDocument, model: ExtractionModel) -> list[VisualRow]:
    region = model.region
    start_anchors = [a.lower() for a in region.start_anchors if a]
    end_anchors = [a.lower() for a in region.end_anchors if a]

    selected: list[VisualRow] = []
    in_region = not start_anchors  # no start anchors -> whole document is the region
    region_was_opened = False

    for page in document.pages:
        if region.page_scope and page.page_number not in region.page_scope:
            continue
        # A page break never closes an open region (in_region carries over).
        # A region closed by an end anchor stays closed on later pages UNLESS
        # the end anchor is declared a per-page footer (end_scope='page'), in
        # which case the next page resumes collecting without its own anchor.
        if (
            start_anchors
            and not in_region
            and region_was_opened
            and region.end_scope == "page"
            and region.include_unanchored_pages
        ):
            in_region = True
        for row in page.visual_rows:
            text_lower = row.text.lower()
            if not in_region:
                if start_anchors and any(anchor in text_lower for anchor in start_anchors):
                    in_region = True
                    region_was_opened = True
                    if region.include_anchor_row:
                        selected.append(row)  # anchor row is also a data/item row
                continue
            if end_anchors and any(anchor in text_lower for anchor in end_anchors):
                in_region = False
                continue
            if start_anchors and any(anchor in text_lower for anchor in start_anchors):
                # Repeated anchor row: pure delimiter by default, kept as data when
                # the same text also marks each item (include_anchor_row).
                if region.include_anchor_row:
                    selected.append(row)
                continue
            selected.append(row)
    return selected


# ---------------------------------------------------------------------------
# Row classification
# ---------------------------------------------------------------------------


def _classify_rows(rows: list[VisualRow], model: ExtractionModel) -> list[tuple[VisualRow, str]]:
    compiled = []
    for classifier in model.row_classifiers:
        compiled.append((classifier.role, classifier.where, _compile_match(classifier.where)))

    classified = []
    for row in rows:
        role = "detail"
        for klass_role, where, patterns in compiled:
            if _row_matches(row, model, where, patterns):
                role = klass_role
                break
        classified.append((row, role))
    return classified


def _compile_match(where: RowMatch) -> dict:
    patterns = {}
    for attr in ("row_text", "pattern"):
        raw = getattr(where, attr, None)
        if raw:
            try:
                patterns[attr] = re.compile(raw)
            except re.error:
                patterns[attr] = None
    return patterns


def _row_matches(row: VisualRow, model: ExtractionModel, where: RowMatch, patterns: dict) -> bool:
    if where.min_cells is not None and len(row.cells) < where.min_cells:
        return False
    if where.row_text:
        compiled = patterns.get("row_text")
        if compiled is None or not compiled.search(row.text):
            return False
    if where.column and where.pattern:
        cell_text = _cell_text_in_column(row, model, where.column)
        compiled = patterns.get("pattern")
        if cell_text is None or compiled is None or not compiled.search(cell_text):
            return False
    elif where.pattern and not where.column:
        compiled = patterns.get("pattern")
        if compiled is None or not compiled.search(row.text):
            return False
    if where.has_amount_in_column:
        cell_text = _cell_text_in_column(row, model, where.has_amount_in_column)
        if cell_text is None or _parse_decimal(cell_text) is None:
            return False
    return True


def _column_bounds(model: ExtractionModel, name: str) -> tuple[float, float] | None:
    for column in model.region.columns:
        if column.name == name:
            return column.x0, column.x1
    return None


def _cell_text_in_column(row: VisualRow, model: ExtractionModel, column_name: str) -> str | None:
    bounds = _column_bounds(model, column_name)
    if bounds is None:
        return None
    x0, x1 = bounds
    parts = []
    for cell in row.cells:
        bbox = cell.normalized_bbox or cell.bbox
        center = (bbox.x0 + bbox.x1) / 2
        if x0 <= center <= x1:
            parts.append(cell.text)
    return " ".join(parts).strip() or None


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def _group_rows(
    classified: list[tuple[VisualRow, str]],
    model: ExtractionModel,
) -> list[list[tuple[VisualRow, str]]]:
    grouping = model.grouping
    mode = grouping.mode

    if mode == "single_row":
        item_role = grouping.item_role or "detail"
        return [[(row, role)] for row, role in classified if role == item_role]

    if mode == "span":
        return _group_span(classified, grouping.start_role, grouping.end_role, grouping.include_end_row)

    if mode == "role_transition":
        return _group_role_transition(classified, grouping.start_role)

    raise ModelExecutionError(f"unknown grouping mode: {mode!r}")


def _group_span(
    classified: list[tuple[VisualRow, str]],
    start_role: str | None,
    end_role: str | None,
    include_end_row: bool,
) -> list[list[tuple[VisualRow, str]]]:
    if not start_role or not end_role:
        raise ModelExecutionError("span grouping requires start_role and end_role")
    groups: list[list[tuple[VisualRow, str]]] = []
    current: list[tuple[VisualRow, str]] | None = None
    for row, role in classified:
        if role == start_role:
            if current:
                # New start before the previous group closed — start fresh.
                groups.append(current)
            current = [(row, role)]
            continue
        if current is None:
            continue
        if role == end_role:
            if include_end_row:
                current.append((row, role))
            groups.append(current)
            current = None
            continue
        current.append((row, role))
    if current:
        groups.append(current)
    return groups


def _group_role_transition(
    classified: list[tuple[VisualRow, str]],
    start_role: str | None,
) -> list[list[tuple[VisualRow, str]]]:
    if not start_role:
        raise ModelExecutionError("role_transition grouping requires start_role")
    groups: list[list[tuple[VisualRow, str]]] = []
    current: list[tuple[VisualRow, str]] | None = None
    for row, role in classified:
        if role == start_role:
            if current:
                groups.append(current)
            current = [(row, role)]
        elif current is not None:
            current.append((row, role))
    if current:
        groups.append(current)
    return groups


# ---------------------------------------------------------------------------
# Item field extraction
# ---------------------------------------------------------------------------


def _emit_items_from_group(
    group: list[tuple[VisualRow, str]],
    model: ExtractionModel,
    existing_items: list[LineItem],
    trace: dict,
    total_values: dict | None = None,
) -> list[LineItem]:
    grouping = model.grouping
    group_trace = {"rows": [_trace_row(row, role) for row, role in group], "items": []}
    trace["groups"].append(group_trace)

    if grouping.emit == "one_item_per_role_row":
        emit_role = grouping.emit_role
        if not emit_role:
            raise ModelExecutionError("emit=one_item_per_role_row requires emit_role")
        items = []
        for row, role in group:
            if role != emit_role:
                continue
            item = _extract_item(
                group, model, emit_row=(row, role), trace=group_trace, root_trace=trace,
                total_values=total_values,
            )
            if item is not None:
                items.append(item)
        return items

    item = _extract_item(
        group, model, emit_row=None, trace=group_trace, root_trace=trace, total_values=total_values,
    )
    return [item] if item is not None else []


def _describe_group(group: list[tuple[VisualRow, str]]) -> str:
    """Short label identifying a group in the drop list (its first row's text)."""
    for row, _role in group:
        text = row.text.strip()
        if text:
            return text[:120]
    return "(empty group)"


# ENGINE INVARIANT: only a missing amount may drop a line item. A charge row
# without an auxiliary field (billing period, quantity, unit rate, ...) is
# still a real charge — one-off task items legitimately lack billing periods.
# Dropping money over a missing date silently corrupts the delivered total;
# the diff/self-check safety nets are the place to judge real failures.
_DROPPABLE_ITEM_FIELDS = frozenset({"amount"})


def _extract_item(
    group: list[tuple[VisualRow, str]],
    model: ExtractionModel,
    emit_row: tuple[VisualRow, str] | None,
    trace: dict,
    root_trace: dict | None = None,
    total_values: dict | None = None,
) -> LineItem | None:
    values: dict[str, object] = {}
    field_trace: dict[str, object] = {}

    deferred_same_as: list[ItemFieldRule] = []
    for rule in model.item_fields:
        if rule.same_as:
            deferred_same_as.append(rule)
            continue
        raw, value = _evaluate_field(rule, group, model, emit_row, values, total_values)
        values[rule.name] = value
        field_trace[rule.name] = {"raw": raw, "value": _jsonable(value)}
        if rule.required and value in (None, ""):
            if rule.name in _DROPPABLE_ITEM_FIELDS:
                field_trace[rule.name]["error"] = "required field missing"
                trace["items"].append(field_trace)
                if root_trace is not None:
                    source = rule.source
                    where = f" in column '{source.column}'" if source and source.column else ""
                    root_trace.setdefault("dropped_items", []).append({
                        "reason": f"no {rule.name}{where}",
                        "field": rule.name,
                        "raw": raw,
                        "row_ids": [row.row_id for row, _ in group],
                        "text": _describe_group(group),
                    })
                return None
            # Required on an auxiliary field is advisory: keep the item.
            field_trace[rule.name]["warning"] = "marked required but missing — item kept"

    for rule in deferred_same_as:
        values[rule.name] = values.get(rule.same_as)
        field_trace[rule.name] = {"same_as": rule.same_as, "value": _jsonable(values[rule.name])}

    trace["items"].append(field_trace)
    return _line_item_from_values(values, group, emit_row)


def _evaluate_field(
    rule: ItemFieldRule,
    group: list[tuple[VisualRow, str]],
    model: ExtractionModel,
    emit_row: tuple[VisualRow, str] | None,
    current_values: dict,
    total_values: dict | None = None,
) -> tuple[str | None, object]:
    # Unified value expression wins over the legacy single/multi-source forms.
    if rule.value is not None:
        return _evaluate_value_expr(rule, group, model, emit_row, current_values, total_values)

    if rule.literal is not None:
        return rule.literal, _apply_transform(rule.literal, rule.transform)

    if rule.sources:
        return _evaluate_combined_field(rule, group, model, emit_row)

    raw = _raw_field_text(rule.source, group, model, emit_row) if rule.source else None
    if raw is None and rule.transform.default is not None:
        return None, _apply_transform(rule.transform.default, rule.transform, skip_value_map=True)
    if raw is None:
        return None, None
    return raw, _apply_transform(raw, rule.transform)


def _resolve_operand(
    operand: ValueOperand,
    group: list[tuple[VisualRow, str]],
    model: ExtractionModel,
    emit_row: tuple[VisualRow, str] | None,
    current_values: dict,
    total_values: dict | None,
) -> tuple[Decimal | None, str | None]:
    """Resolve one operand to (decimal, raw_text). None decimal = not present."""
    if operand.kind == "const":
        return _parse_decimal(operand.const), operand.const
    if operand.kind == "ref":
        ref = operand.ref
        value = current_values.get(ref) if ref else None
        if value is None and total_values is not None and ref:
            value = total_values.get(ref)
        if value is None:
            return None, None
        return _parse_decimal(str(value)), str(value)
    # kind == "extract" (default)
    raw = _raw_field_text(operand.source, group, model, emit_row) if operand.source else None
    return (_parse_decimal(raw) if raw is not None else None), raw


def _evaluate_value_expr(
    rule: ItemFieldRule,
    group: list[tuple[VisualRow, str]],
    model: ExtractionModel,
    emit_row: tuple[VisualRow, str] | None,
    current_values: dict,
    total_values: dict | None,
) -> tuple[str | None, object]:
    """Evaluate a field's value expression (extract / sum / subtract / product)."""
    expr = rule.value
    parts: list[Decimal | None] = []
    raws: list[str] = []
    for operand in expr.operands:
        amount, raw = _resolve_operand(operand, group, model, emit_row, current_values, total_values)
        parts.append(amount)
        if raw:
            raws.append(raw)

    op = expr.op
    if op == "extract":
        result = next((p for p in parts if p is not None), None)
    elif op == "sum":
        present = [p for p in parts if p is not None]
        result = sum(present, Decimal("0")) if present else None
    elif op == "subtract":
        if not parts or parts[0] is None:
            result = None
        else:
            result = parts[0] - sum((p for p in parts[1:] if p is not None), Decimal("0"))
    elif op == "product":
        if not parts or any(p is None for p in parts):
            result = None
        else:
            result = Decimal("1")
            for p in parts:
                result *= p
    else:
        raise ModelExecutionError(f"unknown value op {op!r}")

    if result is None:
        return None, None
    return (" ".join(raws) or None), _apply_transform(str(result), rule.transform)


def _evaluate_combined_field(
    rule: ItemFieldRule,
    group: list[tuple[VisualRow, str]],
    model: ExtractionModel,
    emit_row: tuple[VisualRow, str] | None,
) -> tuple[str | None, object]:
    """Combine several sources into one value (e.g. tax = taxes + surcharges).

    Each source's raw text is parsed as a decimal; a missing component counts as
    zero. Returns ``(None, None)`` only when no component is present at all, so a
    non-required combined field never drops the item.
    """
    parts: list[Decimal] = []
    raws: list[str] = []
    for source in rule.sources:
        raw = _raw_field_text(source, group, model, emit_row)
        if raw is None:
            continue
        amount = _parse_decimal(raw)
        if amount is None:
            continue
        parts.append(amount)
        raws.append(raw)
    if not parts:
        return None, None
    if rule.combine == "sum":
        total = sum(parts, Decimal("0"))
    else:
        raise ModelExecutionError(f"unknown combine mode {rule.combine!r}")
    return " + ".join(raws), _apply_transform(str(total), rule.transform)


def _raw_field_text(
    source: FieldSource,
    group: list[tuple[VisualRow, str]],
    model: ExtractionModel,
    emit_row: tuple[VisualRow, str] | None,
) -> str | None:
    rows = _select_rows(source, group, emit_row)
    if not rows:
        return None

    texts: list[str] = []
    for row, _role in rows:
        if source.column:
            cell_text = _cell_text_in_column(row, model, source.column)
            if cell_text is None:
                continue
            text = cell_text
        else:
            text = row.text
        if source.pattern:
            try:
                match = re.search(source.pattern, text)
            except re.error:
                match = None
            if not match:
                continue
            text = match.group(1) if match.groups() else match.group(0)
        text = text.strip()
        if text:
            texts.append(text)

    if not texts:
        return None
    if source.join is not None:
        return source.join.join(texts)
    return texts[0] if source.occurrence != "last" else texts[-1]


def _select_rows(
    source: FieldSource,
    group: list[tuple[VisualRow, str]],
    emit_row: tuple[VisualRow, str] | None,
) -> list[tuple[VisualRow, str]]:
    if source.rows == "emit_row":
        return [emit_row] if emit_row else []
    if source.rows == "first":
        return group[:1]
    if source.rows == "last":
        return group[-1:]
    if source.rows == "all_in_group":
        return group
    # default: 'role'
    if source.row_role:
        return [(row, role) for row, role in group if role == source.row_role]
    return group


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


def _apply_transform(raw: str, transform: FieldTransform, skip_value_map: bool = False):
    text = raw.strip()
    if transform.value_map and not skip_value_map:
        mapped = _apply_value_map(text, transform.value_map)
        if mapped is not None:
            text = mapped
        elif transform.default is not None:
            text = transform.default

    if transform.type in ("currency", "decimal"):
        return _parse_decimal(text)
    if transform.type == "integer":
        value = _parse_decimal(text)
        return int(value) if value is not None else None
    if transform.type == "date":
        return _parse_date(text, transform.date_format)
    return text or None


def _apply_value_map(text: str, value_map: dict[str, str]) -> str | None:
    lowered = text.lower()
    for key, mapped in value_map.items():
        if key.lower() in lowered:
            return mapped
    return None


def _parse_decimal(value: str | None, ignore_percent: bool = False) -> Decimal | None:
    if not value:
        return None
    text = value.strip()
    if ignore_percent:
        text = re.sub(r"\(?-?\d[\d,.]*\s*%\)?", " ", text)
    # Allow a leading-dot decimal (".83" == 0.83), not just digit-first numbers.
    matches = re.findall(r"[-(]?\s*[$€£]?\s*(?:\d[\d,]*(?:\.\d+)?|\.\d+)\)?", text)
    if not matches:
        return None
    raw = matches[-1]
    # Accounting credit: a number suffixed with CR (e.g. "116.84CR") is negative.
    credit = bool(re.search(r"\d\s*CR\b", text, re.IGNORECASE))
    negative = ("(" in raw and ")" in raw) or credit
    cleaned = re.sub(r"[^\d.,\-]", "", raw)
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # European decimal comma vs thousands separator.
        cleaned = cleaned.replace(",", "") if re.search(r",\d{3}(?:,|$)", cleaned) else cleaned.replace(",", ".")
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    return -amount if negative and amount > 0 else amount


_FALLBACK_DATE_FORMATS = (
    "%d %b %Y", "%d %b %y", "%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d",
    "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%b %d, %Y", "%b. %d, %Y", "%d %B %Y",
)


def _parse_date(value: str | None, date_format: str | None = None) -> date | None:
    if not value:
        return None
    text = value.strip()
    formats = ([date_format] if date_format else []) + list(_FALLBACK_DATE_FORMATS)
    candidates = [text]
    without_dotted_month = re.sub(r"\b([A-Za-z]{3,9})\.(?=\s+\d)", r"\1", text)
    if without_dotted_month != text:
        candidates.append(without_dotted_month)
    for candidate in candidates:
        for fmt in formats:
            try:
                return datetime.strptime(candidate, fmt).date()
            except (ValueError, TypeError):
                continue
    # The value may be embedded in surrounding text — try the first date-ish token.
    match = re.search(
        r"\d{1,2}[-/ ][A-Za-z]{3,9}\.?[-/ ]\d{2,4}"
        r"|\d{4}-\d{2}-\d{2}"
        r"|\d{1,2}[/.]\d{1,2}[/.]\d{2,4}"
        r"|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{2,4}",
        text,
    )
    if match and match.group(0) != text:
        return _parse_date(match.group(0), date_format)
    return None


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------


def _build_document(
    model: ExtractionModel,
    intake_id: str,
    header_values: dict,
    total_values: dict,
    line_items: list[LineItem],
    document: LayoutDocument,
) -> CanonicalInvoice:
    fields: dict[str, object] = {
        "supplier_name": model.supplier,
        "supplier_aliases": [],
        "invoice_number": _str_or_none(header_values.get("invoice_number")) or "UNKNOWN",
        "invoice_date": _date_or_none(header_values.get("invoice_date")) or date.today(),
        "due_date": _date_or_none(header_values.get("due_date")),
        "account_number": _str_or_none(header_values.get("account_number")),
        "ban": _str_or_none(header_values.get("ban")),
        "billing_period_start": _date_or_none(header_values.get("billing_period_start")),
        "billing_period_end": _date_or_none(header_values.get("billing_period_end")),
        "currency": (_str_or_none(header_values.get("currency")) or "USD")[:3],
        "po_number": _str_or_none(header_values.get("po_number")),
        "confidence": 1.0 if header_values.get("invoice_number") else 0.5,
        "output_type": model.output_type,
    }
    for name, value in total_values.items():
        if value is not None:
            fields[name] = value

    # Model-level tax policy → the same magic fields the profile hints use, so
    # execute_model (and its live preview) compute EXT_TAX with no profile needed.
    if model.tax_output_mode:
        fields["_tax_output_mode"] = model.tax_output_mode
    if model.tax_rate_source:
        fields["_tax_rate_source"] = model.tax_rate_source

    rows = [item.model_dump(mode="python") for item in line_items]
    confidence = _compute_confidence_fields(fields, line_items)

    return CanonicalInvoice(
        intake_id=intake_id,
        schema_id="invoice.standard",
        fields=fields,
        rows=rows,
        metadata=ExtractionMetadata(
            extraction_path=ExtractionPath.MODEL,
            model_id=model.model_id,
            supplier_profile_id=model.supplier_profile_id,
            layout_fingerprint=model.layout_fingerprint or None,
            total_pages=len(document.pages),
            overall_confidence=confidence,
        ),
    )


def _compute_confidence_fields(fields: dict, line_items: list[LineItem]) -> float:
    score = 1.0
    if fields.get("invoice_number") == "UNKNOWN":
        score -= 0.3
    if not line_items:
        score -= 0.5
    return max(score, 0.0)


def _build_invoice(
    model: ExtractionModel,
    intake_id: str,
    header_values: dict,
    total_values: dict,
    line_items: list[LineItem],
    document: LayoutDocument,
) -> CanonicalInvoice:
    """Backward-compatible alias."""
    return _build_document(model, intake_id, header_values, total_values, line_items, document)


def _line_item_from_values(
    values: dict,
    group: list[tuple[VisualRow, str]],
    emit_row: tuple[VisualRow, str] | None,
) -> LineItem | None:
    amount = values.get("amount")
    if amount is None:
        return None
    if not isinstance(amount, Decimal):
        amount = _parse_decimal(str(amount))
        if amount is None:
            return None

    description = values.get("description") or _default_description(group)
    source_page = values.get("source_page")
    if source_page is None:
        anchor_row = emit_row[0] if emit_row else group[0][0]
        source_page = anchor_row.page

    charge_type = _safe_charge_type(values.get("charge_type"))

    return LineItem(
        line_number=1,  # resequenced by the caller
        service_id=_str_or_none(values.get("service_id")),
        billing_reference=_str_or_none(values.get("billing_reference")),
        description=str(description)[:500],
        charge_type=charge_type,
        amount=amount,
        tax_amount=_decimal_or_none(values.get("tax_amount")),
        currency=_str_or_none(values.get("currency")) or "USD",
        billing_period_start=_date_or_none(values.get("billing_period_start")),
        billing_period_end=_date_or_none(values.get("billing_period_end")),
        quantity=_decimal_or_none(values.get("quantity")),
        unit_rate=_decimal_or_none(values.get("unit_rate")),
        source_page=int(source_page) if source_page is not None else None,
        confidence=0.95,
    )


def _default_description(group: list[tuple[VisualRow, str]]) -> str:
    texts = [row.text for row, role in group if role == "detail"]
    if not texts:
        texts = [group[0][0].text]
    return "; ".join(texts)[:500]


def _run_self_check(check, invoice: CanonicalInvoice) -> None:
    if check.check != "sum_of_items_equals":
        return
    target = getattr(invoice, check.field, None)
    if target is None:
        return
    items_sum = sum(
        (item.amount for item in invoice.line_items
         if item.charge_type not in (ChargeType.TAX, ChargeType.FEE, ChargeType.SURCHARGE)),
        Decimal("0"),
    )
    if abs(items_sum - target) > Decimal(str(check.tolerance)):
        raise ModelExecutionError(
            f"self-check failed: sum of line items {items_sum} != {check.field} {target}"
        )


# ---------------------------------------------------------------------------
# Small coercion helpers
# ---------------------------------------------------------------------------


def _safe_charge_type(value) -> ChargeType:
    if isinstance(value, ChargeType):
        return value
    if value:
        try:
            return ChargeType(str(value).strip().lower())
        except ValueError:
            pass
    return ChargeType.RECURRING


def _safe_output_type(value) -> OutputType:
    try:
        return OutputType(str(value))
    except ValueError:
        return OutputType.STANDARD


def _str_or_none(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _decimal_or_none(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    return _parse_decimal(str(value))


def _date_or_none(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return _parse_date(str(value))


def _trace_row(row: VisualRow, role: str) -> dict:
    return {
        "row_id": row.row_id,
        "page": row.page,
        "role": role,
        "text": row.text[:160],
        "cells": [cell.text[:60] for cell in row.cells],
    }


def _jsonable(value):
    if isinstance(value, (Decimal, date)):
        return str(value)
    return value
