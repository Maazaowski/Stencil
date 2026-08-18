"""Deterministic reconciliation — verifies extraction math without AI.

Compares computed line item sums against stated totals on the document.
Flags variances beyond the configured threshold.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import structlog

from stencil import runtime_settings
from stencil.fields.loader import default_field_schema
from stencil.fields.schema import FieldRole, FieldSchema, FieldScope
from stencil.output.xlsx_writer import delivered_tax_sum
from stencil.validation.schema import ExtractedDocument, ReconciliationResult

logger = structlog.get_logger()

ZERO = Decimal("0")
ONE = Decimal("1")
ONE_HUNDRED = Decimal("100")
CENT = Decimal("0.01")

_EXCLUDED_CHARGE_TYPES = frozenset({"tax", "fee", "surcharge"})


def reconcile(
    invoice: ExtractedDocument,
    schema: FieldSchema | None = None,
) -> ReconciliationResult | None:
    """Run deterministic reconciliation on an extracted document."""
    schema = schema or default_field_schema()
    amount_fields = schema.fields_with_role(FieldRole.AMOUNT)
    total_fields = schema.fields_with_role(FieldRole.TOTAL)
    subtotal_fields = schema.fields_with_role(FieldRole.SUBTOTAL)

    if not amount_fields or (not total_fields and not subtotal_fields):
        return None

    # A profile "configures" a reconciliation anchor by giving the field a printed
    # label hint. Only then do we reconcile against it (so default profiles like
    # Colt/euNetworks keep their existing computed-total-vs-current/total behaviour).
    subtotal_anchor = any(_is_pre_tax_subtotal_anchor(f.label_hint) for f in subtotal_fields)
    tax_anchor = any(
        (f.label_hint or "").strip()
        for f in schema.fields_with_role(FieldRole.TAX)
        if f.scope == FieldScope.DOCUMENT
    )
    return _reconcile_invoice(
        invoice,
        subtotal_anchor=subtotal_anchor,
        tax_anchor=tax_anchor,
        excluded_total=_excluded_total(invoice, schema),
    )


def _excluded_total(invoice: ExtractedDocument, schema: FieldSchema) -> Decimal:
    """Sum of printed totals for sections the deliverable does not cover.

    Lumen prints ACCOUNT LEVEL CHARGES outside SERVICE LEVEL ACTIVITY, so the
    delivered rows can never sum to Current Charges. Subtracting the excluded
    section's printed total makes the target the charges the rows actually
    represent — the invoice reconciles against itself, with no blueprint.
    """
    total = ZERO
    for field in schema.fields_with_role(FieldRole.EXCLUDED_TOTAL):
        if field.scope != FieldScope.DOCUMENT:
            continue
        value = invoice.fields.get(field.name)
        if value is None:
            continue
        try:
            total += Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
    return total


def _is_pre_tax_subtotal_anchor(label_hint: str | None) -> bool:
    label = (label_hint or "").strip().lower()
    if not label:
        return False
    if "includes tax" in label or "including tax" in label or "includes taxes" in label or "including taxes" in label:
        return False
    return True


def _reconcile_invoice(
    invoice: ExtractedDocument,
    *,
    subtotal_anchor: bool = False,
    tax_anchor: bool = False,
    excluded_total: Decimal = ZERO,
) -> ReconciliationResult:
    """Invoice-standard reconciliation logic (charge_type exclusions preserved)."""
    warnings = []

    line_items_sum = sum(
        (li.amount for li in invoice.line_items if li.charge_type not in _EXCLUDED_CHARGE_TYPES),
        ZERO,
    )
    tax_items_sum = sum(
        (li.amount for li in invoice.line_items if li.charge_type == "tax"),
        ZERO,
    )
    fee_items_sum = sum(
        (li.amount for li in invoice.line_items if li.charge_type in ("fee", "surcharge")),
        ZERO,
    )

    per_line_tax_sum = sum(
        (li.tax_amount for li in invoice.line_items if li.tax_amount is not None),
        ZERO,
    )
    # Tax actually attached to the delivered rows: per-line tax when present,
    # else separate tax-type line items.
    effective_tax_sum = (
        _effective_tax_sum_with_rate_fallback(invoice, per_line_tax_sum)
        if per_line_tax_sum > ZERO
        else tax_items_sum
    )

    stated_subtotal = invoice.subtotal
    stated_tax = invoice.tax
    stated_fees = invoice.fees
    stated_total = invoice.total_due
    delivered_ext_tax_sum = delivered_tax_sum(invoice)

    threshold = Decimal(str(runtime_settings.runtime_value("reconciliation_variance_threshold")))

    # ── Per-supplier path: reconcile delivered amounts/tax against the configured
    # totals (e.g. "TOTAL MONTHLY SERVICE" / "TOTAL TAX APPLIED"). ──
    if subtotal_anchor and stated_subtotal is not None:
        return _reconcile_against_anchors(
            invoice,
            line_items_sum=line_items_sum,
            effective_tax_sum=effective_tax_sum,
            stated_subtotal=stated_subtotal,
            stated_tax=stated_tax,
            stated_fees=stated_fees,
            stated_total=stated_total,
            tax_anchor=tax_anchor,
            threshold=threshold,
        )

    if per_line_tax_sum > ZERO:
        tax_total = effective_tax_sum
        fee_total = fee_items_sum
        if (
            fee_total == ZERO
            and stated_fees is not None
            and (stated_tax is None or stated_fees > stated_tax)
        ):
            fee_total = stated_fees
    else:
        tax_total = tax_items_sum
        if tax_total == ZERO and stated_tax is not None:
            tax_total = stated_tax

        fee_total = fee_items_sum
        if fee_total == ZERO and stated_fees is not None:
            fee_total = stated_fees

        if (
            tax_items_sum == ZERO
            and fee_items_sum == ZERO
            and stated_tax is not None
            and stated_fees is not None
            and stated_fees > ZERO
            and stated_fees < stated_tax
        ):
            fee_total = ZERO

    computed_total = line_items_sum + tax_total + fee_total

    if stated_subtotal is not None:
        subtotal_diff = abs(line_items_sum - stated_subtotal)
        if subtotal_diff > Decimal("0.01"):
            warnings.append(
                f"Subtotal mismatch: computed {line_items_sum} vs stated {stated_subtotal} "
                f"(diff: {subtotal_diff})"
            )

    if stated_tax is not None and effective_tax_sum != ZERO:
        tax_diff = abs(effective_tax_sum - stated_tax)
        if tax_diff > Decimal("0.01"):
            warnings.append(
                f"Tax mismatch: line item taxes {effective_tax_sum} vs stated {stated_tax} "
                f"(diff: {tax_diff})"
            )

    if stated_tax is not None and delivered_ext_tax_sum != ZERO:
        delivered_tax_diff = abs(delivered_ext_tax_sum - stated_tax)
        if delivered_tax_diff > Decimal("0.01"):
            warnings.append(
                f"Delivered EXT_TAX mismatch: output taxes {delivered_ext_tax_sum} vs stated "
                f"{stated_tax} (diff: {delivered_tax_diff})"
            )

    stated_current_charges = invoice.current_charges
    target_candidates: list[tuple[str, Decimal]] = []
    if stated_current_charges is not None:
        target_candidates.append(
            ("current charges", stated_current_charges - excluded_total)
        )
    if stated_total is not None:
        target_candidates.append(("total due", stated_total - excluded_total))

    target_value = computed_total
    if target_candidates:
        target_label, reconcile_target = min(
            target_candidates, key=lambda c: abs(computed_total - c[1])
        )
    elif stated_subtotal is not None:
        target_label, reconcile_target = ("subtotal", stated_subtotal)
        target_value = line_items_sum
    else:
        target_label, reconcile_target = ("total due", None)

    if reconcile_target is not None and reconcile_target != ZERO:
        variance = target_value - reconcile_target
        variance_pct = float(abs(variance) / abs(reconcile_target))
    else:
        variance = ZERO
        variance_pct = 0.0
        if reconcile_target is None:
            warnings.append("No stated total on invoice — cannot verify")

    is_reconciled = (
        reconcile_target is not None
        and abs(Decimal(str(variance_pct))) <= threshold
    )
    verification_status = (
        "reconciled"
        if is_reconciled
        else ("unverifiable" if reconcile_target is None else "mismatch")
    )

    if not is_reconciled and reconcile_target is not None:
        warnings.append(
            f"Total variance {variance_pct:.4%} exceeds threshold "
            f"{float(runtime_settings.runtime_value('reconciliation_variance_threshold')):.2%}: "
            f"computed {computed_total} vs {target_label} {reconcile_target}"
        )

    zero_amount_lines = [li for li in invoice.line_items if li.amount == ZERO]
    if zero_amount_lines:
        warnings.append(f"{len(zero_amount_lines)} line items have zero amount")

    result = ReconciliationResult(
        line_items_sum=line_items_sum,
        stated_subtotal=stated_subtotal,
        stated_tax=stated_tax,
        stated_fees=stated_fees,
        stated_total=stated_total,
        reconcile_target=reconcile_target,
        computed_total=computed_total,
        variance=variance,
        variance_pct=variance_pct,
        is_reconciled=is_reconciled,
        verification_status=verification_status,
        warnings=warnings,
    )

    logger.info(
        "reconciliation.completed",
        is_reconciled=is_reconciled,
        variance=str(variance),
        variance_pct=f"{variance_pct:.4%}",
        warnings_count=len(warnings),
    )

    return result


def _normalize_tax_rate(tax_rate: Decimal | None) -> Decimal | None:
    """Return tax rates as decimal multipliers; accept extracted whole percents."""
    if tax_rate is None:
        return None
    if tax_rate > ONE:
        return tax_rate / ONE_HUNDRED
    return tax_rate


def _effective_tax_sum_with_rate_fallback(
    invoice: ExtractedDocument,
    per_line_tax_sum: Decimal,
) -> Decimal:
    """Fill blank delivered-row taxes when explicit rows prove a rate-based layout."""
    tax_rate = _normalize_tax_rate(invoice.tax_rate)
    if tax_rate is None:
        return per_line_tax_sum

    explicit_rows = [
        li for li in invoice.line_items
        if li.charge_type not in _EXCLUDED_CHARGE_TYPES
        and li.tax_amount is not None
        and li.amount not in (None, ZERO)
    ]
    if not explicit_rows:
        return per_line_tax_sum

    explicit_rows_match_rate = all(
        abs(
            li.tax_amount
            - (li.amount * tax_rate).quantize(CENT, rounding=ROUND_HALF_UP)
        ) <= CENT
        for li in explicit_rows
    )
    if not explicit_rows_match_rate:
        return per_line_tax_sum

    missing_tax_sum = sum(
        (
            (li.amount * tax_rate).quantize(CENT, rounding=ROUND_HALF_UP)
            for li in invoice.line_items
            if li.charge_type not in _EXCLUDED_CHARGE_TYPES
            and li.tax_amount is None
            and li.amount not in (None, ZERO)
        ),
        ZERO,
    )
    return per_line_tax_sum + missing_tax_sum


def _reconcile_against_anchors(
    invoice: ExtractedDocument,
    *,
    line_items_sum: Decimal,
    effective_tax_sum: Decimal,
    stated_subtotal: Decimal,
    stated_tax: Decimal | None,
    stated_fees: Decimal | None,
    stated_total: Decimal | None,
    tax_anchor: bool,
    threshold: Decimal,
) -> ReconciliationResult:
    """Per-supplier reconciliation: the delivered line amounts must equal the
    configured amount total (e.g. ``TOTAL MONTHLY SERVICE``), and — when a tax
    anchor is configured — the delivered tax must equal the configured tax total
    (e.g. ``TOTAL TAX APPLIED``). Both must hold for ``is_reconciled``."""
    warnings: list[str] = []

    variance = line_items_sum - stated_subtotal
    variance_pct = float(abs(variance) / abs(stated_subtotal)) if stated_subtotal != ZERO else 0.0
    amount_ok = Decimal(str(variance_pct)) <= threshold
    if not amount_ok:
        warnings.append(
            f"Amount mismatch: line items {line_items_sum} vs stated total "
            f"{stated_subtotal} (diff: {abs(variance)})"
        )

    tax_ok = True
    if tax_anchor and stated_tax is not None:
        tax_diff = abs(effective_tax_sum - stated_tax)
        if tax_diff > Decimal("0.01"):
            tax_ok = False
            warnings.append(
                f"Tax mismatch: line item taxes {effective_tax_sum} vs stated "
                f"{stated_tax} (diff: {tax_diff})"
            )

    is_reconciled = amount_ok and tax_ok

    zero_amount_lines = [li for li in invoice.line_items if li.amount == ZERO]
    if zero_amount_lines:
        warnings.append(f"{len(zero_amount_lines)} line items have zero amount")

    result = ReconciliationResult(
        line_items_sum=line_items_sum,
        stated_subtotal=stated_subtotal,
        stated_tax=stated_tax,
        stated_fees=stated_fees,
        stated_total=stated_total,
        reconcile_target=stated_subtotal,
        computed_total=line_items_sum,
        variance=variance,
        variance_pct=variance_pct,
        is_reconciled=is_reconciled,
        verification_status="reconciled" if is_reconciled else "mismatch",
        warnings=warnings,
    )
    logger.info(
        "reconciliation.completed",
        is_reconciled=is_reconciled,
        variance=str(variance),
        variance_pct=f"{variance_pct:.4%}",
        warnings_count=len(warnings),
        anchored=True,
    )
    return result
