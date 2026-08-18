"""Formatting helpers for user-facing deliverables."""

from __future__ import annotations

from datetime import date, datetime

DELIVERABLE_DATE_FORMAT = "%m/%d/%Y"


def format_deliverable_date(value: date | datetime | str | None) -> str | None:
    """Format a date for Excel, canonical JSON, and manifest output."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().strftime(DELIVERABLE_DATE_FORMAT)
    if isinstance(value, date):
        return value.strftime(DELIVERABLE_DATE_FORMAT)
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).strftime(DELIVERABLE_DATE_FORMAT)
    except ValueError:
        return text


def format_deliverable_invoice_dates(data: dict) -> dict:
    """Rewrite date fields in a canonical/extracted document dict to MM/DD/YYYY."""
    header = data.get("header")
    if isinstance(header, dict):
        for key in (
            "invoice_date",
            "due_date",
            "billing_period_start",
            "billing_period_end",
        ):
            if key in header and header[key] is not None:
                header[key] = format_deliverable_date(header[key])

    fields = data.get("fields")
    if isinstance(fields, dict):
        for key in (
            "invoice_date",
            "due_date",
            "billing_period_start",
            "billing_period_end",
        ):
            if key in fields and fields[key] is not None:
                fields[key] = format_deliverable_date(fields[key])

    line_items = data.get("line_items") or data.get("rows")
    if isinstance(line_items, list):
        for item in line_items:
            if not isinstance(item, dict):
                continue
            for key in ("billing_period_start", "billing_period_end"):
                if key in item and item[key] is not None:
                    item[key] = format_deliverable_date(item[key])

    return data
