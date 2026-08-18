"""Canonical document schema — the single data contract between all extraction paths and output generation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from stencil.fields.coercion import (
    DOCUMENT_DATE_FIELDS,
    ROW_DATE_FIELDS,
    parse_date_value,
)


class ChargeType(StrEnum):
    RECURRING = "recurring"
    ONE_TIME = "one_time"
    TAX = "tax"
    FEE = "fee"
    CREDIT = "credit"
    ADJUSTMENT = "adjustment"
    SURCHARGE = "surcharge"
    USAGE = "usage"
    UNKNOWN = "unknown"


class OutputType(StrEnum):
    STANDARD = "standard"
    WIRELESS = "wireless"
    TIME_AND_MATERIAL = "time_and_material"


class ExtractionPath(StrEnum):
    AI = "ai"
    MODEL = "model"
    MODEL_FALLBACK_AI = "model_fallback_ai"


class LineItem(BaseModel):
    line_number: int = Field(..., description="Sequential line number within the invoice")
    service_id: str | None = Field(default=None, description="Line identifier (service ID, item code, reference, etc.)")
    billing_reference: str | None = Field(
        default=None,
        description="Contract or billing reference ID (may differ from service_id)",
    )
    description: str = Field(..., description="Charge description as it appears on the invoice")
    charge_type: ChargeType = Field(default=ChargeType.UNKNOWN)
    amount: Decimal = Field(..., description="Line item amount (pre-tax)")
    tax_amount: Decimal | None = Field(
        default=None,
        description="Per-line tax/fees/surcharges as stated on the invoice, if itemized per line",
    )
    currency: str = Field(default="USD", max_length=3)
    billing_period_start: date | None = Field(default=None)
    billing_period_end: date | None = Field(default=None)
    quantity: Decimal | None = Field(default=None)
    unit_rate: Decimal | None = Field(default=None)
    plan_cost: Decimal | None = Field(default=None)
    equipment_cost: Decimal | None = Field(default=None)
    source_page: int | None = Field(default=None, description="PDF page number where this line was found")
    source_row_id: str | None = Field(
        default=None,
        description="Layout visual-row id this line was read from (e.g. 'p5.r12'). Joins a "
        "delivered row back to its printed row for deterministic field repairs.",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("currency", mode="before")
    @classmethod
    def _normalize_currency(cls, value: Any) -> str:
        from stencil.fields.currency import normalize_currency_code

        return normalize_currency_code(value, fallback="USD").code or "USD"


class InvoiceHeader(BaseModel):
    supplier_name: str = Field(..., description="Canonical supplier name")
    supplier_aliases: list[str] = Field(default_factory=list, description="Other names found on the invoice")
    invoice_number: str = Field(..., description="Invoice number as printed on the document")
    invoice_date: date = Field(..., description="Invoice issue date")
    due_date: date | None = Field(default=None)
    account_number: str | None = Field(default=None, description="Customer/account number")
    ban: str | None = Field(default=None, description="Secondary billing account number, if distinct")
    billing_period_start: date | None = Field(default=None)
    billing_period_end: date | None = Field(default=None)
    currency: str = Field(default="USD", max_length=3)
    po_number: str | None = Field(default=None)
    payment_terms: str | None = Field(default=None)
    source_page: int | None = Field(default=None)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("currency", mode="before")
    @classmethod
    def _normalize_currency(cls, value: Any) -> str:
        from stencil.fields.currency import normalize_currency_code

        return normalize_currency_code(value, fallback="USD").code or "USD"


class ReconciliationResult(BaseModel):
    line_items_sum: Decimal = Field(..., description="Sum of all line item amounts")
    stated_subtotal: Decimal | None = Field(default=None, description="Subtotal as printed on the invoice")
    stated_tax: Decimal | None = Field(default=None)
    stated_fees: Decimal | None = Field(default=None)
    stated_total: Decimal | None = Field(default=None, description="Total due as printed on the invoice")
    reconcile_target: Decimal | None = Field(
        default=None,
        description="The figure computed_total was reconciled against (current_charges if present, else total_due)",
    )
    computed_total: Decimal = Field(..., description="Our computed total from line items + tax + fees")
    variance: Decimal = Field(..., description="Difference between stated total and computed total")
    variance_pct: float = Field(..., description="Variance as percentage of stated total")
    is_reconciled: bool = Field(..., description="Whether variance is within acceptable threshold")
    verification_status: Literal["reconciled", "mismatch", "unverifiable"] = Field(
        default="unverifiable",
        description="Whether totals matched, mismatched, or could not be verified.",
    )
    warnings: list[str] = Field(default_factory=list)


class ExtractionMetadata(BaseModel):
    extraction_path: ExtractionPath = Field(..., description="Which path was used: ai, model, or model_fallback_ai")
    model_id: str | None = Field(default=None, description="Extraction model ID if model path was used")
    ai_model_name: str | None = Field(default=None, description="OpenAI model name if AI path was used")
    supplier_profile_id: str | None = Field(default=None, description="Supplier profile used for extraction")
    layout_fingerprint: str | None = Field(default=None, description="PDF layout fingerprint hash")
    total_pages: int = Field(default=0)
    tokens_input: int = Field(default=0)
    tokens_output: int = Field(default=0)
    estimated_cost_usd: Decimal = Field(default=Decimal("0"))
    extraction_duration_ms: int = Field(default=0)
    overall_confidence: float = Field(default=1.0, ge=0.0, le=1.0)


_HEADER_KEYS = {
    "supplier_name", "supplier_aliases", "invoice_number", "invoice_date",
    "due_date", "account_number", "ban", "billing_period_start", "billing_period_end",
    "currency", "po_number", "payment_terms", "source_page", "confidence",
}

_TOTAL_KEYS = {"subtotal", "tax", "fees", "current_charges", "total_due", "tax_rate", "output_type"}
_DECIMAL_TOTAL_KEYS = _TOTAL_KEYS - {"output_type"}
SYNTHETIC_INVOICE_DATE_WARNING = (
    "invoice_date was not extracted; using due_date/billing_period_start/today fallback"
)


def _coerce_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _coerce_date_fields(data: dict[str, Any], keys: frozenset[str]) -> dict[str, Any]:
    """Normalize date keys in a flat dict; drop unavailable or unparseable values."""
    out = dict(data)
    for key in keys:
        if key not in out:
            continue
        if out[key] is None:
            out.pop(key, None)
            continue
        coerced = parse_date_value(out[key])
        if coerced is not None:
            out[key] = coerced
        elif not isinstance(out[key], date):
            out.pop(key, None)
    return out


def _normalize_v1_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Convert nested v1 invoice dict or constructor kwargs to v2 fields/rows."""
    if "fields" in data and "rows" in data:
        return data

    fields: dict[str, Any] = {}
    header = data.pop("header", {}) or {}
    if hasattr(header, "model_dump"):
        header = header.model_dump(mode="python")
    for key in _HEADER_KEYS:
        if key in header:
            fields[key] = header[key]
    fields = _coerce_date_fields(fields, DOCUMENT_DATE_FIELDS)
    for key in _TOTAL_KEYS:
        if key in data:
            raw = data.pop(key)
            if key in _DECIMAL_TOTAL_KEYS:
                coerced = _coerce_decimal(raw)
                if coerced is not None:
                    fields[key] = coerced
            else:
                fields[key] = raw
    if "output_type" in data and "output_type" not in fields:
        ot = data.pop("output_type")
        fields["output_type"] = ot.value if hasattr(ot, "value") else ot

    rows = data.pop("line_items", []) or []
    if rows and hasattr(rows[0], "model_dump"):
        rows = [r.model_dump(mode="python") for r in rows]
    rows = [_coerce_date_fields(row, ROW_DATE_FIELDS) for row in rows]

    out = dict(data)
    out["fields"] = fields
    out["rows"] = rows
    out.setdefault("schema_id", "invoice.standard")
    out.setdefault("schema_version", "2.0")
    return out


class ExtractedDocument(BaseModel):
    """Schema-driven extraction record — generic document + row fields."""

    schema_version: str = Field(default="2.0")
    intake_id: str = Field(..., description="Unique intake identifier (UUID)")
    schema_id: str = Field(default="invoice.standard")
    fields: dict[str, Any] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    reconciliation: ReconciliationResult | None = Field(default=None)
    metadata: ExtractionMetadata
    warnings: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_shape(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if ("header" in data or "line_items" in data) and "fields" not in data:
            return _normalize_v1_payload(dict(data))
        if "header" in data and "line_items" in data and "intake_id" in data:
            return _normalize_v1_payload(dict(data))
        return data

    @model_validator(mode="after")
    def _coerce_stored_dates(self) -> Self:
        self.fields = _coerce_date_fields(self.fields, DOCUMENT_DATE_FIELDS)
        self.rows = [_coerce_date_fields(row, ROW_DATE_FIELDS) for row in self.rows]
        if "invoice_date" not in self.fields and SYNTHETIC_INVOICE_DATE_WARNING not in self.warnings:
            self.warnings.append(SYNTHETIC_INVOICE_DATE_WARNING)
        return self

    def get(self, name: str) -> Any:
        return self.fields.get(name)

    def row_field(self, row: dict[str, Any], name: str) -> Any:
        return row.get(name)

    @property
    def output_type(self) -> OutputType:
        raw = self.fields.get("output_type", OutputType.STANDARD)
        if isinstance(raw, OutputType):
            return raw
        try:
            return OutputType(str(raw))
        except ValueError:
            return OutputType.STANDARD

    @output_type.setter
    def output_type(self, value: OutputType | str) -> None:
        self.fields["output_type"] = value.value if isinstance(value, OutputType) else value

    @property
    def header(self) -> InvoiceHeader:
        data = {k: self.fields[k] for k in self.fields if k in _HEADER_KEYS}
        data = _coerce_date_fields(data, DOCUMENT_DATE_FIELDS)
        if "supplier_name" not in data:
            data.setdefault("supplier_name", "")
        if "invoice_number" not in data:
            data.setdefault("invoice_number", "")
        if "invoice_date" not in data:
            data["invoice_date"] = data.get("due_date") or data.get("billing_period_start") or date.today()
        return InvoiceHeader.model_validate(data)

    @property
    def line_items(self) -> list[LineItem]:
        items: list[LineItem] = []
        for i, row in enumerate(self.rows):
            data = _coerce_date_fields(dict(row), ROW_DATE_FIELDS)
            data.setdefault("line_number", i + 1)
            data.setdefault("description", "")
            if "amount" not in data:
                data["amount"] = Decimal("0")
            items.append(LineItem.model_validate(data))
        return items

    @line_items.setter
    def line_items(self, items: list[LineItem] | list[dict[str, Any]]) -> None:
        rows: list[dict[str, Any]] = []
        for i, item in enumerate(items):
            if isinstance(item, LineItem):
                row = item.model_dump(mode="python")
            else:
                row = dict(item)
            row.setdefault("line_number", i + 1)
            rows.append(row)
        self.rows = rows

    @property
    def subtotal(self) -> Decimal | None:
        return _coerce_decimal(self.fields.get("subtotal"))

    @subtotal.setter
    def subtotal(self, value: Decimal | None) -> None:
        self._set_document_total("subtotal", value)

    @property
    def tax(self) -> Decimal | None:
        return _coerce_decimal(self.fields.get("tax"))

    @tax.setter
    def tax(self, value: Decimal | None) -> None:
        self._set_document_total("tax", value)

    @property
    def fees(self) -> Decimal | None:
        return _coerce_decimal(self.fields.get("fees"))

    @fees.setter
    def fees(self, value: Decimal | None) -> None:
        self._set_document_total("fees", value)

    @property
    def current_charges(self) -> Decimal | None:
        return _coerce_decimal(self.fields.get("current_charges"))

    @current_charges.setter
    def current_charges(self, value: Decimal | None) -> None:
        self._set_document_total("current_charges", value)

    @property
    def total_due(self) -> Decimal | None:
        return _coerce_decimal(self.fields.get("total_due"))

    @total_due.setter
    def total_due(self, value: Decimal | None) -> None:
        self._set_document_total("total_due", value)

    @property
    def tax_rate(self) -> Decimal | None:
        return _coerce_decimal(self.fields.get("tax_rate"))

    @tax_rate.setter
    def tax_rate(self, value: Decimal | None) -> None:
        self._set_document_total("tax_rate", value)

    def _set_document_total(self, name: str, value: Any) -> None:
        if value is None:
            self.fields.pop(name, None)
            return
        coerced = _coerce_decimal(value)
        if coerced is not None:
            self.fields[name] = coerced
        else:
            self.fields.pop(name, None)

    def fields_with_role(self, role: str) -> list[str]:
        from stencil.fields.loader import get_field_schema
        from stencil.fields.schema import FieldRole

        schema = get_field_schema(self.schema_id)
        role_enum = FieldRole(role)
        return [f.name for f in schema.fields_with_role(role_enum)]


class CanonicalInvoice(ExtractedDocument):
    """Backward-compatible alias for ExtractedDocument."""

    pass
