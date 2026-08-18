"""Profile-guided normalization over layout evidence.

AI extraction is good at reading values, but it can still choose the wrong
semantic identifier when a layout shows both child charge IDs and parent total
IDs. Supplier profiles describe that policy; this module applies it
deterministically from PDF coordinates before validation/model authoring.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from stencil.extraction.layout import LayoutDocument, VisualRow, mark_anchor_rows
from stencil.extraction.markers import marker_in_text, marker_terms
from stencil.profiles.schema import LineItemHints, SupplierProfile
from stencil.validation.schema import SYNTHETIC_INVOICE_DATE_WARNING, ChargeType, LineItem

_DATE_FORMATS = (
    "%d %b %Y", "%d %b %y", "%d-%b-%Y", "%d-%b-%y",
    "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y",
    "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y",
    "%Y %m %d",
)
_DATE_VALUE_RE = (
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    r"|\d{4}\s+\d{1,2}\s+\d{1,2}"
    r"|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"
    r"|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}"
    r"|\d{1,2}\s+[A-Za-z]{3,9}\.\s+\d{2,4}"
    r"|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{2,4}"
    r"|[A-Za-z]{3,9}\.?\s+\d{4}"
)
_SERVICE_STOP_LABELS = ("MRC:", "NRC:", "Other Fees:", "Taxes:", "Amount", "$")
_BILLING_STOP_LABELS = ("Other Fees:", "Taxes:", "MRC:", "NRC:", "Amount", "$")


def _anchor_texts_from_hints(profile: SupplierProfile | None, hints: LineItemHints | None) -> list[str]:
    """Section anchors are supplier CONFIGURATION (profile hints), not shared code."""
    anchors: list[str] = []
    if hints and hints.detail_table_anchors:
        anchors.extend(hints.detail_table_anchors)
    if profile and profile.document_structure.detail_start_marker:
        anchors.extend(marker_terms(profile.document_structure.detail_start_marker))
    return anchors


def apply_layout_profile_hints(
    invoice,
    document: LayoutDocument,
    profile: SupplierProfile | None,
    line_item_hints: LineItemHints | None = None,
    *,
    preserve_existing_line_items: bool = False,
):
    """Mutate and return ``invoice`` after applying executable profile hints.

    The behavior is intentionally supplier-agnostic: only structured profile
    hints control it. For ``per_total_group`` layouts, delivered service rows are
    rebuilt from start-row-to-total-footer groups so child charge rows collapse
    into the parent service total.
    """

    hints = line_item_hints or (profile.line_item_hints if profile else None)
    if not hints:
        return invoice

    # A model that declares its own tax policy wins over the profile default
    # (the model is meant to be self-contained). Only fill from profile hints
    # when the model left these unset.
    model_set_tax = "_tax_output_mode" in invoice.fields
    invoice.fields.setdefault("_tax_output_mode", hints.tax_output_mode or "auto")
    if hints.tax_rate_source:
        invoice.fields.setdefault("_tax_rate_source", hints.tax_rate_source)
    if not model_set_tax and hints.tax_source in {
        "label_amount", "table_tax_column", "group_total_minus_amount",
    }:
        invoice.fields["_tax_fallback_policy"] = "explicit_only"
    _apply_header_date_hints(invoice, document, profile)
    _apply_excluded_total_hints(invoice, document, profile)
    if profile:
        invoice.fields.update(profile.advanced.document_field_defaults)
    mark_anchor_rows(document, _anchor_texts_from_hints(profile, hints))

    if preserve_existing_line_items:
        _enrich_preserved_line_items(invoice, document, profile, hints)
        return invoice

    if hints.line_item_granularity == "per_service_group":
        rebuilt = _line_items_from_service_groups(invoice, document, profile, hints)
        if rebuilt:
            invoice.line_items = rebuilt
            return invoice

    if (
        hints.line_item_granularity in {"per_total_group", "per_charge_row"}
        and hints.service_id_preference in {
            "total_row_identifier",
            "parent_identifier",
            "first_identifier",
            "leftmost_identifier",
        }
    ):
        marker_groups = _extract_marker_groups(document, profile)
        groups = marker_groups or _extract_total_groups(document, hints.skip_row_keywords)
        same_billing_ref = hints.billing_reference_preference == "same_as_service_id"
        separate_billing_ref = hints.billing_reference_preference == "separate_column"
        parent_billing_ref = hints.billing_reference_preference == "parent_identifier"
        id_from_footer = hints.service_id_preference in {"total_row_identifier", "parent_identifier"}
        if hints.line_item_granularity == "per_charge_row":
            rebuilt = _charge_line_items_from_total_groups(
                invoice,
                groups,
                same_billing_ref=same_billing_ref,
                separate_billing_ref=separate_billing_ref,
                parent_billing_ref=parent_billing_ref,
                id_from_footer=id_from_footer,
            )
        elif _should_expand_total_groups_to_charge_rows(groups):
            rebuilt = _charge_line_items_from_total_groups(
                invoice,
                groups,
                same_billing_ref=same_billing_ref,
                separate_billing_ref=separate_billing_ref,
                parent_billing_ref=parent_billing_ref,
                id_from_footer=False,
            )
        else:
            rebuilt = _line_items_from_total_groups(
                invoice,
                groups,
                same_billing_ref=same_billing_ref,
                separate_billing_ref=separate_billing_ref,
                parent_billing_ref=parent_billing_ref,
                id_from_footer=id_from_footer,
                hints=hints,
            )
        if rebuilt:
            tax_rows = [item for item in invoice.line_items if item.charge_type == ChargeType.TAX]
            invoice.line_items = rebuilt + tax_rows
    elif hints.billing_reference_preference == "same_as_service_id":
        items = invoice.line_items
        for item in items:
            if item.charge_type not in {ChargeType.TAX, ChargeType.FEE, ChargeType.SURCHARGE} and item.service_id:
                item.billing_reference = item.service_id
        invoice.line_items = items

    return invoice


def _enrich_preserved_line_items(
    invoice,
    document: LayoutDocument,
    profile: SupplierProfile | None,
    hints: LineItemHints,
) -> None:
    """Apply deterministic profile field repairs without replacing AI rows.

    Compact AI extraction already emits the delivered row set. For repeated
    marker layouts, a full rebuild can accidentally drop valid AI rows, but the
    same profile groups are still useful for correcting fields such as Rogers
    group tax and normalized plan names.
    """

    if not invoice.line_items:
        return

    # Runs first: later repairs (e.g. billing_reference mirroring) must see the
    # corrected service id. Rewrites invoice.line_items, so re-read it after.
    if (
        hints.line_item_granularity == "per_charge_row"
        and hints.service_id_preference == CHILD_IDENTIFIER_PREF
    ):
        _assign_child_service_ids(invoice, document, hints)

    items = invoice.line_items

    if hints.billing_reference_value_pattern:
        for item in items:
            if item.billing_reference:
                item.billing_reference = _apply_value_pattern(
                    item.billing_reference,
                    hints.billing_reference_value_pattern,
                )

    if hints.billing_reference_preference == "same_as_service_id":
        for item in items:
            if item.charge_type not in {ChargeType.TAX, ChargeType.FEE, ChargeType.SURCHARGE} and item.service_id:
                item.billing_reference = item.service_id

    invoice.line_items = items

    if not (
        hints.line_item_granularity == "per_total_group"
        and hints.service_id_preference in {
            "total_row_identifier",
            "parent_identifier",
            "first_identifier",
            "leftmost_identifier",
        }
    ):
        return

    groups = _extract_marker_groups(document, profile) or _extract_total_groups(document, hints.skip_row_keywords)
    if not groups or _should_expand_total_groups_to_charge_rows(groups):
        return

    same_billing_ref = hints.billing_reference_preference == "same_as_service_id"
    separate_billing_ref = hints.billing_reference_preference == "separate_column"
    parent_billing_ref = hints.billing_reference_preference == "parent_identifier"
    id_from_footer = hints.service_id_preference in {"total_row_identifier", "parent_identifier"}
    rebuilt = _line_items_from_total_groups(
        invoice,
        groups,
        same_billing_ref=same_billing_ref,
        separate_billing_ref=separate_billing_ref,
        parent_billing_ref=parent_billing_ref,
        id_from_footer=id_from_footer,
        hints=hints,
    )
    if not rebuilt:
        return

    if _total_row_columns_are_authoritative(hints):
        tax_rows = [item for item in invoice.line_items if item.charge_type == ChargeType.TAX]
        invoice.line_items = rebuilt + tax_rows
        return

    by_service: dict[str, LineItem] = {}
    for profile_item in rebuilt:
        key = _line_item_match_key(profile_item.service_id)
        if key and key not in by_service:
            by_service[key] = profile_item

    items = invoice.line_items
    for item in items:
        if item.charge_type in {ChargeType.TAX, ChargeType.FEE, ChargeType.SURCHARGE}:
            continue
        profile_item = by_service.get(_line_item_match_key(item.service_id))
        if profile_item is None:
            continue
        _overlay_profile_fields(item, profile_item, hints)
    invoice.line_items = items


def _service_id_column_bounds(document: LayoutDocument, hints: LineItemHints) -> tuple[float, float] | None:
    """The x-range of the Service-ID column, read from the printed table header.

    Derived from ``service_id_column_label`` rather than a magic coordinate, so
    it survives the differing left margins across a supplier's accounts.
    """
    label = (hints.service_id_column_label or "").strip().lower()
    if not label:
        return None
    for page in document.pages:
        for row in page.visual_rows:
            if row.row_role != "header":
                continue
            for cell in row.cells:
                if cell.text.strip().lower() == label:
                    # The identifiers sit under the header; allow a little slack
                    # for indentation of child rows.
                    return (cell.bbox.x0 - 4.0, cell.bbox.x1 + 40.0)
    return None


def _row_identifier_in_service_column(
    row: VisualRow,
    bounds: tuple[float, float] | None,
    pattern: re.Pattern | None,
) -> str | None:
    """The identifier this row *declares*, or None when it declares none.

    ``Total <id>`` rows re-print an identifier they do not own; they must never
    become the carried-forward service id, so they are skipped outright.
    """
    if row.row_role == "group_total" or not row.cells:
        return None
    for cell in row.cells:
        if bounds is not None and not (bounds[0] <= cell.bbox.x0 <= bounds[1]):
            continue
        text = cell.text.strip()
        if not text:
            continue
        if pattern is not None:
            match = pattern.match(text)
            if match:
                return (match.group(1) if match.groups() else match.group(0)).strip()
            continue
        if cell.role == "identifier":
            return text
        break  # only the leading cell of a row can declare its identifier
    return None


def _child_service_id_by_row(document: LayoutDocument, hints: LineItemHints) -> dict[str, str]:
    """Map every printed row id to the innermost identifier in force at that row.

    Layouts print a parent identifier, then its child identifiers, then the
    charge rows belonging to the last child. Walking rows in reading order and
    keeping the most recent declared identifier therefore yields the *child*,
    and falls back to the parent for services that have no children. The carry
    continues across page breaks; ``Total <id>`` rows never reset it.
    """
    bounds = _service_id_column_bounds(document, hints)
    pattern = None
    if hints.service_id_value_pattern:
        try:
            pattern = re.compile(hints.service_id_value_pattern)
        except re.error:
            pattern = None

    by_row: dict[str, str] = {}
    current: str | None = None
    for page in document.pages:
        for row in page.visual_rows:
            declared = _row_identifier_in_service_column(row, bounds, pattern)
            if declared:
                current = declared
            if current:
                by_row[row.row_id] = current
    return by_row


def _assign_child_service_ids(invoice, document: LayoutDocument, hints: LineItemHints) -> None:
    """Overwrite each delivered row's service_id with its innermost printed id.

    The AI reads values well but routinely picks the parent identifier (or none)
    when a layout nests child ids under it. Each delivered row carries the
    printed row it came from (``source_row_id``), so the correct id is a lookup.
    """
    by_row = _child_service_id_by_row(document, hints)
    if not by_row:
        return
    items = invoice.line_items
    for item in items:
        if item.charge_type in {ChargeType.TAX, ChargeType.FEE, ChargeType.SURCHARGE}:
            continue
        resolved = by_row.get(item.source_row_id or "")
        if resolved:
            item.service_id = resolved
    invoice.line_items = items


def _line_item_match_key(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "").strip().lower()


def _overlay_profile_fields(item: LineItem, profile_item: LineItem, hints: LineItemHints) -> None:
    if profile_item.billing_reference:
        item.billing_reference = profile_item.billing_reference
    if profile_item.amount is not None:
        item.amount = profile_item.amount
        item.unit_rate = profile_item.amount
    if hints.tax_source == "none":
        item.tax_amount = None
    elif hints.tax_source in {"label_amount", "table_tax_column", "group_total_minus_amount"}:
        item.tax_amount = profile_item.tax_amount
    if profile_item.billing_period_start:
        item.billing_period_start = profile_item.billing_period_start
    if profile_item.billing_period_end:
        item.billing_period_end = profile_item.billing_period_end
    if profile_item.currency:
        item.currency = profile_item.currency
    if item.charge_type == ChargeType.UNKNOWN and profile_item.charge_type != ChargeType.UNKNOWN:
        item.charge_type = profile_item.charge_type
    item.confidence = max(item.confidence, profile_item.confidence)


def _total_row_columns_are_authoritative(hints: LineItemHints) -> bool:
    return bool(
        hints.line_item_granularity == "per_total_group"
        and hints.service_id_preference in {"total_row_identifier", "parent_identifier"}
        and (
            hints.amount_source == "table_charges_column"
            or hints.tax_source == "table_tax_column"
        )
    )


def _should_expand_total_groups_to_charge_rows(groups: list[list[VisualRow]]) -> bool:
    """Detect subtotal-style groups that contain multiple real service rows.

    Some invoices list multiple services and then a location/account subtotal
    rather than one service footer per service. In that shape, a per-total-group
    profile would collapse multiple deliverable service rows into one subtotal.
    We only expand when the subtotal identifier is not one of the service starts;
    parent-total layouts where the footer matches a parent service ID stay as
    grouped totals.
    """
    for group in groups:
        service_ids = {
            service_id
            for row in group
            if row.row_role == "service_start"
            for service_id in [_first_identifier([row])]
            if service_id
        }
        footer_id = _footer_identifier(group)
        if footer_id and footer_id in service_ids:
            continue
        if len(_billable_period_service_ids(group)) > 1:
            return True
    return False


def _billable_period_service_ids(group: list[VisualRow]) -> set[str]:
    service_ids: set[str] = set()
    current_service_id: str | None = None
    for row in group:
        if row.row_role == "service_start":
            current_service_id = _first_identifier([row])
            continue
        if row.row_role != "period_amount" or not current_service_id:
            continue
        amount = _row_amount(row)
        if amount is not None and amount != 0:
            service_ids.add(current_service_id)
    return service_ids


def _extract_total_groups(document: LayoutDocument, skip_keywords: list[str]) -> list[list[VisualRow]]:
    skip = [keyword.lower() for keyword in skip_keywords or []]
    rows: list[VisualRow] = []
    in_section = False
    for page in document.pages:
        for row in page.visual_rows:
            text_low = row.text.lower()
            if row.row_role == "anchor":
                in_section = True
                continue
            if in_section and any(keyword and keyword in text_low for keyword in skip):
                continue
            if in_section:
                rows.append(row)

    groups: list[list[VisualRow]] = []
    current: list[VisualRow] = []
    for row in rows:
        if row.row_role == "service_start":
            if current:
                current.append(row)
            else:
                current = [row]
            continue
        if current:
            current.append(row)
            if row.row_role == "group_total":
                groups.append(current)
                current = []
    if current:
        groups.append(current)
    return [
        group for group in groups
        if (_footer_identifier(group) or _first_identifier(group))
        and _footer_amount(group) is not None
        and any(row.row_role == "period_amount" for row in group)
    ]


def _apply_excluded_total_hints(invoice, document: LayoutDocument, profile: SupplierProfile | None) -> None:
    """Read printed excluded-section totals from the layout into invoice.fields.

    A field the profile marks ``role="excluded_total"`` names a printed total for
    a section the deliverable does not cover (e.g. Lumen's "Total Account Level
    Charges"). The compact extractor only emits a fixed set of scalar fields, so
    this value is read deterministically from the labelled row — the Total column,
    the last money value on that row — rather than by AI. The reconciler then
    subtracts it from the target.
    """
    overrides = getattr(profile, "field_overrides", None) or []
    labels = [
        (field.name, field.label_hint)
        for field in overrides
        if getattr(field, "role", None) == "excluded_total"
        and getattr(field, "scope", None) == "document"
        and (field.label_hint or "").strip()
    ]
    if not labels:
        return

    for name, label in labels:
        if invoice.fields.get(name) is not None:
            continue
        needle = label.strip().lower()
        for page in document.pages:
            for row in page.visual_rows:
                if needle not in row.text.strip().lower():
                    continue
                amounts = _decimal_amounts(row.text)
                if amounts:
                    invoice.fields[name] = str(amounts[-1])  # the Total column
                    break
            if invoice.fields.get(name) is not None:
                break


def _apply_header_date_hints(invoice, document: LayoutDocument, profile: SupplierProfile | None) -> None:
    if profile is None:
        return
    labels: dict[str, tuple[str, str | None]] = {}
    for override in profile.field_overrides or []:
        if (
            override.scope == "document"
            and override.name in {"invoice_date", "due_date", "billing_period_start", "billing_period_end"}
            and override.label_hint
        ):
            labels[override.name] = (override.label_hint, override.date_format)
    for field_name, (label, date_format) in labels.items():
        value = _date_after_label(document, label, date_format=date_format)
        if value is not None:
            if field_name == "billing_period_end":
                value = value.replace(day=monthrange(value.year, value.month)[1])
            setattr(invoice.header, field_name, value)
            if hasattr(invoice, "fields"):
                invoice.fields[field_name] = value
            if field_name == "invoice_date" and hasattr(invoice, "warnings"):
                invoice.warnings = [
                    warning
                    for warning in invoice.warnings
                    if warning != SYNTHETIC_INVOICE_DATE_WARNING
                ]


def _date_after_label(
    document: LayoutDocument,
    label: str,
    *,
    date_format: str | None = None,
) -> date | None:
    label_re = re.escape(label.strip()).replace(r"\ ", r"\s+")
    label_pattern = re.compile(label_re, re.I)
    inline_pattern = re.compile(label_re + r"\s*:?\s*(" + _DATE_VALUE_RE + r")", re.I)
    for page in document.pages:
        rows = list(page.visual_rows)
        for index, row in enumerate(rows):
            match = inline_pattern.search(row.text)
            date_text = match.group(1) if match else None
            if not date_text:
                date_text = _date_in_label_cells(row, rows[index + 1:index + 5], label_pattern)
            if date_text:
                parsed = _parse_date(date_text, date_format=date_format)
                if parsed is not None:
                    return parsed
    return None


def _date_in_label_cells(
    row: VisualRow,
    following_rows: list[VisualRow],
    label_pattern: re.Pattern,
) -> str | None:
    if label_pattern.search(row.text):
        same_row = _first_date_text(row.text)
        if same_row:
            return same_row
        label_cell = _matching_label_cell(row, label_pattern)
        if label_cell is not None:
            for next_row in following_rows:
                for cell in next_row.cells:
                    if _cells_overlap_horizontally(label_cell, cell):
                        date_text = _first_date_text(cell.text)
                        if date_text:
                            return date_text
                date_text = _first_date_text(next_row.text)
                if date_text:
                    return date_text
        for next_row in following_rows:
            date_text = _first_date_text(next_row.text)
            if date_text:
                return date_text
    return None


def _matching_label_cell(row: VisualRow, label_pattern: re.Pattern):
    for cell in row.cells:
        if label_pattern.search(cell.text):
            return cell
    return None


def _cells_overlap_horizontally(left, right) -> bool:
    left_box = left.normalized_bbox or left.bbox
    right_box = right.normalized_bbox or right.bbox
    overlap = min(left_box.x1, right_box.x1) - max(left_box.x0, right_box.x0)
    if overlap <= 0:
        return False
    return overlap >= min(left_box.x1 - left_box.x0, right_box.x1 - right_box.x0) * 0.35


def _first_date_text(text: str) -> str | None:
    match = re.search(_DATE_VALUE_RE, text, re.I)
    return match.group(0) if match else None


def _extract_marker_groups(document: LayoutDocument, profile: SupplierProfile | None) -> list[list[VisualRow]]:
    if profile is None:
        return []
    start_markers = marker_terms(profile.document_structure.detail_start_marker)
    end_markers = marker_terms(profile.document_structure.detail_end_marker)
    if not start_markers or not end_markers:
        return []
    groups: list[list[VisualRow]] = []
    current: list[VisualRow] = []
    in_group = False
    for page in document.pages:
        for row in page.visual_rows:
            if marker_in_text(start_markers, row.text):
                if current:
                    groups.append(current)
                current = [row]
                in_group = True
                if marker_in_text(end_markers, row.text):
                    groups.append(current)
                    current = []
                    in_group = False
                continue
            if not in_group:
                continue
            current.append(row)
            if marker_in_text(end_markers, row.text):
                groups.append(current)
                current = []
                in_group = False
    if current:
        groups.append(current)
    return groups


def _line_items_from_service_groups(invoice, document: LayoutDocument, profile, hints: LineItemHints) -> list[LineItem]:
    table_items = _line_items_from_summary_table(invoice, document, hints)
    if table_items:
        return table_items
    return _line_items_from_labeled_service_blocks(invoice, document, hints)


def _line_items_from_labeled_service_blocks(invoice, document: LayoutDocument, hints: LineItemHints) -> list[LineItem]:
    service_label = hints.service_id_column_label
    billing_label = hints.billing_reference_column_label
    amount_label = hints.amount_column_label
    if not service_label or not billing_label or not amount_label:
        return []

    currency = invoice.header.currency or "USD"
    current_service_id: str | None = None
    current_billing_ref: str | None = None
    current_tax: Decimal | None = None
    rebuilt: list[LineItem] = []
    for page in document.pages:
        for row in page.visual_rows:
            text = _squash(row.text)
            if service_label in text:
                value = _value_after_label(text, service_label, _SERVICE_STOP_LABELS)
                if value:
                    current_service_id = _apply_value_pattern(value, hints.service_id_value_pattern)
                    current_billing_ref = None
                    current_tax = None
            if billing_label in text:
                value = _value_after_label(text, billing_label, _BILLING_STOP_LABELS)
                if value:
                    current_billing_ref = _apply_value_pattern(value, hints.billing_reference_value_pattern)
            if hints.tax_amount_column_label and hints.tax_amount_column_label in text:
                tax = _amount_after_label(row, hints.tax_amount_column_label)
                if tax is not None:
                    current_tax = tax
            if amount_label not in text:
                continue
            amount = _row_amount(row)
            if amount is None or not current_service_id:
                continue
            if hints.amount_source == "label_amount_minus_tax" and current_tax is not None:
                amount -= current_tax
            rebuilt.append(LineItem(
                line_number=len(rebuilt) + 1,
                service_id=current_service_id,
                billing_reference=current_billing_ref or current_service_id,
                description=current_billing_ref or text,
                charge_type=ChargeType.RECURRING,
                amount=amount,
                tax_amount=current_tax,
                currency=currency,
                source_page=row.page,
                confidence=0.95,
            ))
            current_tax = None
    return rebuilt


def _line_items_from_summary_table(invoice, document: LayoutDocument, hints: LineItemHints) -> list[LineItem]:
    if hints.amount_source != "table_charges_column":
        return []
    currency = invoice.header.currency or "USD"
    rebuilt: list[LineItem] = []
    in_section = False
    for page in document.pages:
        rows = page.visual_rows
        for index, row in enumerate(rows):
            text = _squash(row.text)
            if any(anchor and anchor in text for anchor in hints.detail_table_anchors or []):
                in_section = True
                continue
            if in_section and any(
                keyword and keyword.lower() in text.lower()
                for keyword in hints.skip_row_keywords or []
            ):
                if rebuilt:
                    return rebuilt
                continue
            if not in_section or row.row_role != "service_start":
                continue
            service_id = _first_identifier([row])
            if not service_id:
                continue
            amount_cells = [cell for cell in row.cells if cell.role == "amount"]
            if len(amount_cells) < 3:
                continue
            tax_amount = _parse_decimal(amount_cells[-3].text) if hints.tax_source == "table_tax_column" else None
            amount = _parse_decimal(amount_cells[-2].text)
            if amount is None:
                continue
            text_cells = [cell.text.strip() for cell in row.cells if cell.role == "text" and cell.text.strip()]
            continuation_rows = _table_continuation_rows(rows[index + 1:], hints)
            billing_reference = _table_billing_reference(text_cells, continuation_rows)
            rebuilt.append(LineItem(
                line_number=len(rebuilt) + 1,
                service_id=service_id,
                billing_reference=billing_reference or service_id,
                description=billing_reference or row.text,
                charge_type=ChargeType.RECURRING,
                amount=amount,
                tax_amount=tax_amount,
                currency=currency,
                source_page=row.page,
                confidence=0.95,
            ))
    return rebuilt


def _resolve_billing_reference(
    group: list[VisualRow],
    *,
    service_id: str,
    same_billing_ref: bool,
    separate_billing_ref: bool,
    parent_billing_ref: bool,
    matching_ai_item: LineItem | None,
) -> str:
    if same_billing_ref:
        return service_id
    if separate_billing_ref:
        second = _second_identifier(group)
        if second:
            return second
    if parent_billing_ref:
        first = _first_identifier(group)
        footer = _footer_identifier(group)
        if footer and service_id == footer and first and first != footer:
            return first
        if first and service_id == first and footer and footer != first:
            return footer
        if first and footer and first != footer:
            return first if service_id == footer else footer
    if matching_ai_item and matching_ai_item.billing_reference:
        return matching_ai_item.billing_reference
    return service_id


def _line_items_from_total_groups(
    invoice,
    groups: list[list[VisualRow]],
    *,
    same_billing_ref: bool,
    separate_billing_ref: bool = False,
    parent_billing_ref: bool = False,
    id_from_footer: bool,
    hints: LineItemHints | None = None,
) -> list[LineItem]:
    existing_service_rows = [
        item for item in invoice.line_items
        if item.charge_type not in {ChargeType.TAX, ChargeType.FEE, ChargeType.SURCHARGE}
    ]

    currency = existing_service_rows[0].currency if existing_service_rows else invoice.header.currency or "USD"
    rebuilt: list[LineItem] = []
    for group in groups:
        group_text = _group_text(group)
        service_id = (
            _value_from_group_pattern(group, hints.service_id_value_pattern)
            if hints and hints.service_id_value_pattern
            else (_footer_identifier(group) if id_from_footer else _first_identifier(group))
        )
        if service_id and hints:
            service_id = _apply_value_pattern(service_id, hints.service_id_value_pattern)
        amount = _amount_from_group(group, hints)
        footer_amount = _footer_amount(group)
        if not service_id or amount is None or amount == 0:
            continue
        tax_amount = _tax_from_group(group, amount, footer_amount, hints)
        period_start, period_end = _first_billing_period(group)
        matching_ai_item = _best_matching_ai_item(existing_service_rows, group, amount)
        description = _description_from_group(group)
        billing_reference = (
            _value_from_group_pattern(group, hints.billing_reference_value_pattern)
            if hints and hints.billing_reference_value_pattern
            else _resolve_billing_reference(
                group,
                service_id=service_id,
                same_billing_ref=same_billing_ref,
                separate_billing_ref=separate_billing_ref,
                parent_billing_ref=parent_billing_ref,
                matching_ai_item=matching_ai_item,
            )
        )
        if billing_reference and hints:
            billing_reference = _apply_value_pattern(billing_reference, hints.billing_reference_value_pattern)
        if same_billing_ref:
            billing_reference = service_id
        rebuilt.append(
            LineItem(
                line_number=len(rebuilt) + 1,
                service_id=service_id,
                billing_reference=billing_reference,
                description=description or group_text[:120],
                charge_type=matching_ai_item.charge_type if matching_ai_item else ChargeType.RECURRING,
                amount=amount,
                tax_amount=tax_amount,
                currency=(matching_ai_item.currency if matching_ai_item else currency),
                billing_period_start=period_start or (
                    matching_ai_item.billing_period_start if matching_ai_item else None
                ),
                billing_period_end=period_end or (matching_ai_item.billing_period_end if matching_ai_item else None),
                quantity=matching_ai_item.quantity if matching_ai_item else Decimal("1"),
                unit_rate=amount,
                source_page=group[0].page,
                confidence=matching_ai_item.confidence if matching_ai_item else 0.9,
            )
        )
    return rebuilt


def _charge_line_items_from_total_groups(
    invoice,
    groups: list[list[VisualRow]],
    *,
    same_billing_ref: bool,
    separate_billing_ref: bool = False,
    parent_billing_ref: bool = False,
    id_from_footer: bool,
) -> list[LineItem]:
    existing_service_rows = [
        item for item in invoice.line_items
        if item.charge_type not in {ChargeType.TAX, ChargeType.FEE, ChargeType.SURCHARGE}
    ]
    if not existing_service_rows:
        return []

    currency = existing_service_rows[0].currency if existing_service_rows else invoice.header.currency
    rebuilt: list[LineItem] = []
    fee_total = Decimal("0")
    current_item: LineItem | None = None
    for group in groups:
        group_service_id = _footer_identifier(group) if id_from_footer else _first_identifier(group)
        if not group_service_id:
            continue
        context: list[VisualRow] = []
        current_item = None
        service_start = group[0]
        for row in group[1:]:
            if row.row_role == "group_total":
                break
            if row.row_role == "service_start":
                service_start = row
                context = []
                continue
            if row.row_role == "period_amount":
                service_id = group_service_id if id_from_footer else _first_identifier([service_start])
                if not service_id:
                    context = []
                    continue
                amount = _row_amount(row)
                if amount is None:
                    context = []
                    continue
                period_start, period_end = _period_from_row(row)
                matching_ai_item = _best_matching_ai_item(existing_service_rows, [*context, row, group[-1]], amount)
                billing_reference = _resolve_billing_reference(
                    group,
                    service_id=service_id,
                    same_billing_ref=same_billing_ref,
                    separate_billing_ref=separate_billing_ref,
                    parent_billing_ref=parent_billing_ref,
                    matching_ai_item=matching_ai_item,
                )
                rebuilt.append(
                    LineItem(
                        line_number=len(rebuilt) + 1,
                        service_id=service_id,
                        billing_reference=billing_reference,
                        description=_description_from_rows([*context, row]),
                        charge_type=matching_ai_item.charge_type if matching_ai_item else ChargeType.RECURRING,
                        amount=amount,
                        tax_amount=None,
                        currency=(matching_ai_item.currency if matching_ai_item else currency),
                        billing_period_start=period_start or (
                            matching_ai_item.billing_period_start if matching_ai_item else None
                        ),
                        billing_period_end=period_end or (
                            matching_ai_item.billing_period_end if matching_ai_item else None
                        ),
                        quantity=_quantity_from_period_row(row) or (
                            matching_ai_item.quantity if matching_ai_item else None
                        ),
                        unit_rate=amount,
                        source_page=row.page,
                        confidence=matching_ai_item.confidence if matching_ai_item else 0.9,
                    )
                )
                current_item = rebuilt[-1]
                context = []
            elif row.row_role in {"detail", "header", "tax_total"}:
                amount = _row_amount(row)
                low = row.text.lower()
                if amount is not None and current_item is not None and ("tax" in low or "vat" in low):
                    current_item.tax_amount = (current_item.tax_amount or Decimal("0")) + amount
                elif amount is not None and (
                    "surcharge" in low or re.search(r"\bfee\b", low) or re.search(r"\bfees\b", low)
                ):
                    fee_total += amount
                elif current_item is None:
                    context.append(row)
    if fee_total:
        invoice.fees = fee_total
    return rebuilt


# ---------------------------------------------------------------------------
# Layout-only grouped line items — the deterministic primitive a MODEL replays.
# The AI ground-truth path (apply_layout_profile_hints) derives service_id +
# amount from the SAME groups, so a model carrying a grouping strategy
# reproduces the delivered (Temforce) columns by construction.
# ---------------------------------------------------------------------------

GROUPING_GRANULARITIES = {"per_total_group", "per_charge_row"}
GROUPING_ID_PREFS = {
    "total_row_identifier", "parent_identifier", "first_identifier", "leftmost_identifier",
}
# Selects the innermost printed identifier rather than a grouping/parent one.
# Unlike the prefs above it does NOT rebuild rows, so it is not in GROUPING_ID_PREFS.
CHILD_IDENTIFIER_PREF = "child_identifier"


def has_grouping_policy(hints: LineItemHints | None) -> bool:
    """Whether a profile declares a deterministic grouping policy."""
    return bool(
        hints
        and hints.line_item_granularity in GROUPING_GRANULARITIES
        and hints.service_id_preference in GROUPING_ID_PREFS
    )


def build_grouped_line_items(
    document: LayoutDocument,
    *,
    granularity: str,
    service_id_preference: str,
    billing_reference_preference: str | None = None,
    anchors: list[str] | None = None,
    skip_keywords: list[str] | None = None,
) -> list[LineItem]:
    """Build delivered line items from the layout's service groups, deterministically.

    Honours the declared granularity: ``per_total_group`` => one row per service
    group (amount = group total); ``per_charge_row`` => one row per charge within
    each group. Returns [] when the policy is incomplete. No AI involved.
    """
    if granularity not in GROUPING_GRANULARITIES or service_id_preference not in GROUPING_ID_PREFS:
        return []
    mark_anchor_rows(document, list(anchors or []))
    groups = _extract_total_groups(document, list(skip_keywords or []))
    same_billing_ref = billing_reference_preference == "same_as_service_id"
    id_from_footer = service_id_preference in {"total_row_identifier", "parent_identifier"}
    if granularity == "per_charge_row":
        return _charge_items_layout_only(groups, same_billing_ref=same_billing_ref, id_from_footer=id_from_footer)
    return _total_items_layout_only(groups, same_billing_ref=same_billing_ref, id_from_footer=id_from_footer)


def _total_items_layout_only(groups, *, same_billing_ref: bool, id_from_footer: bool) -> list[LineItem]:
    rebuilt: list[LineItem] = []
    for group in groups:
        service_id = _footer_identifier(group) if id_from_footer else _first_identifier(group)
        amount = _footer_amount(group)
        if not service_id or amount is None or amount == 0:
            continue
        period_start, period_end = _first_billing_period(group)
        rebuilt.append(LineItem(
            line_number=len(rebuilt) + 1, service_id=service_id, billing_reference=service_id,
            description=_description_from_group(group), charge_type=ChargeType.RECURRING,
            amount=amount, tax_amount=None, currency="USD",
            billing_period_start=period_start, billing_period_end=period_end,
            quantity=Decimal("1"), unit_rate=amount, source_page=group[0].page, confidence=0.9,
        ))
    return rebuilt


def _charge_items_layout_only(groups, *, same_billing_ref: bool, id_from_footer: bool) -> list[LineItem]:
    rebuilt: list[LineItem] = []
    for group in groups:
        group_service_id = _footer_identifier(group) if id_from_footer else _first_identifier(group)
        service_start = group[0]
        context: list[VisualRow] = []
        for row in group[1:]:
            if row.row_role == "group_total":
                break
            if row.row_role == "service_start":
                service_start = row
                context = []
                continue
            if row.row_role == "period_amount":
                service_id = group_service_id if id_from_footer else _first_identifier([service_start])
                if not service_id:
                    context = []
                    continue
                amount = _row_amount(row)
                if amount is None:
                    context = []
                    continue
                period_start, period_end = _period_from_row(row)
                rebuilt.append(LineItem(
                    line_number=len(rebuilt) + 1, service_id=service_id, billing_reference=service_id,
                    description=_description_from_rows([*context, row]), charge_type=ChargeType.RECURRING,
                    amount=amount, tax_amount=None, currency="USD",
                    billing_period_start=period_start, billing_period_end=period_end,
                    quantity=_quantity_from_period_row(row), unit_rate=amount,
                    source_page=row.page, confidence=0.9,
                ))
                context = []
            elif row.row_role in {"detail", "header", "tax_total"}:
                context.append(row)
    return rebuilt


def _group_text(group: list[VisualRow]) -> str:
    return "\n".join(row.text for row in group)


def _squash(value: str) -> str:
    return " ".join(str(value or "").split())


def _value_after_label(text: str, label: str, stop_labels: tuple[str, ...]) -> str | None:
    if label not in text:
        return None
    value = text.split(label, 1)[1].strip()
    for stop in stop_labels:
        if stop in value:
            value = value.split(stop, 1)[0].strip()
    return value.strip(" :") or None


def _apply_value_pattern(value: str | None, pattern: str | None) -> str | None:
    if not value:
        return value
    if not pattern:
        return value.strip()
    try:
        match = re.search(pattern, value)
    except re.error:
        return value.strip()
    if not match:
        return value.strip()
    return match.group(1 if match.lastindex else 0).strip()


def _value_from_group_pattern(group: list[VisualRow], pattern: str | None) -> str | None:
    if not pattern:
        return None
    try:
        match = re.search(pattern, _group_text(group), re.S)
    except re.error:
        return None
    return match.group(1 if match.lastindex else 0).strip() if match else None


def _amount_after_label(row: VisualRow, label: str) -> Decimal | None:
    text = _squash(row.text)
    if label not in text:
        return None
    amount = _row_amount(row)
    if amount is not None:
        return amount
    suffix = text.split(label, 1)[1]
    amount = _parse_decimal(suffix, first=True)
    return amount if amount is not None else _row_amount(row)


def _amount_from_group(group: list[VisualRow], hints: LineItemHints | None) -> Decimal | None:
    if hints and hints.amount_source == "table_charges_column":
        amount, _tax_amount, _total_amount = _footer_amount_parts(group)
        if amount is not None:
            return amount
    if hints and hints.amount_source == "label_amount" and hints.amount_column_label:
        for row in group:
            if hints.amount_column_label in row.text:
                amount = _amount_after_label(row, hints.amount_column_label)
                if amount is not None:
                    return amount
    if hints and hints.amount_source == "first_amount":
        for row in group:
            text_low = row.text.lower()
            if any(word in text_low for word in ("tax", "surcharge", "fee", "total")):
                continue
            amount = _row_amount(row)
            if amount is not None:
                return amount
    return _footer_amount(group)


def _tax_from_group(
    group: list[VisualRow],
    amount: Decimal,
    footer_amount: Decimal | None,
    hints: LineItemHints | None,
) -> Decimal | None:
    if hints is None:
        return None
    if hints.tax_source == "none":
        return None
    if hints.tax_source == "label_amount" and hints.tax_amount_column_label:
        total = Decimal("0")
        found = False
        for row in group:
            if hints.tax_amount_column_label in row.text:
                tax = _amount_after_label(row, hints.tax_amount_column_label)
                if tax is not None:
                    total += tax
                    found = True
        return total if found else None
    if hints.tax_source == "table_tax_column":
        _amount, tax_amount, _total_amount = _footer_amount_parts(group)
        return tax_amount
    if hints.tax_source == "group_total_minus_amount" and footer_amount is not None:
        tax = footer_amount - amount
        return tax if tax != 0 else None
    return None


def _table_continuation_rows(rows: list[VisualRow], hints: LineItemHints) -> list[str]:
    values: list[str] = []
    for row in rows:
        if row.row_role != "detail":
            break
        text = _squash(row.text)
        if not text or any(keyword and keyword.lower() in text.lower() for keyword in hints.skip_row_keywords or []):
            break
        if any(cell.role == "amount" for cell in row.cells):
            break
        values.append(text)
    return values


def _table_billing_reference(text_cells: list[str], continuation_rows: list[str] | None = None) -> str:
    if not text_cells:
        return ""
    parts = [text_cells[0], *(continuation_rows or [])]
    return " ".join(part for part in parts if part).strip()


def _best_matching_ai_item(items: list[LineItem], group: list[VisualRow], amount: Decimal) -> LineItem | None:
    footer_id = _footer_identifier(group)
    group_text = "\n".join(row.text for row in group)
    for item in items:
        if footer_id and (item.service_id == footer_id or item.billing_reference == footer_id):
            return item
    for item in items:
        if item.service_id and item.service_id in group_text:
            return item
        if item.billing_reference and item.billing_reference in group_text:
            return item
    for item in items:
        if item.amount == amount:
            return item
    return items[0] if items else None


def _footer_identifier(group: list[VisualRow]) -> str | None:
    footer = _footer_row(group)
    if not footer:
        return None
    match = re.search(r"\btotal\s+([A-Z0-9][A-Z0-9\-/.]{4,})\b", footer.text, re.I)
    return match.group(1) if match else None


def _first_identifier(group: list[VisualRow]) -> str | None:
    for row in group:
        for cell in row.cells:
            if cell.role == "identifier":
                return cell.text.strip()
    return None


def _second_identifier(group: list[VisualRow]) -> str | None:
    seen_first = False
    for row in group:
        for cell in row.cells:
            if cell.role != "identifier":
                continue
            text = cell.text.strip()
            if not text:
                continue
            if not seen_first:
                seen_first = True
                continue
            return text
    return None


def _footer_amount(group: list[VisualRow]) -> Decimal | None:
    _amount, _tax_amount, total_amount = _footer_amount_parts(group)
    if total_amount is not None:
        return total_amount
    footer = _footer_row(group)
    if not footer:
        return None
    amount_cells = [cell for cell in footer.cells if cell.role == "amount"]
    candidates = [cell.text for cell in amount_cells] or [footer.text]
    for candidate in reversed(candidates):
        amount = _parse_decimal(candidate)
        if amount is not None:
            return amount
    return None


def _footer_amount_parts(group: list[VisualRow]) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    footer = _footer_row(group)
    if not footer:
        return None, None, None
    identifier = _footer_identifier(group)
    segment = _total_segment_for_identifier(footer.text, identifier) if identifier else None
    amounts = _decimal_amounts(segment or footer.text)
    if not amounts:
        return None, None, None
    if len(amounts) >= 3:
        return amounts[-3], amounts[-2], amounts[-1]
    return amounts[-1], None, amounts[-1]


def _total_segment_for_identifier(text: str, identifier: str | None) -> str | None:
    if not identifier:
        return None
    pattern = re.compile(r"\btotal\s+([A-Z0-9][A-Z0-9\-/.]{4,})\b", re.I)
    matches = list(pattern.finditer(text))
    for index, match in enumerate(matches):
        if match.group(1).strip().lower() != identifier.strip().lower():
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return text[match.start():end]
    return None


def _decimal_amounts(value: str) -> list[Decimal]:
    amounts: list[Decimal] = []
    for raw in re.findall(r"[-(]?\s*\$?\s*\d[\d,]*\.\d{2}\)?", value):
        amount = _parse_decimal(raw)
        if amount is not None:
            amounts.append(amount)
    return amounts


def _footer_row(group: list[VisualRow]) -> VisualRow | None:
    if group and "total" in group[-1].text.lower() and _row_amount(group[-1]) is not None:
        return group[-1]
    footer = next((row for row in reversed(group) if row.row_role == "group_total"), None)
    if footer is not None:
        return footer
    return next(
        (
            row for row in reversed(group)
            if "total" in row.text.lower() and _row_amount(row) is not None
        ),
        None,
    )


def _first_billing_period(group: list[VisualRow]):
    for row in group:
        for cell in row.cells:
            if cell.role != "billing_period":
                continue
            parts = _billing_period_parts(cell.text)
            start = _parse_date(parts[0]) if parts else None
            end = _parse_date(parts[1]) if len(parts) > 1 else None
            return start, end
    return None, None


def _period_from_row(row: VisualRow):
    for cell in row.cells:
        if cell.role != "billing_period":
            continue
        parts = _billing_period_parts(cell.text)
        start = _parse_date(parts[0]) if parts else None
        end = _parse_date(parts[1]) if len(parts) > 1 else None
        return start, end
    return None, None


def _row_amount(row: VisualRow) -> Decimal | None:
    amount_cells = [cell for cell in row.cells if cell.role == "amount"]
    for cell in reversed(amount_cells):
        amount = _parse_decimal(cell.text)
        if amount is not None:
            return amount
    return None


def _quantity_from_period_row(row: VisualRow) -> Decimal | None:
    amount_cells = [cell for cell in row.cells if cell.role == "amount"]
    if len(amount_cells) >= 3 and re.fullmatch(r"\d+(?:\.\d+)?", amount_cells[-3].text.strip()):
        return _parse_decimal(amount_cells[-3].text)
    if len(amount_cells) >= 2:
        return _parse_decimal(amount_cells[-2].text)
    return None


def _billing_period_parts(value: str) -> list[str]:
    parts = re.split(r"\s+-\s+", value.strip(), maxsplit=1)
    if len(parts) > 1:
        return parts
    matches = re.findall(r"\d{1,2}-[A-Za-z]{3}-\d{2,4}|\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}", value)
    return matches[:2] if len(matches) >= 2 else parts


def _description_from_group(group: list[VisualRow]) -> str:
    rows = [
        row.text for row in group
        if (row.row_role in {"detail", "period_amount"} and "Loc " in row.text)
        or row.row_role == "period_amount"
    ]
    if not rows:
        rows = [row.text for row in group if row.row_role == "detail"]
    return "; ".join(rows)[:500] or group[0].text[:120]


def _description_from_rows(rows: list[VisualRow]) -> str:
    return "; ".join(row.text for row in rows if row.row_role != "header")[:500]


def _parse_date(value: str | None, *, date_format: str | None = None):
    if not value:
        return None
    clean = value.strip()
    candidates = [clean]
    without_dotted_month = re.sub(r"\b([A-Za-z]{3,9})\.(?=\s+\d)", r"\1", clean)
    if without_dotted_month != clean:
        candidates.append(without_dotted_month)
    formats = ((date_format,) if date_format else ()) + _DATE_FORMATS
    for candidate in candidates:
        for fmt in formats:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def _parse_decimal(value: str, *, first: bool = False) -> Decimal | None:
    matches = re.findall(r"[-(]?\s*[$€£]?\s*\d[\d,]*(?:\.\d+)?\)?", value)
    if not matches:
        return None
    raw = matches[0] if first else matches[-1]
    negative = raw.strip().startswith("(") and raw.strip().endswith(")")
    cleaned = re.sub(r"[^\d,.\-]", "", raw)
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", "")
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    return -amount if negative and amount > 0 else amount
