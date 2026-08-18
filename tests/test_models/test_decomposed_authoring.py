"""Decomposed authoring: focused section calls, grouped layouts skip line-items."""

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from stencil.models import authoring
from stencil.profiles.schema import (
    AdvancedHints,
    ClassificationSignals,
    LineItemHints,
    SupplierIdentity,
    SupplierProfile,
)
from stencil.validation.schema import (
    CanonicalInvoice,
    ExtractionMetadata,
    ExtractionPath,
    InvoiceHeader,
    LineItem,
    OutputType,
)


def _invoice() -> CanonicalInvoice:
    return CanonicalInvoice(
        intake_id="t", output_type=OutputType.STANDARD,
        header=InvoiceHeader(supplier_name="X", invoice_number="INV1",
                             invoice_date=date(2026, 1, 1), account_number="A1"),
        line_items=[LineItem(line_number=1, service_id="S1", billing_reference="S1",
                             description="svc", charge_type="recurring", amount=Decimal("10.00"))],
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )


def _profile(grouped: bool) -> SupplierProfile:
    hints = (LineItemHints(line_item_granularity="per_charge_row", service_id_preference="first_identifier")
             if grouped else LineItemHints())
    return SupplierProfile(
        profile_id="x.standard.v1", identity=SupplierIdentity(canonical_name="X"),
        classification=ClassificationSignals(output_type="standard"),
        advanced=AdvancedHints(line_item_hints=hints),
    )


def _resp(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _patch_openai(monkeypatch) -> list[str]:
    calls: list[str] = []

    def create(**kwargs):
        name = kwargs["response_format"]["json_schema"]["name"]
        calls.append(name)
        if "scalars" in name:
            return _resp(json.dumps({"header_fields": [], "totals": [], "self_checks": [],
                                     "confidence": 0.9, "notes": None}))
        return _resp(json.dumps({
            "region": {"start_anchors": [], "end_anchors": [], "end_scope": "document", "columns": []},
            "row_classifiers": [],
            "grouping": {"mode": "single_row", "item_role": None, "start_role": None,
                         "end_role": None, "include_end_row": False,
                         "emit": "one_item_per_group", "emit_role": None},
            "item_fields": [], "confidence": 0.9, "notes": None}))

    client = MagicMock()
    client.chat.completions.create.side_effect = create
    monkeypatch.setattr(authoring, "get_ai_client", lambda *a, **k: client)
    return calls


def _author(profile: SupplierProfile):
    return authoring.author_extraction_model(
        page_texts=["LAYOUT PAGE 1"],
        layout_evidence={"layout": {}, "target_alignment": {"header": {}, "totals": {}, "line_items": []}},
        ai_invoice=_invoice(), profile=profile, fingerprint="sha256:fp",
        layout_family_key=None, intake_id="t",
    )


def test_grouped_layout_skips_line_item_call(monkeypatch):
    calls = _patch_openai(monkeypatch)
    result = _author(_profile(grouped=True))
    assert calls == ["extraction_rules_scalars"]  # one call; line-item recipe skipped
    assert result.tokens_input == 10
    assert result.model is not None


def test_non_grouped_layout_makes_two_section_calls(monkeypatch):
    calls = _patch_openai(monkeypatch)
    result = _author(_profile(grouped=False))
    assert calls == ["extraction_rules_scalars", "extraction_rules_line_items"]
    assert result.tokens_input == 20  # two calls x 10


def test_section_schemas_are_restricted_and_strict():
    from stencil.fields.loader import default_field_schema

    fs = default_field_schema()
    scalars = authoring._section_schema(fs, "scalars")["schema"]
    line = authoring._section_schema(fs, "line_items")["schema"]
    assert set(scalars["properties"]) == {"header_fields", "totals", "self_checks", "confidence", "notes"}
    assert set(line["properties"]) == {"region", "row_classifiers", "grouping", "item_fields", "confidence", "notes"}
    assert scalars["additionalProperties"] is False
    assert set(scalars["required"]) == set(scalars["properties"])
    assert set(line["required"]) == set(line["properties"])


def test_slice_evidence_drops_layout_summary():
    ev = {
        "layout": {"big": "x" * 1000},
        "target_alignment": {"header": {"h": 1}, "totals": {"t": 2}, "line_items": [1, 2]},
    }
    scal = authoring._slice_evidence(ev, "scalars")
    line = authoring._slice_evidence(ev, "line_items")
    assert "layout" not in scal and "layout" not in line  # bulky summary dropped
    assert set(scal["target_alignment"]) == {"header", "totals"}
    assert set(line["target_alignment"]) == {"line_items"}
