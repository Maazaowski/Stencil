"""Field schema — the user-defined extraction contract (mirrors output/spec.py)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class FieldScope(StrEnum):
    DOCUMENT = "document"
    ROW = "row"


class FieldType(StrEnum):
    STRING = "string"
    DATE = "date"
    NUMBER = "number"
    CURRENCY = "currency"
    INTEGER = "integer"
    ENUM = "enum"


class FieldRole(StrEnum):
    NONE = "none"
    IDENTIFIER = "identifier"
    AMOUNT = "amount"
    TAX = "tax"
    SUBTOTAL = "subtotal"
    TOTAL = "total"
    TAX_RATE = "tax_rate"
    # A printed total for a section the deliverable deliberately excludes (e.g.
    # Lumen's "Total Account Level Charges", printed outside SERVICE LEVEL
    # ACTIVITY). Subtracted from the reconciliation target so delivered rows can
    # close against the charges they actually cover.
    EXCLUDED_TOTAL = "excluded_total"


class FieldDef(BaseModel):
    name: str = Field(..., description="Field key in ExtractedDocument.fields or .rows")
    scope: FieldScope
    type: FieldType = FieldType.STRING
    role: FieldRole = FieldRole.NONE
    label_hint: str | None = Field(default=None, description="Printed label hint for AI/model")
    date_format: str | None = Field(
        default=None,
        description="Optional strptime format for the date as printed on this supplier's invoice.",
    )
    required: bool = False
    enum_values: list[str] = Field(default_factory=list)
    description: str = Field(default="")

    model_config = {"extra": "ignore"}


class FieldSchema(BaseModel):
    schema_id: str = Field(..., description="e.g. 'invoice.standard'")
    name: str = Field(default="")
    fields: list[FieldDef] = Field(..., min_length=1)

    model_config = {"extra": "ignore"}

    def document_fields(self) -> list[FieldDef]:
        return [f for f in self.fields if f.scope == FieldScope.DOCUMENT]

    def row_fields(self) -> list[FieldDef]:
        return [f for f in self.fields if f.scope == FieldScope.ROW]

    def fields_with_role(self, role: FieldRole | str) -> list[FieldDef]:
        role_val = FieldRole(role) if isinstance(role, str) else role
        return [f for f in self.fields if f.role == role_val]

    def field_by_name(self, name: str) -> FieldDef | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None
