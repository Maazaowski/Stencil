"""Declarative extraction model schema.

An ExtractionModel is a fully self-contained rule document: every piece of
layout knowledge (region anchors, column x-bounds, row classification patterns,
grouping rules, field rules, transforms, self-checks) lives in the model JSON.
The interpreter (`stencil.models.interpreter`) is a small generic engine
with zero supplier knowledge — covering a new supplier means authoring a
different rule document, never new Python.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ModelStatus(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    RETIRED = "retired"
    FAILED_VALIDATION = "failed_validation"


# ---------------------------------------------------------------------------
# Header / total fields — label-relative locators (proven design, kept)
# ---------------------------------------------------------------------------


class HeaderFieldRule(BaseModel):
    """How to find a single labelled value on the page."""

    page: int = Field(default=1, description="Which page (1-indexed)")
    label: str = Field(..., description="Anchor text printed next to the value, e.g. 'Invoice Date'")
    value_position: str = Field(
        default="right",
        description=(
            "Where the VALUE sits relative to the label: 'right' (same line, after the label), "
            "'left' (same line, before the label), 'below' (next non-empty line), "
            "'above' (previous non-empty line)."
        ),
    )
    value_pattern: str | None = Field(
        default=None,
        description="Optional regex the value must match (first capture group used when present).",
    )
    date_format: str | None = Field(
        default=None,
        description="strptime format the value is printed in ON THE PAGE (e.g. '%d-%b-%Y').",
    )
    ignore_percent: bool = Field(
        default=False,
        description="When extracting a number, skip percentage tokens (e.g. '23.00%').",
    )
    occurrence: str = Field(
        default="first",
        description="Which label match to use when it appears more than once: 'first' or 'last'.",
    )
    sample_value: str | None = Field(default=None, description="The value as printed on the authoring invoice")
    literal: str | None = Field(
        default=None,
        description="Constant value when no reliable label exists on the page (skips label lookup).",
    )
    required: bool = Field(default=True)

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Region — where the line items live, and the column geometry
# ---------------------------------------------------------------------------


class ColumnDef(BaseModel):
    """A named vertical band of the line-item region.

    Coordinates are normalized to 0-1000 of the page width (matching
    LayoutCell.normalized_bbox), so the model is resolution independent.
    """

    name: str = Field(..., description="Model-chosen column name, e.g. 'service_id', 'amount'")
    x0: float = Field(..., ge=0, le=1000, description="Left edge (0-1000 normalized page width)")
    x1: float = Field(..., ge=0, le=1000, description="Right edge (0-1000 normalized page width)")
    header_text: str | None = Field(default=None, description="Column header text as printed, if any")

    model_config = {"extra": "ignore"}


class RegionRule(BaseModel):
    """Locates the line-item region rows across pages."""

    start_anchors: list[str] = Field(
        default_factory=list,
        description="Text markers that begin the line-item section (matched case-insensitively, substring)",
    )
    end_anchors: list[str] = Field(
        default_factory=list,
        description="Text markers that end the section",
    )
    end_scope: str = Field(
        default="document",
        description=(
            "'document': an end anchor closes the region for good (until a start anchor "
            "re-opens it). 'page': the end anchor is a per-page footer (e.g. 'carried "
            "forward') — the region resumes on the next page even without a start anchor."
        ),
    )
    repeat_per_page: bool = Field(
        default=True,
        description="When the start anchor repeats on continuation pages, re-enter the region on each page",
    )
    include_anchor_row: bool = Field(
        default=False,
        description=(
            "Keep the start-anchor row as a data row (it is re-classified normally) "
            "instead of consuming it as a pure delimiter. Needed when the same text "
            "both opens the region and marks each item (e.g. a per-block 'Charges for' "
            "header that is also the item_start)."
        ),
    )
    include_unanchored_pages: bool = Field(
        default=True,
        description=(
            "When a continuation page has no start anchor, keep collecting rows there "
            "(only with end_scope='page'; a page break never closes an open region)"
        ),
    )
    page_scope: list[int] | None = Field(default=None, description="Optional 1-indexed pages to inspect")
    columns: list[ColumnDef] = Field(default_factory=list)
    column_split_x: list[float] = Field(
        default_factory=list,
        description=(
            "Vertical gutter x's (normalized 0-1000) that split a two-column "
            "'newspaper' page into independent reading columns. Empty (default) = "
            "single column, unchanged. When set, the interpreter reads each column "
            "as its own top-to-bottom stream so blocks are not interleaved by y."
        ),
    )

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Row classification — model-owned patterns, no shared heuristics
# ---------------------------------------------------------------------------


class RowMatch(BaseModel):
    """Predicate evaluated against a single visual row."""

    row_text: str | None = Field(
        default=None,
        description="Regex tested against the full row text",
    )
    column: str | None = Field(
        default=None,
        description="Name of a region column; the predicate then applies to the cell text in that column",
    )
    pattern: str | None = Field(
        default=None,
        description="Regex tested against the named column's cell text (requires 'column')",
    )
    min_cells: int | None = Field(default=None, description="Minimum number of cells in the row")
    has_amount_in_column: str | None = Field(
        default=None,
        description="Name of a region column that must contain a parseable amount",
    )

    model_config = {"extra": "ignore"}


class RowClassifier(BaseModel):
    """Assigns a model-defined role to rows matching a predicate.

    Classifiers are evaluated in order; the first match wins. Unmatched rows
    get the role 'detail'. The reserved role 'skip' removes a row entirely.
    """

    role: str = Field(..., description="Role name, e.g. 'item_start', 'item_total', 'skip'")
    where: RowMatch

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Grouping — visual rows -> logical line items
# ---------------------------------------------------------------------------


class GroupingRule(BaseModel):
    mode: str = Field(
        default="single_row",
        description=(
            "'single_row': every row with role item_role is one line item. "
            "'span': a group starts at start_role and closes at end_role (inclusive when include_end_row). "
            "'role_transition': a new group starts at each start_role row and runs to the next one."
        ),
    )
    item_role: str | None = Field(default=None, description="single_row mode: role marking item rows")
    start_role: str | None = Field(default=None, description="span/role_transition: role that opens a group")
    end_role: str | None = Field(default=None, description="span mode: role that closes a group")
    include_end_row: bool = Field(default=True)
    emit: str = Field(
        default="one_item_per_group",
        description=(
            "'one_item_per_group': each group becomes one line item. "
            "'one_item_per_role_row': each row with emit_role inside a group becomes a line item "
            "(the group provides shared context like the parent service id)."
        ),
    )
    emit_role: str | None = Field(default=None, description="emit=one_item_per_role_row: role of the emitting rows")

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Field rules — what to extract for each line item
# ---------------------------------------------------------------------------


class FieldSource(BaseModel):
    """Where a field's raw text comes from within a group."""

    rows: str = Field(
        default="role",
        description=(
            "'role': rows in the group having row_role. "
            "'emit_row': the row that emitted this item (emit=one_item_per_role_row). "
            "'first' / 'last': first/last row of the group. "
            "'all_in_group': every row in the group (use 'join')."
        ),
    )
    row_role: str | None = Field(default=None, description="rows='role': which role to select")
    column: str | None = Field(default=None, description="Region column to read the cell from")
    pattern: str | None = Field(
        default=None,
        description="Regex with one capture group applied to the selected text",
    )
    join: str | None = Field(default=None, description="Separator when multiple rows are selected")
    occurrence: str = Field(default="first", description="'first' or 'last' matching row")

    model_config = {"extra": "ignore"}


class FieldTransform(BaseModel):
    type: str = Field(
        default="string",
        description="'string', 'currency', 'integer', 'decimal', or 'date'",
    )
    date_format: str | None = Field(default=None, description="strptime format for type='date'")
    value_map: dict[str, str] = Field(
        default_factory=dict,
        description="Optional raw-text -> canonical-value map applied after extraction (substring match)",
    )
    default: str | None = Field(default=None, description="Fallback canonical value when nothing matched")

    model_config = {"extra": "ignore"}


class ValueOperand(BaseModel):
    """One input to a value expression: extract from the page, a constant, or a
    reference to another already-computed field or document total."""

    kind: str = Field(default="extract", description="'extract' | 'const' | 'ref'")
    source: FieldSource | None = Field(default=None, description="kind='extract': where to read it from")
    const: str | None = Field(default=None, description="kind='const': a literal number")
    ref: str | None = Field(
        default=None,
        description="kind='ref': another item field (e.g. 'amount') or a document total (e.g. 'subtotal', 'tax_rate')",
    )

    model_config = {"extra": "ignore"}


class ValueExpr(BaseModel):
    """A field's value as an operation over operands — the single mechanism for
    both direct extraction and computed amount/tax.

    'extract' returns the first operand that resolves (the common one-operand
    case). 'sum' adds them (a missing operand counts as 0). 'subtract' is
    first − Σ(rest). 'product' multiplies them (any missing → no value).
    """

    op: str = Field(default="extract", description="'extract' | 'sum' | 'subtract' | 'product'")
    operands: list[ValueOperand] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class ItemFieldRule(BaseModel):
    """One canonical line-item field: where it comes from + how to normalize it."""

    name: str = Field(..., description="Canonical field name, e.g. 'service_id', 'amount'")
    value: ValueExpr | None = Field(
        default=None,
        description=(
            "Unified value expression (extract / sum / subtract / product over operands). "
            "When set it wins over `source`/`sources`/`literal`. Covers direct extraction and "
            "computed amount/tax (e.g. amount = Gross − Discount, tax = Taxes + Surcharges)."
        ),
    )
    source: FieldSource | None = Field(default=None)
    sources: list[FieldSource] = Field(
        default_factory=list,
        description=(
            "Multiple sources combined via `combine` — e.g. tax = 'Total Taxes' + "
            "'Total Surcharges and Other Fees'. Each is parsed with `transform`; a "
            "missing component counts as 0. Takes precedence over `source` when set."
        ),
    )
    combine: str = Field(default="sum", description="How to combine `sources`: 'sum'.")
    transform: FieldTransform = Field(default_factory=FieldTransform)
    literal: str | None = Field(default=None, description="Constant value (no source lookup)")
    same_as: str | None = Field(default=None, description="Copy another already-extracted field")
    required: bool = Field(
        default=False,
        description=(
            "Only honored for 'amount' (a row without an amount is not a charge). "
            "On auxiliary fields the flag is advisory: items missing them are still "
            "emitted (one-off items legitimately lack billing periods etc.)."
        ),
    )

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Self checks
# ---------------------------------------------------------------------------


class SelfCheck(BaseModel):
    check: str = Field(
        default="sum_of_items_equals",
        description="'sum_of_items_equals': sum of item amounts must equal the named total field",
    )
    field: str = Field(default="subtotal", description="Which extracted total to reconcile against")
    tolerance: float = Field(default=0.01)

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# The model document
# ---------------------------------------------------------------------------


class LineItemStrategy(BaseModel):
    """Deterministic, profile-declared line-item policy.

    When set, the interpreter builds line items by replaying the SAME layout
    grouping the AI ground truth uses (see extraction.normalization), instead of
    the AI-authored row_classifier/grouping/item_fields recipe — so grouped
    layouts (parent-total or per-charge) are reproduced exactly, with no AI
    variance. Authored deterministically when the supplier profile declares a
    grouping policy.
    """

    kind: str = Field(default="profile_groups")
    granularity: str = Field(..., description="per_total_group or per_charge_row")
    service_id_preference: str = Field(...)
    billing_reference_preference: str | None = Field(default=None)
    anchors: list[str] = Field(default_factory=list, description="Section start anchors")
    skip_keywords: list[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class ExtractionModel(BaseModel):
    """Self-contained declarative extraction rule document for one supplier layout."""

    model_config = {"extra": "ignore", "protected_namespaces": ()}

    model_id: str = Field(..., description="Unique identifier; equals the supplier profile id")
    supplier_profile_id: str = Field(..., description="Owning supplier profile (one model per layout profile)")
    version: int = Field(default=1)
    supplier: str = Field(..., description="Canonical supplier name")
    output_type: str = Field(default="standard")
    status: ModelStatus = Field(default=ModelStatus.CANDIDATE)

    layout_fingerprint: str = Field(default="", description="SHA-256 layout fingerprint of the authoring invoice")
    layout_family_key: str | None = Field(default=None)

    header_fields: dict[str, HeaderFieldRule] = Field(
        default_factory=dict,
        description="invoice_number, invoice_date, due_date, account_number, currency, ...",
    )
    region: RegionRule = Field(default_factory=RegionRule)
    row_classifiers: list[RowClassifier] = Field(default_factory=list)
    grouping: GroupingRule = Field(default_factory=GroupingRule)
    item_fields: list[ItemFieldRule] = Field(default_factory=list)
    # When set, line items come from this deterministic grouping strategy and the
    # AI row_classifier/grouping/item_fields recipe above is ignored.
    line_item_strategy: LineItemStrategy | None = Field(default=None)
    totals: dict[str, HeaderFieldRule] = Field(
        default_factory=dict,
        description="subtotal, tax, fees, total_due, tax_rate, current_charges",
    )
    self_checks: list[SelfCheck] = Field(default_factory=list)

    # Delivered-tax policy carried on the model, so a hand-built model is
    # self-contained and its preview computes EXT_TAX without needing a profile.
    # When set, these win over profile-level line_item_hints (see
    # extraction/normalization.apply_layout_profile_hints).
    tax_output_mode: str | None = Field(
        default=None, description="extract_exact | calculate | none"
    )
    tax_rate_source: str | None = Field(
        default=None,
        description=(
            "invoice_tax_rate | invoice_tax_divided_by_subtotal | "
            "consistent_line_tax_rate | auto"
        ),
    )

    # Intakes the model is AUTHORED to reproduce. Grows as same-fingerprint
    # invoices reveal new row shapes; the model must reproduce EVERY one before
    # it can be approved. Distinct from validation_intake_ids (the production /
    # credit history surfaced through the DB record).
    training_intake_ids: list[str] = Field(default_factory=list)

    # Holdout intakes the model must reproduce but is NEVER authored from. They
    # are the trust signal: reproducing an invoice the AI never saw during
    # authoring is evidence the rules generalize instead of overfitting. Kept
    # separate from training_intake_ids so authoring never sees them.
    holdout_intake_ids: list[str] = Field(default_factory=list)

    confidence: float = Field(default=0.0)
    created_by: str = Field(default="ai_authoring")
    created_from_intake: str | None = Field(default=None)
    created_at: datetime | None = Field(default=None)
    updated_by: str | None = Field(default=None)
    approved_by: str | None = Field(default=None)
    approved_at: datetime | None = Field(default=None)
    retired_by: str | None = Field(default=None)
    retired_at: datetime | None = Field(default=None)
    notes: str | None = Field(default=None)
