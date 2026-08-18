"""Deterministic verify-and-repair of header/total rules against ground truth."""

from datetime import date
from decimal import Decimal

from stencil.models.authoring import _verify_and_repair_rules
from stencil.models.interpreter import find_header_value, find_total_value
from stencil.models.schema import ExtractionModel, HeaderFieldRule


def _row(row_id: str, *cells: tuple[str, int, int]) -> str:
    parts = [
        f"[{row_id}.c{i} c{i} text x={x0}-{x1}] {text}"
        for i, (text, x0, x1) in enumerate(cells)
    ]
    return f"{row_id} role=detail " + " | ".join(parts)


PAGE = "\n".join([
    "LAYOUT PAGE 1 (595x842) id=p1 (cell x spans normalized to 0-1000 of page width)",
    _row("p1.r0", ("Invoice No.", 30, 130), ("708205451", 700, 790)),
    _row("p1.r1", ("Invoice Date", 30, 140), ("01/02/2024", 700, 790)),
    _row("p1.r2", ("VAT 23.00 %", 30, 140)),
    _row("p1.r3", ("Total excl. VAT", 30, 160), ("7,992.68", 700, 790)),
    _row("p1.r4", ("Total Amount Due", 30, 170), ("9,831.00", 700, 790)),
])

TARGET = {
    "header": {
        "invoice_number": "708205451",
        "invoice_date": "2024-02-01",
    },
    "totals": {
        "subtotal": "7992.68",
        "total_due": "9831.00",
        "tax_rate": "0.23",
    },
}


def _model(**overrides) -> ExtractionModel:
    return ExtractionModel(
        model_id="colt.standard.v1",
        supplier_profile_id="colt.standard.v1",
        supplier="Colt",
        **overrides,
    )


def test_correct_rules_are_left_alone():
    model = _model(
        header_fields={
            "invoice_number": HeaderFieldRule(label="Invoice No."),
            "invoice_date": HeaderFieldRule(label="Invoice Date", date_format="%d/%m/%Y"),
        },
        totals={
            "subtotal": HeaderFieldRule(label="Total excl. VAT", required=False),
            "total_due": HeaderFieldRule(label="Total Amount Due", required=False),
            "tax_rate": HeaderFieldRule(label="VAT", required=False),
        },
    )
    repaired = _verify_and_repair_rules(model, TARGET, [PAGE])
    assert repaired == []
    assert model.header_fields["invoice_number"].label == "Invoice No."


def test_colt_bug_vat_anchored_subtotal_no_longer_yields_the_rate():
    # The Colt bug: the AI anchored subtotal on 'VAT' and the old lookup grabbed
    # the 23.00 rate. The percent-skip validator now rejects '23.00 %' and falls
    # through to the real amount — the rule self-heals without repair.
    rule = HeaderFieldRule(label="VAT", required=False)
    _raw, amount = find_total_value("subtotal", rule, [PAGE])
    assert amount == Decimal("7992.68")


def test_broken_subtotal_rule_is_repaired_from_layout():
    # A label that doesn't exist on the page can never verify — the repair pass
    # must synthesize a working rule from the value cell's neighbouring label.
    model = _model(
        header_fields={"invoice_number": HeaderFieldRule(label="Invoice No.")},
        totals={"subtotal": HeaderFieldRule(label="Net Charges Summary", required=False)},
    )
    repaired = _verify_and_repair_rules(model, TARGET, [PAGE])
    assert "totals.subtotal" in repaired

    _raw, amount = find_total_value("subtotal", model.totals["subtotal"], [PAGE])
    assert amount == Decimal("7992.68")
    assert model.totals["subtotal"].label == "Total excl. VAT"


def test_missing_rules_are_synthesized():
    model = _model(header_fields={"invoice_number": HeaderFieldRule(label="Invoice No.")})
    repaired = _verify_and_repair_rules(model, TARGET, [PAGE])
    assert {"invoice_date", "totals.subtotal", "totals.total_due", "totals.tax_rate"} <= set(repaired)

    _raw, value = find_header_value("invoice_date", model.header_fields["invoice_date"], [PAGE])
    assert value == date(2024, 2, 1)
    assert model.header_fields["invoice_date"].date_format == "%d/%m/%Y"

    _raw, rate = find_total_value("tax_rate", model.totals["tax_rate"], [PAGE])
    assert rate == Decimal("0.23")

    _raw, total = find_total_value("total_due", model.totals["total_due"], [PAGE])
    assert total == Decimal("9831.00")


def test_wrong_date_label_is_repaired():
    model = _model(
        header_fields={
            "invoice_number": HeaderFieldRule(label="Invoice No."),
            "invoice_date": HeaderFieldRule(label="Date of Issue", date_format="%m/%d/%Y"),
        },
    )
    repaired = _verify_and_repair_rules(model, TARGET, [PAGE])
    assert "invoice_date" in repaired
    _raw, value = find_header_value("invoice_date", model.header_fields["invoice_date"], [PAGE])
    assert value == date(2024, 2, 1)


def test_unrepairable_optional_rule_is_dropped():
    target = {
        "header": {"invoice_number": "708205451", "ban": "NOT-ON-PAGE"},
        "totals": {},
    }
    model = _model(
        header_fields={
            "invoice_number": HeaderFieldRule(label="Invoice No."),
            "ban": HeaderFieldRule(label="Invoice No.", required=False),  # wrong + optional
        },
    )
    repaired = _verify_and_repair_rules(model, target, [PAGE])
    assert "ban (dropped)" in repaired
    assert "ban" not in model.header_fields


def test_wrong_required_currency_rule_is_replaced_with_literal():
    target = {
        **TARGET,
        "header": {**TARGET["header"], "currency": "USD"},
    }
    model = _model(
        header_fields={
            "invoice_number": HeaderFieldRule(label="Invoice No."),
            "currency": HeaderFieldRule(label="Total Amount Due", required=True),
        },
    )
    repaired = _verify_and_repair_rules(model, target, [PAGE])
    assert "currency" in repaired
    assert model.header_fields["currency"].literal == "USD"
    assert model.header_fields["currency"].required is False

    _raw, value = find_header_value("currency", model.header_fields["currency"], [PAGE])
    assert value == "USD"
