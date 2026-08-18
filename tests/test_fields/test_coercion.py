"""Tests for field value coercion and date parsing."""

from datetime import date
from decimal import Decimal

from stencil.fields.coercion import coerce_field_value, parse_date_value
from stencil.fields.currency import normalize_currency_code
from stencil.fields.schema import FieldDef, FieldScope, FieldType
from stencil.validation.schema import (
    SYNTHETIC_INVOICE_DATE_WARNING,
    CanonicalInvoice,
    ExtractionPath,
    LineItem,
)


class TestParseDateValue:
    def test_iso_string(self):
        assert parse_date_value("2024-10-01") == date(2024, 10, 1)

    def test_us_deliverable_string(self):
        assert parse_date_value("10/01/2024") == date(2024, 10, 1)

    def test_dotted_month_abbreviation(self):
        assert parse_date_value("Jun. 30, 2026") == date(2026, 6, 30)
        assert parse_date_value("Payment Due Date Jun. 30, 2026") == date(2026, 6, 30)

    def test_junk_returns_none(self):
        assert parse_date_value(".") is None
        assert parse_date_value("") is None
        assert parse_date_value(None) is None

    def test_date_object_passthrough(self):
        d = date(2024, 10, 1)
        assert parse_date_value(d) is d


class TestCoerceFieldValueDate:
    def test_us_date_coerced(self):
        field = FieldDef(
            name="invoice_date",
            scope=FieldScope.DOCUMENT,
            type=FieldType.DATE,
            required=True,
        )
        assert coerce_field_value(field, "10/01/2024") == date(2024, 10, 1)

    def test_date_format_hint_coerces_day_first_supplier_dates(self):
        field = FieldDef(
            name="invoice_date",
            scope=FieldScope.DOCUMENT,
            type=FieldType.DATE,
            date_format="%d/%m/%Y",
            required=True,
        )
        assert coerce_field_value(field, "05/01/2026") == date(2026, 1, 5)


class TestCurrencyNormalization:
    def test_builtin_currency_aliases(self):
        assert normalize_currency_code("Rupees").code == "INR"
        assert normalize_currency_code("Rs").code == "INR"
        assert normalize_currency_code("₹").code == "INR"
        assert normalize_currency_code("usd").code == "USD"
        assert normalize_currency_code("US Dollars").code == "USD"
        assert normalize_currency_code("$").code == "USD"

    def test_profile_single_allowed_currency_is_fallback(self):
        rules = {"allowed_codes": ["INR"], "aliases": {}, "default_code": None}
        assert normalize_currency_code("mystery money", rules=rules).code == "INR"
        assert normalize_currency_code(None, rules=rules).code == "INR"

    def test_profile_multiple_allowed_without_default_drops_unknown(self):
        rules = {"allowed_codes": ["USD", "EUR"], "aliases": {}, "default_code": None}
        result = normalize_currency_code("mystery money", rules=rules)
        assert result.code is None
        assert "dropped" in (result.warning or "")

    def test_profile_alias_overrides_builtin_alias(self):
        rules = {"allowed_codes": ["CAD"], "aliases": {"$": "CAD"}, "default_code": None}
        assert normalize_currency_code("$", rules=rules).code == "CAD"

    def test_field_coercion_uses_currency_rules(self):
        field = FieldDef(name="currency", scope=FieldScope.ROW, type=FieldType.STRING)
        rules = {"allowed_codes": ["INR"], "aliases": {"Rupees": "INR"}}
        assert coerce_field_value(field, "Rupees", currency_rules=rules) == "INR"

    def test_line_item_schema_normalizes_long_currency_words(self):
        item = LineItem(
            line_number=1,
            description="svc",
            amount=Decimal("10"),
            currency="Rupees",
        )
        assert item.currency == "INR"


class TestExtractedDocumentDateReload:
    def test_us_dates_in_fields_reload_for_header(self):
        doc = CanonicalInvoice.model_validate({
            "intake_id": "x",
            "fields": {
                "supplier_name": "Colt",
                "invoice_number": "708205451",
                "invoice_date": "10/01/2024",
                "due_date": "10/31/2024",
                "output_type": "standard",
            },
            "rows": [],
            "metadata": {"extraction_path": ExtractionPath.AI},
        })
        assert doc.header.invoice_date == date(2024, 10, 1)
        assert doc.header.due_date == date(2024, 10, 31)

    def test_prepared_evidence_does_not_crash_on_us_dates(self):
        from stencil.extraction.evidence import build_model_authoring_evidence
        from stencil.extraction.layout import LayoutDocument

        doc = CanonicalInvoice.model_validate({
            "intake_id": "x",
            "fields": {
                "supplier_name": "Colt",
                "invoice_number": "708205451",
                "invoice_date": "10/01/2024",
                "output_type": "standard",
            },
            "rows": [],
            "metadata": {"extraction_path": ExtractionPath.AI},
        })
        evidence = build_model_authoring_evidence(LayoutDocument(pages=[]), doc)
        assert evidence["target_alignment"]["header"] is not None

    def test_null_invoice_date_falls_back_without_crashing(self):
        doc = CanonicalInvoice.model_validate({
            "intake_id": "x",
            "fields": {
                "supplier_name": "Granite",
                "invoice_number": "238526639",
                "invoice_date": None,
                "due_date": "03/01/2026",
                "output_type": "standard",
            },
            "rows": [],
            "metadata": {"extraction_path": ExtractionPath.AI},
        })

        assert doc.header.invoice_date == date(2026, 3, 1)
        assert "invoice_date" not in doc.fields
        assert SYNTHETIC_INVOICE_DATE_WARNING in doc.warnings
