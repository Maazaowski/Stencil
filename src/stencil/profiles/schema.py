"""Supplier profile schema — the small, domain-agnostic config that tells
Stencil how to recognise a source's documents and (optionally) how to read
tricky layouts deterministically.

Design intent: setup is dead simple. Identity + which OutputSpec to deliver is
all most sources need — the AI extractor reads the document without hand-tuning.
The few genuinely load-bearing hints (used only by the layout fingerprint and
the deterministic grouped-line-item model) live in an optional ``advanced``
block that defaults empty.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from stencil.fields.schema import FieldDef


class SupplierIdentity(BaseModel):
    canonical_name: str = Field(..., description="Primary source name used across the system")
    aliases: list[str] = Field(default_factory=list, description="Other names this source uses on documents")


class ClassificationSignals(BaseModel):
    """How this source's documents are categorised. ``output_type`` is a generic
    layout/category label (not domain-specific); it salts the layout fingerprint
    and is informational. The delivered columns come from ``output_spec_id``."""

    output_type: str = Field(default="standard")


class DeliveryAccount(BaseModel):
    """One billing account served by this profile: a folder pair the account's
    PDFs are dropped into and delivered to. One profile (one layout) can serve
    many accounts without being cloned."""

    label: str = Field(..., description="Account label/number (used to route output).")
    inbound_path: str = Field(..., description="Directory watched for this account's inbound PDFs.")
    output_path: str = Field(..., description="Directory this account's delivered output is copied to.")


class DeliveryConfig(BaseModel):
    """Where active documents are picked up and delivered (file-drop integration).

    A profile may serve several billing accounts via ``accounts`` (each its own
    folder pair). The single ``inbound_path``/``output_path`` are the one-account
    shorthand kept for backward compatibility — use ``effective_accounts`` to read
    a normalized list regardless of which form a profile uses."""

    inbound_path: str | None = Field(
        default=None, description="Directory watched for inbound PDFs when active (single-account shorthand).")
    output_path: str | None = Field(
        default=None, description="Directory where delivered output is copied (single-account shorthand).")
    accounts: list[DeliveryAccount] = Field(
        default_factory=list, description="Per-account folder pairs; one profile, many accounts.")


class DocumentStructure(BaseModel):
    detail_start_marker: str | None = Field(
        default=None, description="Text marking the START of the line-item section")
    detail_end_marker: str | None = Field(
        default=None,
        description="Text marking the END of the line-item section. Line items are read only "
        "between the start and end markers; everything outside is ignored.",
    )


class LineItemHints(BaseModel):
    """Load-bearing hints for layout fingerprinting and the deterministic
    grouped-line-item model. All optional — AI authoring handles layouts that
    leave these empty."""

    subtotal_keywords: list[str] = Field(default_factory=lambda: ["Subtotal", "Sub Total", "Sub-Total"])
    tax_keywords: list[str] = Field(default_factory=lambda: ["Tax", "Taxes", "VAT"])
    detail_table_anchors: list[str] = Field(
        default_factory=list,
        description="Line-item table/section header texts (fingerprint + region anchors).",
    )
    line_item_granularity: str | None = Field(
        default=None,
        description="Grouped-line-item policy: per_charge_row, per_service_group, per_total_group.",
    )
    service_id_preference: str | None = Field(
        default=None,
        description=(
            "first_identifier, parent_identifier, total_row_identifier, leftmost_identifier, "
            "child_identifier. child_identifier takes the innermost printed identifier in force "
            "at each charge row (parent when a service has no children) and never a Total row's."
        ),
    )
    billing_reference_preference: str | None = Field(
        default=None,
        description="same_as_service_id, parent_identifier, separate_column, none.",
    )
    service_id_column_label: str | None = Field(
        default=None,
        description="Line-item table column header for service_id (e.g. 'Service Number').",
    )
    billing_reference_column_label: str | None = Field(
        default=None,
        description="Line-item table column header for billing_reference (e.g. 'Contract Number').",
    )
    amount_column_label: str | None = Field(
        default=None,
        description="Line-item table column/header label for the delivered amount.",
    )
    tax_amount_column_label: str | None = Field(
        default=None,
        description="Line-item table column/header label for per-line tax amount.",
    )
    amount_source: str | None = Field(
        default=None,
        description=(
            "Executable amount policy: group_total, first_amount, label_amount, "
            "label_amount_minus_tax, table_charges_column."
        ),
    )
    tax_source: str | None = Field(
        default=None,
        description="Executable tax policy: none, label_amount, group_total_minus_amount, table_tax_column.",
    )
    tax_output_mode: str | None = Field(
        default=None,
        description=(
            "Delivered EXT_TAX policy: auto (default), extract_exact, calculate, none. "
            "auto uses printed per-line tax when present, else allocates an invoice-level "
            "tax total across lines, else a resolved rate, else blank."
        ),
    )
    tax_rate_source: str | None = Field(
        default=None,
        description=(
            "Tax-rate source when tax_output_mode=calculate: invoice_tax_rate, "
            "invoice_tax_divided_by_subtotal, consistent_line_tax_rate, auto."
        ),
    )
    service_id_value_pattern: str | None = Field(
        default=None,
        description="Regex with one capture group used to normalize/extract service_id from a group.",
    )
    billing_reference_value_pattern: str | None = Field(
        default=None,
        description="Regex with one capture group used to normalize/extract billing_reference from a group.",
    )
    skip_row_keywords: list[str] = Field(default_factory=list)
    table_column_labels: list[str] = Field(
        default_factory=list,
        description="Line-item table column headers (extraction hints + fingerprint).",
    )


class CurrencyRules(BaseModel):
    """Profile-scoped extraction currency normalization rules."""

    default_code: str | None = Field(
        default=None,
        description="ISO currency code to use when the invoice omits or prints an unrecognized currency.",
    )
    allowed_codes: list[str] = Field(
        default_factory=list,
        description="ISO currency codes this profile is allowed to emit.",
    )
    aliases: dict[str, str] = Field(
        default_factory=dict,
        description="Printed currency words/symbols mapped to ISO codes, e.g. Rupees -> INR.",
    )


class ValueExpression(BaseModel):
    """Restricted, serializable extraction expression.

    Expressions are interpreted by Stencil; they are never evaluated as
    Python or spreadsheet code.  Optional attributes keep the JSON compact while
    supporting document labels, row columns/text, existing fields and literals.
    """

    op: Literal[
        "document_label", "row_column", "row_text", "field", "literal",
        "copy", "regex_extract", "sum", "subtract", "multiply", "abs",
        "coalesce", "month_start", "month_end",
    ]
    label: str | None = None
    field: str | None = None
    value: Any = None
    pattern: str | None = None
    group: int = 1
    args: list["ValueExpression"] = Field(default_factory=list)


class RegionRule(BaseModel):
    name: str = "line_items"
    role: Literal["line_items", "scalar_support"] = "line_items"
    start_markers: list[str] = Field(default_factory=list)
    end_markers: list[str] = Field(default_factory=list)
    occurrence: Literal["first", "all"] = "first"
    include_start: bool = True
    include_end: bool = False
    max_pages: int | None = Field(default=None, ge=1)


class RowSelectorRule(BaseModel):
    scope: Literal["row", "group_footer", "service_block"] = "row"
    include_pattern: str | None = None
    exclude_patterns: list[str] = Field(default_factory=list)
    identifier_pattern: str | None = None


class RowContextRule(BaseModel):
    """Values printed once and inherited by subsequent source rows."""

    anchor_pattern: str
    field_groups: dict[str, int] = Field(default_factory=dict)
    reset_patterns: list[str] = Field(default_factory=list)


class ReconciliationRule(BaseModel):
    name: str
    scope: Literal["row", "document"] = "row"
    left: ValueExpression
    right: ValueExpression
    tolerance: float = Field(default=0.01, ge=0)
    required: bool = True


class ExtractionPlan(BaseModel):
    """Versioned, domain-agnostic plan used by authoring and production."""

    version: str = "1.0"
    document_family: Literal["standard", "wireless", "time_and_material"] = "standard"
    regions: list[RegionRule] = Field(default_factory=list)
    row_selector: RowSelectorRule = Field(default_factory=RowSelectorRule)
    row_context_rules: list[RowContextRule] = Field(default_factory=list)
    document_field_rules: dict[str, ValueExpression] = Field(default_factory=dict)
    row_field_rules: dict[str, ValueExpression] = Field(default_factory=dict)
    reconciliation_rules: list[ReconciliationRule] = Field(default_factory=list)


class AuthoringEvidence(BaseModel):
    evidence_level: Literal["paired_blueprint", "historical_blueprint", "invoice_only"]
    status: Literal["verified", "review_required", "failed"]
    category_confidence: float = Field(default=0, ge=0, le=1)
    metrics: dict[str, Any] = Field(default_factory=dict)
    unresolved_risks: list[str] = Field(default_factory=list)
    hard_blockers: list[str] = Field(default_factory=list)
    review_warnings: list[str] = Field(default_factory=list)
    engine_version: str = "1.0"


class AdvancedHints(BaseModel):
    """Optional deterministic-help block. Defaults empty — only set when a layout
    needs fingerprint stability or a declared grouped-line-item policy."""

    document_structure: DocumentStructure = Field(default_factory=DocumentStructure)
    line_item_hints: LineItemHints = Field(default_factory=LineItemHints)
    currency: CurrencyRules = Field(default_factory=CurrencyRules)
    extraction_plan: ExtractionPlan | None = None
    document_field_defaults: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Profile-owned constant document values applied after extraction. Use only for "
            "downstream account labels or other values that are genuinely invariant and are "
            "not printed invoice fields."
        ),
    )
    include_zero_amount_line_items: bool = Field(
        default=False,
        description="Keep zero-amount line items in the output (some sources list every item).",
    )
    require_line_item_identifier: bool = Field(
        default=False,
        description="Drop delivered line items that have neither a service_id nor a "
        "billing_reference (rows with no identifier are treated as noise for this source).",
    )

    model_config = {"extra": "ignore"}


class TrainingConfig(BaseModel):
    """Gates for the explicit training phase of a profile's extraction model."""

    min_validation_successes: int = Field(
        default=3,
        description="Training documents the model must reproduce exactly before approval is enabled",
    )
    require_reconciliation: bool = Field(default=True)


class OutputMappingOverride(BaseModel):
    """Profile-specific source mapping for one delivered output column.

    Output headers/order/formatting remain owned by the OutputSpec.  A profile
    may only replace the value source and its optional fallback.
    """

    output_header: str = Field(..., description="Unique delivered column header to override.")
    source: str = Field(..., description="Profile-specific field/row/computed source path.")
    fallback: str | None = Field(default=None, description="Optional fallback source path.")
    transforms: list[Literal["digits_only", "trim", "uppercase", "lowercase"]] = Field(
        default_factory=list,
        description="Safe string transforms applied after source/fallback resolution.",
    )

    @field_validator("transforms")
    @classmethod
    def _unique_transforms(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("output mapping transforms must not contain duplicates")
        if "uppercase" in value and "lowercase" in value:
            raise ValueError("output mapping cannot combine uppercase and lowercase")
        return value


class LayoutFingerprintRules(BaseModel):
    """Fingerprint-only tuning rules.

    Header labels come from merged field schema ``label_hint`` values on the profile.
    Tax wording from ``advanced.line_item_hints.tax_keywords``; table columns from
    ``advanced.line_item_hints.table_column_labels`` / ``detail_table_anchors``.
    This block holds only the extra normalization and ignore rules.
    """

    summary_anchors: list[str] = Field(
        default_factory=list,
        description="Summary/footer labels not covered by header_mapping",
    )
    currency_codes: list[str] = Field(
        default_factory=list,
        description="ISO currency codes stripped before hashing",
    )
    ignore_label_patterns: list[str] = Field(
        default_factory=list,
        description="Regex patterns for account-specific labels to drop (regional totals, etc.)",
    )
    optional_column_patterns: list[str] = Field(
        default_factory=list,
        description="Regex patterns for optional columns that must not change the fingerprint",
    )
    exclude_span_patterns: list[str] = Field(
        default_factory=list,
        description="Regex patterns for payment/bank/footer spans to drop",
    )

    model_config = {"extra": "ignore"}


PROFILE_STATUSES = ("draft", "training", "active", "retired")


class SupplierProfile(BaseModel):
    """Versioned profile for ONE source layout variant.

    Lifecycle: draft -> training -> active -> retired. The same source with a
    different layout gets a separate profile with its own model and fingerprint.

    The few internal consumers that read ``line_item_hints`` / ``document_structure`` /
    ``include_zero_amount_line_items`` / ``inbound_path`` / ``output_path`` are served
    by read-only convenience properties that proxy into ``advanced`` / ``delivery`` —
    the persisted JSON stays slim.
    """

    profile_id: str = Field(..., description="e.g. 'gtt.standard.v1'")
    version: int = Field(default=1)
    status: str = Field(default="draft", description="draft, training, active, retired")
    layout_description: str | None = Field(
        default=None, description="Human label for this layout variant")

    identity: SupplierIdentity
    classification: ClassificationSignals = Field(default_factory=ClassificationSignals)
    output_spec_id: str = Field(
        default="temforce.standard", description="Which OutputSpec this profile delivers.")
    field_schema_id: str = Field(
        default="invoice.standard", description="Which FieldSchema defines extraction fields.")
    field_overrides: list[FieldDef] = Field(
        default_factory=list,
        description="Profile-specific field definition overrides (label_hint, add/edit by name).",
    )
    output_mapping_overrides: list[OutputMappingOverride] = Field(
        default_factory=list,
        description="Profile-specific source/fallback mappings keyed by delivered output header.",
    )
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
    notes: str | None = Field(default=None, description="Free-text guidance passed to the AI as context")
    advanced: AdvancedHints = Field(default_factory=AdvancedHints)
    layout_fingerprint: LayoutFingerprintRules | None = Field(default=None)
    training_config: TrainingConfig = Field(default_factory=TrainingConfig)
    authoring_evidence: AuthoringEvidence | None = None

    owner: str | None = Field(default=None)
    created_date: str | None = Field(default=None)
    last_updated_date: str | None = Field(default=None)
    audit: dict | None = Field(default=None)

    model_config = {"extra": "ignore"}

    # ── Convenience proxies (read-only; not persisted) ──────────────────────
    @property
    def document_structure(self) -> DocumentStructure:
        return self.advanced.document_structure

    @property
    def line_item_hints(self) -> LineItemHints:
        return self.advanced.line_item_hints

    @property
    def include_zero_amount_line_items(self) -> bool:
        return self.advanced.include_zero_amount_line_items

    @property
    def require_line_item_identifier(self) -> bool:
        return self.advanced.require_line_item_identifier

    @property
    def inbound_path(self) -> str | None:
        return self.delivery.inbound_path

    @property
    def output_path(self) -> str | None:
        return self.delivery.output_path

    @property
    def effective_accounts(self) -> list[DeliveryAccount]:
        """Normalized account folder pairs: ``delivery.accounts`` when set, else
        the single legacy ``inbound_path``/``output_path`` as one account."""
        if self.delivery.accounts:
            return list(self.delivery.accounts)
        if self.delivery.inbound_path and self.delivery.output_path:
            return [DeliveryAccount(
                label=self.identity.canonical_name or self.profile_id,
                inbound_path=self.delivery.inbound_path,
                output_path=self.delivery.output_path,
            )]
        return []

    def output_path_for_account(self, account_label: str | None) -> str | None:
        """Resolve the output folder for a given account label (fallback to the
        legacy single output_path)."""
        if account_label:
            for account in self.delivery.accounts:
                if account.label == account_label:
                    return account.output_path
        return self.delivery.output_path
