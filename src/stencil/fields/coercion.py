"""Coerce raw extraction values to typed Python values per FieldDef."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from stencil.fields.currency import normalize_currency_code
from stencil.fields.schema import FieldDef, FieldType

DOCUMENT_DATE_FIELDS = frozenset({
    "invoice_date", "due_date", "billing_period_start", "billing_period_end",
})
ROW_DATE_FIELDS = frozenset({"billing_period_start", "billing_period_end"})

_FALLBACK_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%d %b %Y",
    "%d %b %y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%d.%m.%Y",
    "%b %d, %Y",
    "%b. %d, %Y",
    "%d %B %Y",
)


def _date_text_variants(text: str) -> list[str]:
    variants = [text]
    without_dotted_month = re.sub(r"\b([A-Za-z]{3,9})\.(?=\s+\d)", r"\1", text)
    if without_dotted_month != text:
        variants.append(without_dotted_month)
    return variants


def parse_date_value(value: Any, *, date_format: str | None = None) -> date | None:
    """Parse a date from AI output, stored JSON, or deliverable strings.

    Returns None for missing, blank, or unparseable values (never raises).
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text or text == ".":
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    formats = ([date_format] if date_format else []) + list(_FALLBACK_DATE_FORMATS)
    for candidate in _date_text_variants(text):
        for fmt in formats:
            try:
                return datetime.strptime(candidate, fmt).date()
            except (ValueError, TypeError):
                continue
    match = re.search(
        r"\d{1,2}[-/ ][A-Za-z]{3,9}\.?[-/ ]\d{2,4}"
        r"|\d{4}-\d{2}-\d{2}"
        r"|\d{1,2}[/.]\d{1,2}[/.]\d{2,4}"
        r"|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{2,4}",
        text,
    )
    if match and match.group(0) != text:
        return parse_date_value(match.group(0), date_format=date_format)
    return None


def coerce_field_value(
    field: FieldDef,
    value: Any,
    *,
    currency_rules: Any = None,
    currency_fallback: str | None = None,
) -> Any:
    if value is None:
        return None
    if field.name == "supplier_aliases":
        if isinstance(value, list):
            return value
        return [str(value)] if value else []
    if field.name == "currency":
        return normalize_currency_code(value, rules=currency_rules, fallback=currency_fallback).code
    if field.type == FieldType.STRING:
        return str(value)
    if field.type == FieldType.DATE:
        return parse_date_value(value, date_format=field.date_format)
    if field.type == FieldType.INTEGER:
        return int(value)
    if field.type in (FieldType.NUMBER, FieldType.CURRENCY):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    if field.type == FieldType.ENUM:
        return str(value)
    return value
