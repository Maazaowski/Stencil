"""Unit tests for the unified field value expression (extract/sum/subtract/product)."""

from decimal import Decimal

from stencil.models.interpreter import _evaluate_value_expr
from stencil.models.schema import (
    ExtractionModel,
    FieldTransform,
    ItemFieldRule,
    ValueExpr,
    ValueOperand,
)


def _model():
    return ExtractionModel(model_id="m", supplier_profile_id="m", supplier="S")


def _eval(op, operands, current=None, totals=None, transform_type="decimal"):
    rule = ItemFieldRule(
        name="x",
        value=ValueExpr(op=op, operands=[ValueOperand(**o) for o in operands]),
        transform=FieldTransform(type=transform_type),
    )
    _raw, value = _evaluate_value_expr(rule, [], _model(), None, current or {}, totals or {})
    return value


def test_extract_returns_first_present_operand():
    assert _eval("extract", [{"kind": "const", "const": None}, {"kind": "const", "const": "7"}]) == Decimal("7")


def test_sum_treats_missing_operand_as_zero():
    assert _eval("sum", [{"kind": "const", "const": "5"}, {"kind": "const", "const": None}]) == Decimal("5")


def test_sum_all_missing_is_none():
    assert _eval("sum", [{"kind": "const", "const": None}]) is None


def test_subtract_is_first_minus_rest():
    # amount = Gross - Discount
    assert _eval("subtract", [{"kind": "const", "const": "336.00"},
                              {"kind": "const", "const": "43.44"}]) == Decimal("292.56")


def test_product_multiplies_operands():
    # tax = amount x rate
    assert _eval("product", [{"kind": "ref", "ref": "amount"}, {"kind": "const", "const": "0.2"}],
                 current={"amount": Decimal("100")}) == Decimal("20.0")


def test_product_with_missing_operand_is_none():
    assert _eval("product", [{"kind": "ref", "ref": "amount"}, {"kind": "const", "const": None}],
                 current={"amount": Decimal("100")}) is None


def test_ref_resolves_from_document_totals():
    # tax = total - subtotal, both document totals
    assert _eval("subtract", [{"kind": "ref", "ref": "total"}, {"kind": "ref", "ref": "subtotal"}],
                 totals={"total": Decimal("120"), "subtotal": Decimal("100")}) == Decimal("20")


def test_ref_prefers_line_field_over_total():
    assert _eval("extract", [{"kind": "ref", "ref": "amount"}],
                 current={"amount": Decimal("9")}, totals={"amount": Decimal("999")}) == Decimal("9")
