"""Unit tests for the deterministic reconciliation engine."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from stencil.validation.reconciler import reconcile
from stencil.validation.schema import (
    CanonicalInvoice,
    ExtractionMetadata,
    ExtractionPath,
    InvoiceHeader,
    LineItem,
    OutputType,
)

# ── Helpers ──────────────────────────────────────────────────


def _make_header(**overrides) -> InvoiceHeader:
    defaults = dict(
        supplier_name="TestSupplier",
        invoice_number="INV-001",
        invoice_date=date(2026, 1, 15),
    )
    defaults.update(overrides)
    return InvoiceHeader(**defaults)


def _make_metadata() -> ExtractionMetadata:
    return ExtractionMetadata(extraction_path=ExtractionPath.AI)


def _make_invoice(
    line_items: list[LineItem],
    subtotal: Decimal | None = None,
    tax: Decimal | None = None,
    fees: Decimal | None = None,
    total_due: Decimal | None = None,
    current_charges: Decimal | None = None,
    tax_rate: Decimal | None = None,
) -> CanonicalInvoice:
    return CanonicalInvoice(
        intake_id="test-intake-001",
        output_type=OutputType.STANDARD,
        header=_make_header(),
        line_items=line_items,
        subtotal=subtotal,
        tax=tax,
        fees=fees,
        current_charges=current_charges,
        total_due=total_due,
        tax_rate=tax_rate,
        metadata=_make_metadata(),
    )


def _li(amount: str, charge_type: str = "recurring", **kw) -> LineItem:
    return LineItem(
        line_number=kw.pop("line_number", 1),
        description=kw.pop("description", "Test charge"),
        amount=Decimal(amount),
        charge_type=charge_type,
        **kw,
    )


# Pin the variance threshold so tests don't depend on .env / runtime settings.
@pytest.fixture(autouse=True)
def _mock_settings():
    from stencil import runtime_settings

    real = runtime_settings.runtime_value

    def fake(name):
        if name == "reconciliation_variance_threshold":
            return 0.01  # 1%
        return real(name)

    with patch("stencil.validation.reconciler.runtime_settings.runtime_value", side_effect=fake):
        yield


# ── Perfect match ────────────────────────────────────────────


class TestPerfectMatch:
    def test_exact_match_zero_variance(self):
        items = [_li("100.00"), _li("50.00")]
        inv = _make_invoice(items, subtotal=Decimal("150.00"), total_due=Decimal("150.00"))

        result = reconcile(inv)

        assert result.is_reconciled is True
        assert result.variance == Decimal("0")
        assert result.variance_pct == 0.0
        assert result.line_items_sum == Decimal("150.00")
        assert result.computed_total == Decimal("150.00")
        assert len(result.warnings) == 0

    def test_exact_match_with_tax_and_fees(self):
        items = [
            _li("100.00", "recurring"),
            _li("10.00", "tax"),
            _li("5.00", "fee"),
        ]
        inv = _make_invoice(
            items,
            subtotal=Decimal("100.00"),
            tax=Decimal("10.00"),
            total_due=Decimal("115.00"),
        )

        result = reconcile(inv)

        assert result.is_reconciled is True
        assert result.computed_total == Decimal("115.00")
        assert len(result.warnings) == 0

    def test_exact_match_with_invoice_level_tax_only(self):
        items = [_li("100.00", "recurring"), _li("50.00", "recurring")]
        inv = _make_invoice(
            items,
            subtotal=Decimal("150.00"),
            tax=Decimal("15.00"),
            total_due=Decimal("165.00"),
        )

        result = reconcile(inv)

        assert result.is_reconciled is True
        assert result.computed_total == Decimal("165.00")
        assert not any("tax mismatch" in warning.lower() for warning in result.warnings)

    def test_auto_allocated_tax_reconciles_delivered_ext_tax(self):
        # Invoice-level-only tax under the default (auto) mode: the delivered
        # EXT_TAX is allocated across lines and must sum to the stated tax, so no
        # "delivered EXT_TAX" mismatch is raised.
        from stencil.output.xlsx_writer import delivered_tax_sum

        items = [_li("600.00", "recurring"), _li("400.00", "recurring", line_number=2)]
        inv = _make_invoice(
            items,
            subtotal=Decimal("1000.00"),
            tax=Decimal("123.45"),
            current_charges=Decimal("1123.45"),
            total_due=Decimal("1123.45"),
        )

        result = reconcile(inv)

        assert delivered_tax_sum(inv) == Decimal("123.45")
        assert result.is_reconciled is True
        assert not any("ext_tax" in warning.lower() for warning in result.warnings)


# ── Per-line tax_amount (Lumen-style "Taxes, Fees, Surcharges" column) ──


class TestPerLineTax:
    def test_per_line_tax_is_authoritative_and_combines_fees(self):
        """When line items carry a per-line tax_amount that already combines
        taxes + fees + surcharges, the reconciler must use that sum and NOT add
        the overlapping invoice-level tax/fees on top (the Lumen double-count bug)."""
        items = [
            _li("13860.00", "recurring", tax_amount=Decimal("1320.86")),
            _li("3561.81", "recurring", tax_amount=Decimal("2858.82")),
        ]
        # invoice.tax is the COMBINED taxes+fees+surcharges; invoice.fees is the
        # fees subset already inside that combined figure.
        inv = _make_invoice(
            items,
            subtotal=Decimal("17421.81"),
            tax=Decimal("4179.68"),
            fees=Decimal("3000.00"),
            current_charges=Decimal("21601.49"),
            total_due=Decimal("21601.49"),
        )

        result = reconcile(inv)

        # computed = line items (17421.81) + per-line tax (4179.68) = 21601.49
        assert result.computed_total == Decimal("21601.49")
        assert result.is_reconciled is True

    def test_combined_stated_tax_does_not_double_count_fees(self):
        """No per-line tax_amount, but invoice.tax already includes the stated
        fees (fees < tax). The guard must not add fees on top of tax."""
        items = [_li("100584.31", "recurring")]
        inv = _make_invoice(
            items,
            subtotal=Decimal("100584.31"),
            tax=Decimal("56969.88"),
            fees=Decimal("47878.02"),
            current_charges=Decimal("157554.19"),
            total_due=Decimal("155981.90"),
        )

        result = reconcile(inv)

        # computed = 100584.31 + 56969.88 (fees NOT added again) = 157554.19
        assert result.computed_total == Decimal("157554.19")
        assert result.is_reconciled is True

    def test_missing_rate_based_row_tax_uses_normalized_invoice_rate(self):
        items = [
            _li("100.00", "recurring", tax_amount=Decimal("19.00")),
            _li("72.00", "recurring", tax_amount=None, line_number=2),
            _li("527.50", "recurring", tax_amount=None, line_number=3),
        ]
        inv = _make_invoice(
            items,
            subtotal=Decimal("699.50"),
            tax=Decimal("132.91"),
            total_due=Decimal("832.41"),
            tax_rate=Decimal("19"),
        )

        result = reconcile(inv)

        assert result.computed_total == Decimal("832.41")
        assert result.is_reconciled is True
        assert not any("Tax mismatch" in warning for warning in result.warnings)


# ── Within threshold ─────────────────────────────────────────


class TestWithinThreshold:
    def test_small_variance_passes(self):
        """0.5% variance is within the 1% threshold."""
        items = [_li("100.00")]
        inv = _make_invoice(items, total_due=Decimal("100.50"))

        result = reconcile(inv)

        assert result.is_reconciled is True
        assert abs(result.variance_pct) < 0.01

    def test_penny_rounding(self):
        """Rounding off by a penny should still pass."""
        items = [_li("33.33"), _li("33.33"), _li("33.34")]
        inv = _make_invoice(items, subtotal=Decimal("100.00"), total_due=Decimal("100.00"))

        result = reconcile(inv)

        assert result.is_reconciled is True


# ── Over threshold (failure) ─────────────────────────────────


class TestOverThreshold:
    def test_large_variance_fails(self):
        """10% variance should fail with a warning."""
        items = [_li("100.00")]
        inv = _make_invoice(items, total_due=Decimal("110.00"))

        result = reconcile(inv)

        assert result.is_reconciled is False
        assert any("variance" in w.lower() or "threshold" in w.lower() for w in result.warnings)

    def test_subtotal_mismatch_warns(self):
        items = [_li("100.00"), _li("50.00")]
        inv = _make_invoice(items, subtotal=Decimal("200.00"), total_due=Decimal("150.00"))

        result = reconcile(inv)

        assert any("subtotal mismatch" in w.lower() for w in result.warnings)

    def test_tax_mismatch_warns(self):
        items = [_li("100.00"), _li("15.00", "tax")]
        inv = _make_invoice(items, tax=Decimal("10.00"), total_due=Decimal("115.00"))

        result = reconcile(inv)

        assert any("tax mismatch" in w.lower() for w in result.warnings)


# ── Missing stated total ─────────────────────────────────────


class TestMissingTotal:
    def test_no_stated_total_warns(self):
        items = [_li("100.00")]
        inv = _make_invoice(items, total_due=None)

        result = reconcile(inv)

        assert any("no stated total" in w.lower() for w in result.warnings)
        assert result.is_reconciled is False
        assert result.verification_status == "unverifiable"

    def test_zero_stated_total(self):
        items = [_li("0.00")]
        inv = _make_invoice(items, total_due=Decimal("0"))

        result = reconcile(inv)

        # Zero total means variance calc avoids division by zero
        assert result.is_reconciled is True


# ── Zero-amount line items ───────────────────────────────────


class TestZeroAmountLines:
    def test_zero_amount_flagged(self):
        items = [_li("100.00"), _li("0.00")]
        inv = _make_invoice(items, total_due=Decimal("100.00"))

        result = reconcile(inv)

        assert any("zero amount" in w.lower() for w in result.warnings)

    def test_no_warning_when_no_zero_amounts(self):
        items = [_li("50.00"), _li("50.00")]
        inv = _make_invoice(items, total_due=Decimal("100.00"))

        result = reconcile(inv)

        assert not any("zero amount" in w.lower() for w in result.warnings)


# ── Tax/fee separation ───────────────────────────────────────


class TestTaxFeeSeparation:
    def test_surcharges_treated_as_fees(self):
        items = [
            _li("100.00", "recurring"),
            _li("8.00", "tax"),
            _li("5.00", "surcharge"),
        ]
        inv = _make_invoice(items, total_due=Decimal("113.00"))

        result = reconcile(inv)

        # surcharge should be in fee_items_sum, not line_items_sum
        assert result.line_items_sum == Decimal("100.00")
        assert result.computed_total == Decimal("113.00")
        assert result.is_reconciled is True

    def test_credits_in_subtotal(self):
        """Credits are not tax/fee, so they should be in line_items_sum."""
        items = [
            _li("200.00", "recurring"),
            _li("-50.00", "credit"),
        ]
        inv = _make_invoice(items, subtotal=Decimal("150.00"), total_due=Decimal("150.00"))

        result = reconcile(inv)

        assert result.line_items_sum == Decimal("150.00")
        assert result.is_reconciled is True


# ── Credit-bearing invoices (current_charges target) ─────────


class TestCurrentChargesTarget:
    def test_reconciles_against_current_charges_not_net_total(self):
        """Line items sum to current charges; total_due nets a prior credit.

        Mirrors the Lumen case: current charges 157554.19, prior credit
        1572.29, total due 155981.90. Reconciling against current_charges
        must pass; against total_due it would have failed.
        """
        items = [_li("157554.19", "recurring")]
        inv = _make_invoice(
            items,
            current_charges=Decimal("157554.19"),
            total_due=Decimal("155981.90"),
        )

        result = reconcile(inv)

        assert result.is_reconciled is True
        assert result.reconcile_target == Decimal("157554.19")
        assert result.variance == Decimal("0")
        assert result.stated_total == Decimal("155981.90")

    def test_falls_back_to_total_due_when_no_current_charges(self):
        items = [_li("100.00")]
        inv = _make_invoice(items, total_due=Decimal("100.00"))

        result = reconcile(inv)

        assert result.is_reconciled is True
        assert result.reconcile_target == Decimal("100.00")

    def test_mismatch_against_all_targets_fails(self):
        # computed (100) matches neither current_charges (150) nor total_due (180);
        # the closest target (current charges, 150) is chosen and it still fails.
        items = [_li("100.00")]
        inv = _make_invoice(
            items,
            current_charges=Decimal("150.00"),
            total_due=Decimal("180.00"),
        )

        result = reconcile(inv)

        assert result.is_reconciled is False
        assert result.reconcile_target == Decimal("150.00")

    def test_pretax_current_charges_reconciles_against_total_due(self):
        # Colt-style: current_charges is stated PRE-tax (= subtotal) while
        # total_due is tax-inclusive. computed_total includes tax, so the
        # reconciler must pick total_due (the closest target), not current_charges.
        items = [
            _li("3996.34", "recurring"),
            _li("3996.34", "recurring"),
            _li("1838.31", "tax"),
        ]
        inv = _make_invoice(
            items,
            subtotal=Decimal("7992.68"),
            tax=Decimal("1838.31"),
            current_charges=Decimal("7992.68"),  # pre-tax (mis-stated as subtotal)
            total_due=Decimal("9830.99"),         # tax-inclusive
        )

        result = reconcile(inv)

        assert result.computed_total == Decimal("9830.99")
        assert result.reconcile_target == Decimal("9830.99")
        assert result.is_reconciled is True


# ── Decimal precision edge cases ─────────────────────────────


class TestDecimalPrecision:
    def test_many_small_items(self):
        """Sum of many small decimals should not lose precision."""
        items = [_li("0.01", line_number=i + 1) for i in range(100)]
        inv = _make_invoice(items, total_due=Decimal("1.00"))

        result = reconcile(inv)

        assert result.line_items_sum == Decimal("1.00")
        assert result.is_reconciled is True

    def test_large_amounts(self):
        items = [_li("999999.99"), _li("0.01")]
        inv = _make_invoice(items, total_due=Decimal("1000000.00"))

        result = reconcile(inv)

        assert result.is_reconciled is True

    def test_negative_variance(self):
        """Computed < stated should still report correctly."""
        items = [_li("90.00")]
        inv = _make_invoice(items, total_due=Decimal("100.00"))

        result = reconcile(inv)

        assert result.variance == Decimal("-10.00")
        assert result.is_reconciled is False


class TestUncoercedTotals:
    def test_string_totals_in_fields_do_not_crash_reconcile(self):
        """Totals stored as strings (e.g. from JSON round-trip) must not break math."""
        items = [_li("100.00")]
        inv = _make_invoice(items, subtotal=Decimal("100.00"), total_due=Decimal("100.00"))
        inv.fields["subtotal"] = "100.00"
        inv.fields["total_due"] = "100.00"

        result = reconcile(inv)

        assert result.is_reconciled is True
        assert result.variance == Decimal("0")


# ── Per-supplier anchored reconciliation ─────────────────────


def _anchored_schema():
    """Default schema with the subtotal + document tax fields given printed
    labels — i.e. a profile that 'configures' reconciliation anchors."""
    from stencil.fields.loader import default_field_schema
    from stencil.fields.schema import FieldSchema

    base = default_field_schema()
    fields = []
    for f in base.fields:
        if f.scope.value == "document" and f.name == "subtotal":
            f = f.model_copy(update={"label_hint": "TOTAL MONTHLY SERVICE"})
        elif f.scope.value == "document" and f.name == "tax":
            f = f.model_copy(update={"label_hint": "TOTAL TAX APPLIED"})
        fields.append(f)
    return FieldSchema(schema_id=base.schema_id, name=base.name, fields=fields)


def _tax_included_subtotal_schema():
    from stencil.fields.loader import default_field_schema
    from stencil.fields.schema import FieldSchema

    base = default_field_schema()
    fields = []
    for f in base.fields:
        if f.scope.value == "document" and f.name == "subtotal":
            f = f.model_copy(update={"label_hint": "Total (Includes taxes)"})
        elif f.scope.value == "document" and f.name == "tax":
            f = f.model_copy(update={"label_hint": "Total taxes"})
        fields.append(f)
    return FieldSchema(schema_id=base.schema_id, name=base.name, fields=fields)


def test_anchored_reconciles_against_subtotal_and_per_line_tax():
    items = [
        LineItem(line_number=1, service_id="S1", description="svc", charge_type="recurring",
                 amount=Decimal("60.86"), tax_amount=Decimal("3.65")),
        LineItem(line_number=2, service_id="S2", description="svc", charge_type="recurring",
                 amount=Decimal("60.86"), tax_amount=Decimal("5.48")),
    ]
    inv = _make_invoice(items, subtotal=Decimal("121.72"), tax=Decimal("9.13"),
                        total_due=Decimal("2225.48"))
    res = reconcile(inv, _anchored_schema())
    assert res is not None
    # Reconciled against the configured subtotal, NOT total_due.
    assert res.reconcile_target == Decimal("121.72")
    assert res.computed_total == Decimal("121.72")
    assert res.is_reconciled is True
    assert not any("Tax mismatch" in w for w in res.warnings)


def test_tax_included_subtotal_label_reconciles_against_delivered_gross():
    items = [
        LineItem(line_number=1, service_id="416-294-0179", description="svc", charge_type="recurring",
                 amount=Decimal("37.41"), tax_amount=Decimal("19.09")),
        LineItem(line_number=2, service_id="437-332-6295", description="svc", charge_type="recurring",
                 amount=Decimal("50.00"), tax_amount=Decimal("6.50")),
    ]
    inv = _make_invoice(
        items,
        subtotal=Decimal("113.00"),
        tax=Decimal("25.59"),
        current_charges=Decimal("113.00"),
        total_due=Decimal("939.38"),
    )

    res = reconcile(inv, _tax_included_subtotal_schema())

    assert res is not None
    assert res.computed_total == Decimal("113.00")
    assert res.reconcile_target == Decimal("113.00")
    assert res.is_reconciled is True


def test_anchored_flags_tax_mismatch_from_per_line_tax():
    items = [LineItem(line_number=1, service_id="S1", description="svc", charge_type="recurring",
                      amount=Decimal("100.00"), tax_amount=Decimal("3.00"))]
    inv = _make_invoice(items, subtotal=Decimal("100.00"), tax=Decimal("10.00"),
                        total_due=Decimal("110.00"))
    res = reconcile(inv, _anchored_schema())
    assert res.is_reconciled is False  # amounts match, but per-line tax 3.00 != stated 10.00
    assert any("Tax mismatch" in w for w in res.warnings)


# ── excluded_total: a printed section the deliverable does not cover ──────────


def _excluded_total_schema():
    """Base schema plus a document field flagged as an excluded section total."""
    from stencil.fields.loader import default_field_schema, merge_field_schema
    from stencil.fields.schema import FieldDef, FieldRole, FieldScope, FieldType

    override = FieldDef(
        name="total_account_level_charges",
        scope=FieldScope.DOCUMENT,
        type=FieldType.CURRENCY,
        role=FieldRole.EXCLUDED_TOTAL,
        label_hint="Total Account Level Charges",
    )
    return merge_field_schema(default_field_schema(), [override])


class TestExcludedTotal:
    def test_excluded_section_total_is_subtracted_from_target(self):
        # Real Lumen 5-CMXNHDKC figures: SERVICE LEVEL ACTIVITY sums to
        # 14099.22 amount + 1152.47 tax; Current Charges 16090.38 also includes
        # ACCOUNT LEVEL CHARGES, whose printed section total is 838.69.
        items = [_li("14099.22", "recurring", tax_amount=Decimal("1152.47"))]
        inv = _make_invoice(items, current_charges=Decimal("16090.38"), total_due=Decimal("-2620.10"))
        inv.fields["total_account_level_charges"] = "838.69"

        result = reconcile(inv, _excluded_total_schema())

        assert result.computed_total == Decimal("15251.69")
        assert result.reconcile_target == Decimal("15251.69")  # 16090.38 - 838.69
        assert result.is_reconciled is True

    def test_missing_excluded_value_leaves_target_unchanged(self):
        items = [_li("100.00", "recurring")]
        inv = _make_invoice(items, current_charges=Decimal("100.00"), total_due=Decimal("100.00"))
        # no total_account_level_charges extracted

        result = reconcile(inv, _excluded_total_schema())

        assert result.reconcile_target == Decimal("100.00")
        assert result.is_reconciled is True

    def test_no_excluded_role_is_a_no_op(self):
        items = [_li("100.00", "recurring")]
        inv = _make_invoice(items, current_charges=Decimal("100.00"), total_due=Decimal("100.00"))
        inv.fields["total_account_level_charges"] = "838.69"  # present but base schema ignores it

        result = reconcile(inv)  # default schema has no excluded_total role

        assert result.reconcile_target == Decimal("100.00")
