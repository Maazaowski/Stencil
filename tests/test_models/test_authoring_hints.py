"""Tests for profile-driven authoring hint reconciliation."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stencil.extraction.layout import extract_layout_document, render_layout_text
from stencil.models.authoring import (
    _apply_profile_authoring_hints,
    _build_target,
    _ensure_tax_rate_for_net_lines,
    _reconcile_currency_header,
    build_model_from_rules,
)
from stencil.models.interpreter import (
    _convert_header_value,
    _find_value_by_label,
)
from stencil.models.schema import ExtractionModel, HeaderFieldRule
from stencil.output.xlsx_writer import build_output_rows
from stencil.profiles.schema import SupplierProfile
from stencil.validation.schema import (
    CanonicalInvoice,
    ChargeType,
    ExtractionMetadata,
    ExtractionPath,
    InvoiceHeader,
    LineItem,
    OutputType,
)
from tests.corpus_utils import load_corpus_profile as _corpus_profile

EUNETWORKS_PDF = Path(
    r"C:\Stencil\Sample Data\EUNetworks\A837737\pdf\A837737 - IE-SI53564.pdf"
)


@pytest.fixture(scope="module")
def eunetworks_context():
    if not EUNETWORKS_PDF.exists():
        pytest.skip("euNetworks sample PDF not available")
    profile = _corpus_profile("eunetworks.standard")
    document = extract_layout_document(EUNETWORKS_PDF)
    page_texts = render_layout_text(document)
    ai_invoice = CanonicalInvoice(
        intake_id="hint-test",
        output_type=OutputType.STANDARD,
        header=InvoiceHeader(
            supplier_name="euNetworks",
            invoice_number="IE-SI53564",
            invoice_date=date(2025, 12, 2),
            due_date=date(2026, 1, 1),
            account_number="A837737",
            currency="EUR",
        ),
        line_items=[
            LineItem(
                line_number=1,
                service_id="S00364341",
                billing_reference="C35332-338",
                description="Service",
                charge_type=ChargeType.RECURRING,
                amount=Decimal("850.00"),
            ),
        ],
        subtotal=Decimal("15275.00"),
        tax=Decimal("3513.25"),
        total_due=Decimal("18788.25"),
        tax_rate=Decimal("0.23"),
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )
    ai_invoice.fields["_tax_output_mode"] = "calculate"
    ai_invoice.fields["_tax_rate_source"] = "invoice_tax_rate"
    target = {
        "header": {
            "invoice_number": ai_invoice.header.invoice_number,
            "invoice_date": ai_invoice.header.invoice_date.isoformat(),
            "due_date": ai_invoice.header.due_date.isoformat(),
            "account_number": ai_invoice.header.account_number,
            "currency": ai_invoice.header.currency,
        },
        "totals": {
            "subtotal": str(ai_invoice.subtotal),
            "tax_rate": str(ai_invoice.tax_rate),
            "total_due": str(ai_invoice.total_due),
        },
        "line_items": [],
    }
    return profile, page_texts, target, ai_invoice, document


def _bad_ai_model(profile: SupplierProfile) -> ExtractionModel:
    """Simulate common AI mistakes that caused the user's Excel diffs."""
    raw = {
        "header_fields": [
            {
                "name": "invoice_number",
                "label": "Document No.",
                "value_position": "right",
                "value_pattern": r"(IE-[A-Z0-9]+)",
                "date_format": None,
                "ignore_percent": False,
                "occurrence": "first",
                "page": 1,
                "required": True,
            },
            {
                "name": "account_number",
                "label": "Document No.",
                "value_position": "right",
                "value_pattern": r"([A-Z]?\d{5,6})",
                "date_format": None,
                "ignore_percent": False,
                "occurrence": "first",
                "page": 1,
                "required": True,
            },
            {
                "name": "invoice_date",
                "label": "Invoice Date",
                "value_position": "right",
                "value_pattern": None,
                "date_format": "%d/%m/%Y",
                "ignore_percent": False,
                "occurrence": "first",
                "page": 1,
                "required": True,
            },
            {
                "name": "due_date",
                "label": "Invoice Date",
                "value_position": "right",
                "value_pattern": None,
                "date_format": "%d/%m/%Y",
                "ignore_percent": False,
                "occurrence": "first",
                "page": 1,
                "required": False,
            },
        ],
        "region": {"start_anchors": ["Service"], "end_anchors": ["Net Total"], "columns": []},
        "row_classifiers": [],
        "grouping": {"mode": "single_row"},
        "item_fields": [],
        "totals": [],
        "self_checks": [],
        "confidence": 0.5,
    }
    return build_model_from_rules(
        raw,
        profile=profile,
        fingerprint="test-fp",
        layout_family_key=None,
        intake_id="hint-test",
    )


class TestAuthoringHintReconciliation:
    def test_fixes_bad_header_rules_for_output_columns(self, eunetworks_context):
        profile, page_texts, target, ai_invoice, _document = eunetworks_context
        model = _bad_ai_model(profile)
        model = _apply_profile_authoring_hints(
            model, profile, page_texts=page_texts, target=target,
        )

        header = target["header"]
        for field_name in ("invoice_number", "account_number", "invoice_date", "due_date"):
            expected = header[field_name]
            rule = model.header_fields[field_name]
            raw = _find_value_by_label(rule, page_texts)
            actual = _convert_header_value(field_name, raw, rule)
            if "date" in field_name:
                assert actual == date.fromisoformat(str(expected))
            else:
                assert str(actual) == str(expected)

        assert "tax_rate" in model.totals
        from decimal import Decimal

        from stencil.models.interpreter import find_total_value

        _tax_raw, tax_rate = find_total_value("tax_rate", model.totals["tax_rate"], page_texts)
        assert tax_rate == Decimal("0.23")

        sample_rows = build_output_rows(ai_invoice)
        assert sample_rows[0][5] == "A837737"  # EXT_ACCOUNT
        assert sample_rows[0][3] == "01/01/2026"  # formula / due_date
        assert sample_rows[0][7] == 195.5  # EXT_TAX for 850 @ 23%


def test_colt_currency_without_label_uses_literal():
    profile = _corpus_profile("colt.standard")
    page_texts = [
        "\n".join([
            "LAYOUT PAGE 1 (595x842) id=p1",
            "[p1.r0 role=summary c0 text x=30-170] Total Amount Due | [p1.r1 c1 text x=700-790] 9,831.00 USD",
        ])
    ]
    model = ExtractionModel(
        model_id=profile.profile_id,
        supplier_profile_id=profile.profile_id,
        supplier=profile.identity.canonical_name,
        header_fields={
            "currency": HeaderFieldRule(label="Total Amount Due", required=True),
        },
    )
    target = {"header": {"currency": "USD"}, "totals": {}, "line_items": []}

    _reconcile_currency_header(model, profile, page_texts=page_texts, target=target)

    assert model.header_fields["currency"].literal == "USD"
    assert model.header_fields["currency"].required is False


def test_build_target_derives_tax_rate_from_totals_for_ext_tax():
    """Ground truth without explicit tax_rate must still carry the rate EXT_TAX uses."""
    invoice = CanonicalInvoice(
        intake_id="derive-rate",
        output_type=OutputType.STANDARD,
        header=InvoiceHeader(
            supplier_name="Colt",
            invoice_number="708205451",
            invoice_date=date(2024, 2, 1),
            currency="USD",
        ),
        line_items=[
            LineItem(
                line_number=1,
                service_id="336619691",
                description="Service",
                charge_type=ChargeType.RECURRING,
                amount=Decimal("3773.04"),
            ),
        ],
        subtotal=Decimal("7992.68"),
        tax=Decimal("1838.32"),
        total_due=Decimal("9831.00"),
        tax_rate=None,
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )
    invoice.fields["_tax_output_mode"] = "calculate"
    invoice.fields["_tax_rate_source"] = "invoice_tax_divided_by_subtotal"
    target = _build_target(invoice)
    assert target["totals"]["tax_rate"] == "0.23"

    rows = build_output_rows(invoice)
    assert rows[0][7] == 867.80  # EXT_TAX = 3773.04 × 0.23


def test_build_target_derives_tax_rate_from_consistent_line_tax_for_ext_tax():
    invoice = CanonicalInvoice(
        intake_id="derive-rate-lines",
        output_type=OutputType.STANDARD,
        header=InvoiceHeader(
            supplier_name="Colt",
            invoice_number="724247246",
            invoice_date=date(2025, 2, 1),
            currency="USD",
        ),
        line_items=[
            LineItem(
                line_number=1,
                service_id="343450796",
                description="Service",
                charge_type=ChargeType.RECURRING,
                amount=Decimal("3471.19"),
                tax_amount=Decimal("867.80"),
            ),
            LineItem(
                line_number=2,
                service_id="343450797",
                description="Service",
                charge_type=ChargeType.RECURRING,
                amount=Decimal("3052.08"),
                tax_amount=Decimal("763.02"),
            ),
        ],
        subtotal=None,
        tax=None,
        total_due=None,
        tax_rate=None,
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )

    target = _build_target(invoice)

    assert target["totals"]["tax_rate"] == "0.25"


def test_ensure_tax_rate_literal_when_net_tax_and_no_page_anchor():
    """Colt net-tax: model must get a literal rate so EXT_TAX is not blank."""
    profile = _corpus_profile("colt.standard")
    page_texts = [
        "\n".join([
            "LAYOUT PAGE 1 (595x842) id=p1",
            "[p1.r0 role=summary c0 text x=30-170] Total Amount Due | [p1.r1 c1 text x=700-790] 9,831.00 USD",
        ])
    ]
    target = {
        "header": {"currency": "USD"},
        "totals": {"tax_rate": "0.23"},
        "line_items": [{"charge_type": "recurring", "amount": "100.00", "tax_amount": None}],
    }
    model = ExtractionModel(
        model_id=profile.profile_id,
        supplier_profile_id=profile.profile_id,
        supplier=profile.identity.canonical_name,
        totals={},
    )
    _ensure_tax_rate_for_net_lines(model, profile, target=target, page_texts=page_texts)
    assert model.totals["tax_rate"].literal == "0.23"


def test_ensure_tax_rate_literal_even_when_ground_truth_has_per_line_tax():
    """Grouped models need invoice tax_rate even when AI carried EXT_TAX per row."""
    profile = _corpus_profile("colt.standard")
    page_texts = [
        "\n".join([
            "LAYOUT PAGE 1 (595x842) id=p1",
            "[p1.r0 role=summary c0 text x=30-170] Total Amount Due | [p1.r1 c1 text x=700-790] 9,831.00 USD",
        ])
    ]
    target = {
        "header": {"currency": "USD"},
        "totals": {"tax_rate": "0.23"},
        "line_items": [{"charge_type": "recurring", "amount": "100.00", "tax_amount": "23.00"}],
    }
    model = ExtractionModel(
        model_id=profile.profile_id,
        supplier_profile_id=profile.profile_id,
        supplier=profile.identity.canonical_name,
        totals={},
    )

    _ensure_tax_rate_for_net_lines(model, profile, target=target, page_texts=page_texts)

    assert model.totals["tax_rate"].literal == "0.23"
