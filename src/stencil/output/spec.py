"""Configurable output specification — the customer-defined deliverable contract.

An ``OutputSpec`` is configuration (like a supplier profile): it declares which
columns the customer's downstream system expects, in order, and how each column
maps to canonical invoice data. TemForce's 8-column format is just the first
shipped spec, not a hardcoded constant.

This module is a leaf — it imports nothing else from ``stencil.output`` so
the writer, mapper, diff, and loader can all depend on it without cycles.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OutputColumn(BaseModel):
    """One column in the delivered output."""

    header: str = Field(..., description="Column header written to the deliverable")
    source: str = Field(
        ...,
        description=(
            "Where the value comes from: a canonical path (e.g. 'header.invoice_date', "
            "'line_item.amount', 'subtotal') or a derived field 'computed.<name>' "
            "(e.g. 'computed.line_tax')."
        ),
    )
    fallback: str | None = Field(
        default=None,
        description="Secondary canonical path used when 'source' resolves to empty.",
    )
    transforms: list[str] = Field(
        default_factory=list,
        description="Safe value transforms applied after resolving source/fallback.",
    )
    width: int = Field(default=15, description="Column width hint for XLSX output")
    number_format: str | None = Field(
        default=None, description="openpyxl number format (e.g. '#,##0.00'); null = text"
    )

    model_config = {"extra": "ignore"}


class OutputSpec(BaseModel):
    """A named, versioned set of output columns a customer requires."""

    spec_id: str = Field(..., description="e.g. 'temforce.standard'")
    name: str = Field(default="", description="Human-readable label")
    columns: list[OutputColumn] = Field(..., min_length=1)

    model_config = {"extra": "ignore"}
