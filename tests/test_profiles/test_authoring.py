"""Offline unit tests for AI profile authoring (no OpenAI calls)."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from stencil.fields.loader import default_field_schema
from stencil.profiles import authoring
from stencil.profiles.authoring import (
    InvoiceEvidence,
    author_profile,
    build_profile_authoring_schema,
    draft_to_supplier_profile_dict,
    supplier_profile_to_draft,
)
from stencil.profiles.schema import SupplierProfile
from stencil.specs.loader import default_output_spec


def _flat_draft() -> dict:
    return {
        "canonical_name": "Acme",
        "aliases": ["Acme Inc", "Acme Telecom"],
        "output_type": "standard",
        "notes": "Header is a label/value table on page 1.",
        "field_overrides": [{
            "field_path": "field.due_date",
            "label_hint": "Payment Due",
            "date_format": "%d/%m/%Y",
        }],
        "output_mapping_overrides": [],
        "detail_start_marker": "Service Charges",
        "detail_end_marker": None,
        "line_item_granularity": "per_charge_row",
        "service_id_preference": "first_identifier",
        "billing_reference_preference": "same_as_service_id",
        "service_id_column_label": "Service Number",
        "billing_reference_column_label": None,
        "amount_column_label": "Amount",
        "tax_amount_column_label": "Tax",
        "amount_source": "table_charges_column",
        "tax_source": "table_tax_column",
        "tax_output_mode": "extract_exact",
        "tax_rate_source": None,
        "service_id_value_pattern": None,
        "billing_reference_value_pattern": None,
        "detail_table_anchors": ["Service Charges"],
        "table_column_labels": ["Service", "Amount"],
        "subtotal_keywords": [],
        "tax_keywords": ["VAT"],
        "skip_row_keywords": ["Total Current Charges"],
        "include_zero_amount_line_items": False,
        "require_line_item_identifier": True,
    }


def test_schema_pins_scoped_fields_and_output_mapping_paths():
    fs = default_field_schema()
    spec = default_output_spec()
    schema = build_profile_authoring_schema(fs, spec)
    enum = schema["schema"]["properties"]["profile"]["properties"]["field_overrides"]["items"][
        "properties"
    ]["field_path"]["enum"]
    assert set(enum) == {
        f"{'field' if str(field.scope) == 'document' else 'row'}.{field.name}"
        for field in fs.fields
    }
    assert schema["strict"] is True
    assert schema["schema"]["properties"]["profile"]["properties"]["field_overrides"][
        "items"
    ]["properties"]["date_format"] == {"type": ["string", "null"]}
    assert schema["schema"]["properties"]["profile"]["properties"]["field_overrides"][
        "items"
    ]["properties"]["label_hint"] == {"type": ["string", "null"]}
    mapping = schema["schema"]["properties"]["profile"]["properties"][
        "output_mapping_overrides"]["items"]
    assert mapping["properties"]["output_header"]["enum"] == [
        column.header for column in spec.columns]
    assert "row.billing_period_start" in mapping["properties"]["source"]["enum"]
    assert "computed.line_tax" in mapping["properties"]["source"]["enum"]
    assert mapping["properties"]["fallback"]["type"] == ["string", "null"]
    assert mapping["properties"]["transforms"]["items"]["enum"] == [
        "digits_only", "trim", "uppercase", "lowercase",
    ]
    assert "uniqueItems" not in mapping["properties"]["transforms"]


def test_draft_removes_redundant_mapping_but_preserves_transform_override():
    fs = default_field_schema()
    spec = default_output_spec()
    inherited = spec.columns[5]
    draft = _flat_draft()
    draft["output_mapping_overrides"] = [{
        "output_header": inherited.header,
        "source": inherited.source,
        "fallback": inherited.fallback,
        "transforms": [],
    }]
    result = draft_to_supplier_profile_dict(
        {"profile": draft}, output_spec_id=spec.spec_id,
        field_schema_id=fs.schema_id, field_schema=fs, output_spec=spec,
    )
    assert result["output_mapping_overrides"] == []

    draft["output_mapping_overrides"][0]["transforms"] = ["digits_only"]
    result = draft_to_supplier_profile_dict(
        {"profile": draft}, output_spec_id=spec.spec_id,
        field_schema_id=fs.schema_id, field_schema=fs, output_spec=spec,
    )
    assert result["output_mapping_overrides"][0]["transforms"] == ["digits_only"]


def test_draft_maps_to_valid_supplier_profile():
    fs = default_field_schema()
    draft = _flat_draft()
    draft["output_mapping_overrides"] = [
        {
            "output_header": "EXT_DATE",
            "source": "row.billing_period_start",
            "fallback": "field.invoice_date",
            "transforms": [],
            "reason": "The blueprint uses each service period start.",
        },
        {
            "output_header": "formula",
            "source": "row.billing_period_end",
            "fallback": "field.due_date",
            "transforms": [],
            "reason": "The blueprint uses each service period end.",
        },
    ]
    profile_dict = draft_to_supplier_profile_dict(
        {"profile": draft},
        output_spec_id="temforce.standard",
        field_schema_id="invoice.standard",
        field_schema=fs,
        output_spec=default_output_spec(),
    )
    profile_dict["profile_id"] = "acme.standard.v1"
    profile = SupplierProfile.model_validate(profile_dict)

    assert profile.identity.canonical_name == "Acme"
    assert "Acme Telecom" in profile.identity.aliases
    assert profile.line_item_hints.line_item_granularity == "per_charge_row"
    assert profile.line_item_hints.service_id_column_label == "Service Number"
    assert profile.line_item_hints.amount_column_label == "Amount"
    assert profile.line_item_hints.tax_source == "table_tax_column"
    assert profile.line_item_hints.tax_output_mode == "extract_exact"
    assert profile.require_line_item_identifier is True
    due_date_override = next(item for item in profile.field_overrides if item.name == "due_date")
    assert due_date_override.date_format == "%d/%m/%Y"
    # Label override is applied as a label-only merge (scope/type from base schema).
    override = next(o for o in profile.field_overrides if o.name == "due_date")
    assert override.label_hint == "Payment Due"
    assert str(override.scope) == "document"
    assert [mapping.model_dump() for mapping in profile.output_mapping_overrides] == [
        {
            "output_header": "EXT_DATE",
            "source": "row.billing_period_start",
            "fallback": "field.invoice_date",
            "transforms": [],
        },
        {
            "output_header": "formula",
            "source": "row.billing_period_end",
            "fallback": "field.due_date",
            "transforms": [],
        },
    ]


def test_draft_rejects_unknown_or_duplicate_output_mappings():
    fs = default_field_schema()
    spec = default_output_spec()
    draft = _flat_draft()
    draft["output_mapping_overrides"] = [
        {
            "output_header": "EXT_DATE",
            "source": "row.billing_period_start",
            "fallback": None,
        },
        {
            "output_header": "EXT_DATE",
            "source": "row.billing_period_end",
            "fallback": None,
        },
    ]
    with pytest.raises(ValueError, match="duplicate output mapping"):
        draft_to_supplier_profile_dict(
            {"profile": draft},
            output_spec_id=spec.spec_id,
            field_schema_id=fs.schema_id,
            field_schema=fs,
            output_spec=spec,
        )

    draft["output_mapping_overrides"] = [{
        "output_header": "EXT_DATE",
        "source": "row.not_a_field",
        "fallback": None,
    }]
    with pytest.raises(ValueError, match="unknown output mapping source"):
        draft_to_supplier_profile_dict(
            {"profile": draft},
            output_spec_id=spec.spec_id,
            field_schema_id=fs.schema_id,
            field_schema=fs,
            output_spec=spec,
        )


def test_date_only_override_survives_profile_to_authoring_draft():
    fs = default_field_schema()
    draft = _flat_draft()
    draft["field_overrides"] = [{
        "field_path": "field.invoice_date",
        "label_hint": None,
        "date_format": "%d/%m/%Y",
    }]
    profile_dict = draft_to_supplier_profile_dict(
        {"profile": draft},
        output_spec_id="temforce.standard",
        field_schema_id="invoice.standard",
        field_schema=fs,
    )
    profile_dict["profile_id"] = "acme.date-only.v1"

    restored = supplier_profile_to_draft(SupplierProfile.model_validate(profile_dict))

    assert restored["profile"]["field_overrides"] == [{
        "field_path": "field.invoice_date",
        "label_hint": None,
        "date_format": "%d/%m/%Y",
    }]


def test_author_profile_with_stubbed_openai(monkeypatch):
    captured = {}

    def create(**kwargs):
        captured["schema_name"] = kwargs["response_format"]["json_schema"]["name"]
        captured["messages"] = kwargs["messages"]
        payload = {
            "assistant_message": "Mapped due date and set per-charge grouping.",
            "open_questions": ["Is VAT itemized per line?"],
            "confidence": 0.82,
            "profile": _flat_draft(),
        }
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=40),
        )

    client = MagicMock()
    client.chat.completions.create.side_effect = create
    monkeypatch.setattr(authoring, "get_ai_client", lambda *a, **k: client)

    evidence = [
        InvoiceEvidence(
            label="acme_jan.pdf",
            page_texts=["[p1] Invoice ... Service Charges ..."],
            target={
                "header": {"invoice_number": "INV1"},
                "totals": {"subtotal": "100.00", "tax": "8.25"},
                "line_items": [{"amount": "100.00", "tax_amount": "8.25"}],
            },
            blueprint_context={
                "filename": "acme_jan.xls",
                "sheet_name": "Invoice Data",
                "raw_headers": ["EXT_SERVICEID", "EXT_AMOUNT", "EXT_TAX"],
                "aligned_rows": [["SVC1", "", "", "", "100.00", "", "", "8.25"]],
                "row_count": 1,
                "sample_rows": [["SVC1", "", "", "", "100.00", "", "", "8.25"]],
                "totals": {"amount": "100.00", "tax": "8.25"},
                "warnings": [],
            },
            diff_feedback="exact match",
        )
    ]
    result = author_profile(
        field_schema=default_field_schema(),
        output_spec=default_output_spec(),
        invoices=evidence,
        conversation=[{"role": "user", "content": "Service ID is the leftmost number, one row per charge."}],
        current_draft=None,
    )

    assert captured["schema_name"] == "supplier_profile_draft"
    assert result.assistant_message.startswith("Mapped")
    assert result.open_questions == ["Is VAT itemized per line?"]
    assert result.confidence == 0.82
    assert result.tokens_input == 100 and result.tokens_output == 40
    assert result.draft["profile"]["line_item_granularity"] == "per_charge_row"
    system_prompt = captured["messages"][0]["content"]
    user_prompt = captured["messages"][1]["content"]
    assert "source" in system_prompt and "truth" in system_prompt
    assert "Use the blueprint to learn what" in system_prompt
    assert "PDF as evidence for printed facts and totals" in system_prompt
    assert "Never relabel field.invoice_date" in system_prompt
    assert "COMPLETE desired output_mapping_overrides" in system_prompt
    assert "Explain every meaningful difference" in system_prompt
    assert "Build an internal extraction plan" in system_prompt
    assert "service ID source" in system_prompt
    assert "reconciliation targets" in system_prompt
    assert "Use the available extraction toolkit" in system_prompt
    assert "treat matching it exactly as the goal" not in system_prompt
    assert "blueprint_context" in user_prompt
    assert "baseline_extracted_evidence" in user_prompt
    assert "allowed_output_sources" in user_prompt
    assert "blueprint_vs_invoice_summary" in user_prompt
    assert "preview_vs_blueprint_diff" in user_prompt


def test_schema_allows_blueprint_amount_and_tax_hints():
    fs = default_field_schema()
    profile_props = build_profile_authoring_schema(
        fs, default_output_spec())["schema"]["properties"]["profile"]["properties"]

    assert profile_props["amount_source"]["enum"] == [
        "group_total", "first_amount", "label_amount", "label_amount_minus_tax", "table_charges_column", None]
    assert profile_props["tax_source"]["enum"] == [
        "none", "label_amount", "group_total_minus_amount", "table_tax_column", None]
    assert profile_props["tax_output_mode"]["enum"] == ["auto", "extract_exact", "calculate", "none", None]
    assert "service_id_value_pattern" in profile_props
    assert "billing_reference_value_pattern" in profile_props


def test_blueprint_summary_warns_when_xlsx_values_do_not_match_pdf_truth():
    summary = authoring._blueprint_vs_invoice_summary(
        {
            "aligned_headers": ["EXT_SERVICEID", "EXT_DATE", "EXT_AMOUNT", "EXT_INVOICENUMBER", "EXT_TAX"],
            "aligned_rows": [["441101218", "07/01/2026", "608.48", "792254753", "406.23"]],
            "row_count": 1,
            "totals": {"amount": "608.48", "tax": "406.23"},
        },
        {
            "header": {"invoice_number": "788263904", "invoice_date": "2026-06-01"},
            "totals": {"subtotal": "25551.43", "tax": "3368.85"},
            "line_items": [],
        },
    )

    assert summary is not None
    assert summary["warnings"] == [
        "Blueprint amount total differs from the printed invoice subtotal; "
        "explain the delivery semantics or reconcile the difference.",
        "Blueprint tax total differs from the printed invoice tax total; "
        "explain the delivery semantics or reconcile the difference.",
        "Blueprint invoice number differs from the baseline header extraction; verify the intended output mapping.",
        "Blueprint date differs from the baseline invoice date; check billing-period "
        "or row-date fields before choosing an output mapping.",
    ]


def test_blueprint_summary_identifies_row_billing_period_as_date_mapping_evidence():
    summary = authoring._blueprint_vs_invoice_summary(
        {
            "aligned_headers": ["EXT_DATE", "formula"],
            "aligned_rows": [["07/01/2026", "07/31/2026"]],
            "row_count": 1,
        },
        {
            "header": {"invoice_date": "2026-08-03", "due_date": "2026-08-31"},
            "totals": {},
            "line_items": [{
                "billing_period_start": "2026-07-01",
                "billing_period_end": "2026-07-31",
            }],
        },
        output_spec=default_output_spec(),
        field_schema=default_field_schema(),
    )

    evidence = {item["output_header"]: item for item in summary["output_mapping_evidence"]}
    assert evidence["EXT_DATE"]["matching_alternative_sources"] == ["row.billing_period_start"]
    assert evidence["formula"]["matching_alternative_sources"] == ["row.billing_period_end"]
