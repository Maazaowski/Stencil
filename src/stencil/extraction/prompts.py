"""Prompt templates for OpenAI classification and extraction calls."""

from stencil.extraction.instructions import compile_instructions

CLASSIFICATION_SYSTEM_PROMPT = """\
You are a document classifier. Given the first page(s) of a PDF, identify:
1. The source/supplier name (canonical, exactly as printed on the document)
2. A short document category label (e.g. "standard"); use "standard" if unsure
3. Your confidence score (0.0 to 1.0)

Respond with valid JSON matching the schema provided."""


CLASSIFICATION_USER_PROMPT = """\
Classify this PDF. Identify the source/supplier and a short category label.

Known sources: {known_suppliers}

If the source is not in the known list, still identify it by name from the document."""


EXTRACTION_SYSTEM_PROMPT = """\
You are a precise data extractor for invoices, bills, and statements. Extract ALL data from the
provided PDF into the exact JSON schema specified.

CRITICAL RULES:
- Before extracting rows, build an internal extraction plan. Use it to decide the
  supplier/document type, requested output layout, header fields, invoice totals,
  line-item section start/end, row granularity, service ID source,
  billing reference source, amount source, tax source, page-break carry-forward rules,
  rows/sections to skip, reconciliation targets, and extraction risks. Do not
  include the plan as prose in the final answer; return only the requested JSON.
- You MUST extract EVERY line item from EVERY page. Do NOT stop at the first page of charges.
- Documents often have many line items spanning multiple pages; extract them ALL.
- Each individual line item with its own charge is a separate line item.
- Each surcharge, fee, credit, or adjustment that is its OWN charge is a separate line item.
  A row that is ONLY a tax amount for the service/charges above it is NOT a separate line item —
  fold its amount into that service line item's tax_amount (see tax_amount below).
- The extracted coordinate-aware rows/cells contain exact numbers from the PDF. Use them for amounts,
  dates, account numbers, and line identifiers. Use images only for pages with weak/no text.
- Extract line items from reconstructed visual rows/tables, not from flat reading-order text.
- Extract by document structure, not isolated page-by-page guesses. Preserve parent-child
  relationships across page breaks: if a heading, service label, account, circuit, or charge group
  starts on one page and rows continue on the next, carry that context forward until the document
  structure clearly opens a new group or closes the section.
- Prefer invoice-visible evidence over inferred labels. Produce structured invoice facts only;
  downstream code handles workbook, preview, profile, and reconciliation output.
- Amounts must be numeric (no currency symbols in the amount field).
- Currency fields must be ISO 4217 three-letter codes (USD, EUR, GBP, INR), never symbols
  or words such as "$", "Rupees", "Dollars", or "Pounds".
- Dates must be in YYYY-MM-DD format.
- charge_type must be one of: recurring, one_time, tax, fee, credit, adjustment, surcharge, usage, unknown.
- If a field is not found on the document, use null.
- billing_reference: If the document shows BOTH a line identifier AND a separate contract/billing
  reference or order number per line item, put the identifier in service_id and the separate
  reference in billing_reference. If the source hints say billing_reference mirrors service_id, use
  the same value in both fields.
- Supplier profile field labels and identifier-column hints override the generic wording above. If a
  profile explicitly says billing_reference comes from a column/field label such as "NAME", extract
  that value into billing_reference even when it is not a contract number.
- tax_rate: If the invoice states a tax rate (e.g. "23% VAT", "19% MwSt"), extract it as a decimal
  (0.23, 0.19). If no explicit rate is stated, set tax_rate to null.
- current_charges is this period's charges before prior-balance/credit adjustment. total_due is the
  payable amount. Reconcile line items to current_charges when present, otherwise total_due.
- tax_amount: Record the tax that belongs to THIS line item. When charges are grouped by
  service / account / circuit and a group shows a sub-line that is ONLY a tax amount (labeled e.g.
  'Tax', 'VAT', 'Taxes'), set that amount as the group's main service line item's tax_amount — do
  NOT emit the tax sub-line as its own line item, and do NOT leave tax_amount null expecting it to
  be computed. If a group lists several tax sub-lines, sum them into one tax_amount. Only set
  tax_amount null when the document prints no per-line or per-group tax (tax is purely an
  invoice-level total).
- EXT_TAX delivery is profile-controlled. Extract row tax_amount only when the document prints
  tax for that delivered line/group; do not assume blank row taxes will be calculated later.
- reconcile extracted line amounts against current charges/subtotal when present,
  and reconcile extracted tax/fee/surcharge values against invoice tax/fee totals.
  If invoice evidence conflicts with reusable hints, preserve invoice-backed
  values and surface the ambiguity through low confidence, warnings, or exceptions
  in the schema.
- Include confidence scores, sequential line numbers, and source_page for each item."""


def build_extraction_user_prompt(
    supplier_profile: dict | None = None,
    supplier_name: str | None = None,
    output_type: str = "standard",
    field_schema=None,
) -> str:
    compiled = compile_instructions(
        supplier_profile=supplier_profile,
        field_schema=field_schema,
        output_type=output_type,
        supplier_name=supplier_name,
    )
    return "\n".join([
        "Extract all invoice data from this PDF.",
        f"Supplier: {supplier_name or 'Unknown'}",
        f"Output type: {output_type}",
        compiled.render("extraction"),
    ])


MODEL_AUTHORING_SYSTEM_PROMPT = """\
You are an expert at writing REUSABLE, DETERMINISTIC, declarative extraction rule documents for
invoices. A generic interpreter (no AI, no supplier knowledge) will replay your rule document on
future invoices of the same layout, so EVERY piece of layout knowledge must live in the rules.

You are given (1) coordinate-aware visual rows/cells from one invoice (each cell has a normalized
x range 0-1000 of the page width), (2) the exact ground-truth values already extracted from it,
and (3) evidence pinning each ground-truth value to its page/row/cell.

Write the rule document:

1. header_fields — label-relative locators per header field (label text, value_position,
   optional date_format as printed ON THE PAGE, optional value_pattern with one capture group,
   occurrence, page).

2. region — start_anchors / end_anchors (text printed on the page that opens/closes the
   line-item section) and columns: named vertical bands with normalized x0/x1 (0-1000) — copy
   them from the x spans shown on the cells (already normalized to 0-1000 of page width),
   widened slightly. Choose stable bands that will hold when row counts change.
   end_scope: 'document' when the end anchor truly ends the section (default); 'page' ONLY when
   the end anchor is a per-page footer (e.g. 'carried forward') and items resume on the next page.

3. row_classifiers — ordered patterns that classify each region row by ROLE. You define the
   roles. Examples: an 'item_start' row is one whose first column matches an identifier pattern;
   an 'item_total' row starts with 'Total'; noise rows get role 'skip'. Patterns must describe
   the SHAPE of rows (regex on row text or on a named column's cell), never literal IDs or
   amounts from this invoice. Unmatched rows automatically get role 'detail'.

4. grouping — how classified rows become logical line items:
   - 'single_row' + item_role: each matching row is one item.
   - 'span' + start_role/end_role: a group runs from a start row to a closing row.
   - 'role_transition' + start_role: a new group begins at each start row.
   emit='one_item_per_group' produces one item per group; emit='one_item_per_role_row' +
   emit_role produces one item per matching row inside the group (shared context from the group).

5. item_fields — one rule per canonical field (service_id, billing_reference, description,
   charge_type, amount, tax_amount, currency, billing_period_start, billing_period_end, quantity,
   unit_rate). Each rule selects rows (by role / first / last / emit_row / all_in_group), reads a
   column band or applies a regex with ONE capture group, then transforms (currency, date with
   strptime format, integer, value_map for charge_type). Use same_as to copy another field,
   literal for constants. Only 'amount' may be marked required — auxiliary fields (billing
   periods, quantity, unit_rate, ...) can be absent on one-off items even when every item on
   THIS invoice has them; items missing them must still be emitted.

6. totals — label-relative locators for subtotal, tax, fees, total_due, tax_rate, current_charges
   (only those actually printed).

7. self_checks — add {"check": "sum_of_items_equals", "field": ...} when the sum of item amounts
   should reconcile against a stated total.

CRITICAL RULES:
- Describe repeating patterns, never fixed row counts, literal IDs, or literal amounts.
- All regex must be valid Python regex; use exactly one capture group when extracting a value.
- Choose column x-ranges from the visible cell coordinates, widened slightly for tolerance.
- When ground-truth line items have no per-line tax_amount, line amounts are net (pre-tax) and
  invoice-level VAT/tax belongs in totals/tax_rate, NOT duplicated in item rows.
- When line amounts are net, sum_of_items_equals must target subtotal or current_charges — NOT
  total_due (total_due includes tax).
- When ground-truth line items include per-line tax_amount, extract those amounts per item.
- Always extract invoice tax_rate when the invoice states a rate, even when per-line tax is absent.
- If ground truth header.currency is set but no dedicated 'Currency' label appears in the layout text,
  use a literal value for currency — do NOT anchor currency to unrelated labels like 'Bank Name'.
- Multi-row service blocks must be grouped with span/role_transition grouping. Use the supplier
  hints and evidence to decide which identifiers are service_id vs billing_reference; do not assume
  a universal ID format.
- Group-total layouts: when the ground truth has one line item per logical service/group but the
  page shows many detail rows followed by a total/subtotal row, do not emit one item per detail row.
  Match the ground-truth item COUNT exactly by emitting at the same granularity shown in the
  examples and evidence.
- If your first attempt produces too MANY rows, you are almost certainly emitting one item per detail
  row instead of one per group — switch to group emission. If you produce ZERO rows, your start_anchors
  or row_classifier patterns don't match the page; widen them using the anchors/markers in the hints.
- Supplier hints are advisory context; the executable rules must stand on their own.
- The rule document must reproduce the ground-truth values EXACTLY on this invoice."""


def build_authoring_user_prompt(
    *,
    page_texts: list[str],
    target: dict,
    layout_evidence: dict | None = None,
    supplier_profile: dict | None = None,
    field_schema=None,
    line_amounts_are_net: bool = True,
    feedback: str | None = None,
    additional_examples: list[dict] | None = None,
    focus: str | None = None,
) -> str:
    import json as _json

    tax_guidance = (
        "Line amounts on this invoice are NET (pre-tax); extract invoice tax_rate and put VAT in totals."
        if line_amounts_are_net
        else "Line items include per-line tax_amount; extract those amounts and do not duplicate VAT in totals."
    )
    multi = bool(additional_examples)
    parts = [
        "Write a reusable declarative extraction rule document for this supplier layout.",
    ]
    if focus:
        # Section-scoped authoring: this call only produces part of the rule
        # document (the response schema enforces it).
        parts.append(focus)
    parts += [
        f"\nTax handling for this invoice: {tax_guidance}",
        "EXT_TAX delivery is profile-controlled; do not assume blank row tax is automatically calculated.",
    ]
    if multi:
        parts.append(
            f"\nYou are given {len(additional_examples) + 1} invoices of the SAME layout. Your SINGLE "
            "rule document must reproduce the ground truth of EVERY one. The invoices differ in line "
            "count and which optional fields/row shapes appear (e.g. one-off items without a billing "
            "period). Write rules that GENERALIZE across all of them — never rules that only fit the "
            "first invoice's row count or fields."
        )

    if field_schema is not None or supplier_profile:
        compiled = compile_instructions(
            supplier_profile=supplier_profile,
            field_schema=field_schema,
            output_type=(supplier_profile or {}).get("classification", {}).get("output_type", "standard"),
            supplier_name=(supplier_profile or {}).get("identity", {}).get("canonical_name"),
        )
        parts.append(compiled.render("authoring"))

    primary_header = (
        "\n--- PRIMARY INVOICE GROUND TRUTH (richest example; the layout below is this invoice) ---"
        if multi
        else "\n--- GROUND TRUTH VALUES (the rules must reproduce these exactly on this invoice) ---"
    )
    parts.append(primary_header)
    parts.append(_json.dumps(target, indent=2, default=str))

    for i, example in enumerate(additional_examples or [], start=2):
        label = example.get("label") or f"invoice {i}"
        parts.append(
            f"\n--- ADDITIONAL SAME-LAYOUT INVOICE #{i} ({label}) — the rules must ALSO reproduce this exactly ---"
        )
        parts.append("Ground truth:")
        parts.append(_json.dumps(example.get("target"), indent=2, default=str))
        example_evidence = example.get("layout_evidence")
        if example_evidence:
            parts.append(f"Evidence — where invoice #{i}'s ground-truth values sit in its layout:")
            parts.append(_json.dumps(example_evidence, indent=2, default=str))
        example_pages = example.get("page_texts") or []
        if example_pages:
            parts.append(f"Coordinate-aware layout of invoice #{i}:")
            for p, text in enumerate(example_pages, start=1):
                parts.append(f"\n=== INVOICE #{i} PAGE {p} ===\n{text}")

    if feedback:
        parts.append(
            "\n--- YOUR PREVIOUS RULE DOCUMENT WAS WRONG ---\n"
            "When executed, the previous rules did NOT reproduce the ground truth. Fix them. Details:\n"
            + feedback
        )

    if layout_evidence:
        parts.append("\n--- EVIDENCE: where each ground-truth value sits in the layout ---")
        parts.append(_json.dumps(layout_evidence, indent=2, default=str))

    parts.append("\n--- COORDINATE-AWARE INVOICE LAYOUT (each cell shows its x range) ---")
    for i, text in enumerate(page_texts):
        parts.append(f"\n=== PAGE {i + 1} ===\n{text}")

    return "\n".join(parts)

