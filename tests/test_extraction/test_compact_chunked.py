import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from stencil import runtime_settings
from stencil.config import settings
from stencil.extraction import extractor
from stencil.extraction.compact_chunked import (
    CandidateRow,
    LayoutChunk,
    LayoutRowLine,
    _apply_candidate_defaults,
    _apply_row_context_rules,
    _build_chunk_prompt,
    _build_scalar_prompt,
    _completeness_warnings,
    _detail_region_rows,
    _extract_chunk_rows,
    _line_item_defaults_from_context,
    _recover_missing_total_candidate_rows,
    _reject_label_candidates,
    _total_candidate_reconciliation_warnings,
    build_compact_line_item_schema,
    build_compact_scalar_schema,
    chunk_layout_rows,
    compact_to_legacy_raw,
    detect_candidate_rows,
    merge_compact_rows,
)
from stencil.fields.loader import default_field_schema, merge_field_schema
from stencil.fields.schema import FieldDef, FieldScope, FieldType
from stencil.output.spec import OutputColumn, OutputSpec
from stencil.output.xlsx_writer import build_output_rows
from stencil.profiles.schema import ExtractionPlan, RowContextRule
from stencil.validation.document_io import build_extracted_from_ai_raw
from stencil.validation.schema import SYNTHETIC_INVOICE_DATE_WARNING, ExtractionMetadata, ExtractionPath


class _FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=_FakeCompletions(responses))


def _response(content: str, finish_reason: str = "stop", prompt_tokens: int = 10, completion_tokens: int = 5):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, refusal=None),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def _metadata():
    return ExtractionMetadata(extraction_path=ExtractionPath.AI, ai_model_name="test-model")


def _granite_profile_for_candidates():
    return {
        "identity": {"canonical_name": "Granite"},
        "classification": {"output_type": "standard"},
        "advanced": {
            "line_item_hints": {
                "service_id_column_label": "ACCOUNT",
                "billing_reference_column_label": "NAME",
                "amount_column_label": "CHARGES",
                "tax_amount_column_label": "TAX / SURCHRG",
                "amount_source": "ai_table_column",
                "tax_source": "ai_table_column",
                "tax_output_mode": "extract_exact",
                "table_column_labels": [
                    "ACCOUNT",
                    "NAME",
                    "CITY / STATE",
                    "USAGE",
                    "TAX / SURCHRG",
                    "CHARGES",
                    "ADJUST",
                    "SUB-TOT",
                ],
            }
        },
    }


def _rogers_profile_for_marker_candidates(include_pattern: bool = True):
    hints = {
        "line_item_granularity": "per_total_group",
        "service_id_column_label": "Wireless, Phone",
        "amount_column_label": "Total before taxes",
        "tax_amount_column_label": "Total for Wireless/Phone-Total before taxes",
        "amount_source": "label_amount",
        "tax_source": "group_total_minus_amount",
        "tax_output_mode": "extract_exact",
        "include_zero_amount_line_items": False,
    }
    if include_pattern:
        hints["service_id_value_pattern"] = r"PDFSPLITSTART-[^_]+_(.+?)-PDFSPLITEND"
    return {
        "identity": {"canonical_name": "Rogers"},
        "classification": {"output_type": "standard"},
        "advanced": {
            "document_structure": {
                "detail_start_marker": "PDFSPLITSTART",
                "detail_end_marker": "Total for Wireless,Total for Phone",
            },
            "line_item_hints": hints,
            "include_zero_amount_line_items": False,
        },
    }


def _orange_profile_for_labeled_service_candidates():
    return {
        "identity": {"canonical_name": "Orange"},
        "classification": {"output_type": "standard"},
        "advanced": {
            "document_structure": {
                "detail_start_marker": "Breakdown Of Charges",
                "detail_end_marker": "TOTAL Internet Essential, TOTAL Packaged Services",
            },
            "line_item_hints": {
                "line_item_granularity": "per_service_group",
                "service_id_column_label": "Installed Offer ID",
                "billing_reference_column_label": "Description",
                "tax_amount_column_label": "Tax %",
                "tax_output_mode": "calculate",
                "tax_rate_source": "consistent_line_tax_rate",
                "table_column_labels": [
                    "Description",
                    "Period",
                    "Quantity",
                    "Monthly",
                    "Disc",
                    "Amount in",
                    "Exchange Rate %",
                    "Amount",
                ],
            },
            "include_zero_amount_line_items": True,
            "require_line_item_identifier": False,
        },
    }


def _att_column_profile_for_candidates():
    return {
        "identity": {"canonical_name": "AT&T"},
        "classification": {"output_type": "standard"},
        "advanced": {
            "line_item_hints": {
                "line_item_granularity": None,
                "service_id_preference": "child_identifier",
                "billing_reference_preference": "separate_column",
                "service_id_column_label": "Circuit #",
                "billing_reference_column_label": "AT&T VPN Service",
                "amount_column_label": "Total AT&T VPN Service",
                "amount_source": "label_amount",
                "service_id_value_pattern": r"(?i)Circuit #:\s*([A-Z0-9.]+)",
                "billing_reference_value_pattern": r"(?i)(AT&T VPN Service)",
            },
            "include_zero_amount_line_items": False,
        },
    }


def _lumen_profile_for_total_row_candidates():
    return {
        "identity": {"canonical_name": "Lumen"},
        "classification": {"output_type": "standard"},
        "advanced": {
            "line_item_hints": {
                "line_item_granularity": "per_total_group",
                "service_id_preference": "total_row_identifier",
                "billing_reference_preference": "same_as_service_id",
                "amount_source": "table_charges_column",
                "tax_source": "table_tax_column",
                "tax_output_mode": "extract_exact",
            }
        },
    }


def test_compact_line_schema_omits_verbose_unused_row_fields():
    schema = build_compact_line_item_schema({"service_id", "billing_reference", "amount", "tax_amount"})

    assert set(schema["schema"]["properties"]) == {"rows"}
    assert schema["schema"]["required"] == ["rows"]
    row_props = schema["schema"]["properties"]["rows"]["items"]["properties"]
    assert set(row_props) == {"r", "s", "b", "c", "a", "x", "p"}
    assert "description" not in row_props
    assert row_props["c"]["enum"] == [
        "recurring",
        "one_time",
        "tax",
        "fee",
        "credit",
        "adjustment",
        "surcharge",
        "usage",
        "unknown",
        None,
    ]


def test_compact_scalar_schema_does_not_request_model_confidence():
    schema = build_compact_scalar_schema({"invoice_number", "current_charges"})

    assert set(schema["schema"]["properties"]) == {"h"}
    assert schema["schema"]["required"] == ["h"]
    assert set(schema["schema"]["properties"]["h"]["properties"]) == {"in", "cc"}


def test_compact_payload_expands_to_canonical_invoice():
    raw = compact_to_legacy_raw(
        {
            "h": {
                "sn": "Granite",
                "in": "238526639",
                "id": "2026-03-01",
                "dd": "2026-03-01",
                "ac": "08918872",
                "cu": "USD",
                "cc": 813.15,
                "tx": 89.73,
            },
            "rows": [
                {"r": "p2.r10", "s": "08918899", "b": "Branch A", "a": 104.36, "x": 12.53, "p": 2},
                {"r": "p2.r11", "s": "08918901", "b": None, "a": 100.36, "x": None, "p": 2},
            ],
            "oc": 813.15,
        },
        supplier_name="Granite",
    )

    doc = build_extracted_from_ai_raw(raw, intake_id="i1", metadata=_metadata())

    assert doc.header.supplier_name == "Granite"
    assert doc.header.invoice_number == "238526639"
    assert doc.current_charges == Decimal("813.15")
    assert doc.metadata.overall_confidence == 1.0
    assert [item.line_number for item in doc.line_items] == [1, 2]
    assert doc.line_items[0].service_id == "08918899"
    assert doc.line_items[0].billing_reference == "Branch A"
    assert doc.line_items[0].amount == Decimal("104.36")
    assert doc.line_items[0].tax_amount == Decimal("12.53")
    assert doc.line_items[0].description == ""
    assert doc.line_items[0].charge_type == "unknown"


def test_compact_currency_uses_profile_rules_for_header_and_rows():
    rules = {
        "default_code": "INR",
        "allowed_codes": ["INR"],
        "aliases": {"Rupees": "INR"},
    }
    raw = compact_to_legacy_raw(
        {
            "h": {"sn": "Airtel", "in": "INV-1", "cu": "Rupees"},
            "rows": [
                {"r": "p2.r1", "s": "CIR-1", "a": 100, "cu": "Rupees"},
                {"r": "p2.r2", "s": "CIR-2", "a": 200},
            ],
        },
        supplier_name="Airtel",
        currency_rules=rules,
    )

    doc = build_extracted_from_ai_raw(
        raw,
        intake_id="airtel",
        metadata=_metadata(),
        currency_rules=rules,
    )

    assert raw["header"]["currency"] == "INR"
    assert [row["currency"] for row in raw["line_items"]] == ["INR", "INR"]
    assert doc.header.currency == "INR"
    assert [item.currency for item in doc.line_items] == ["INR", "INR"]


def test_line_item_defaults_reuse_grouped_row_labels_from_scalar_context():
    profile = {
        "advanced": {
            "line_item_hints": {
                "service_id_column_label": "Circuit ID",
                "billing_reference_column_label": "Relationship No, Relationship number",
            }
        }
    }
    rows = [
        LayoutRowLine(
            page=2,
            row_id="p2.r0",
            text=(
                "p2.r0 role=detail [p2.r0.c0 c0 detail x=12-30] Circuit ID | "
                "[p2.r0.c1 c1 detail x=40-90] : TMPA1_988099"
            ),
        ),
        LayoutRowLine(
            page=2,
            row_id="p2.r2",
            text=(
                "p2.r2 role=detail [p2.r2.c0 c0 detail x=12-38] Relationship No | "
                "[p2.r2.c1 c1 detail x=40-90] : 7038596705"
            ),
        ),
    ]

    defaults = _line_item_defaults_from_context(rows, profile, default_field_schema())

    assert defaults == {
        "service_id": "TMPA1_988099",
        "billing_reference": "7038596705",
    }


def test_line_item_defaults_fall_back_to_output_field_label_hints():
    schema = merge_field_schema(
        default_field_schema(),
        [
            FieldDef(name="service_id", scope=FieldScope.ROW, type=FieldType.STRING, label_hint="Circuit ID"),
            FieldDef(
                name="billing_reference",
                scope=FieldScope.ROW,
                type=FieldType.STRING,
                label_hint="Relationship number",
            ),
        ],
    )
    rows = [
        LayoutRowLine(page=2, row_id="p2.r0", text="Circuit ID : TMPA1_988099"),
        LayoutRowLine(page=2, row_id="p2.r1", text="Relationship number : 7038596705"),
    ]

    defaults = _line_item_defaults_from_context(rows, {"advanced": {"line_item_hints": {}}}, schema)

    assert defaults == {
        "service_id": "TMPA1_988099",
        "billing_reference": "7038596705",
    }


def test_line_item_defaults_ignore_rows_inside_detail_region():
    profile = {
        "advanced": {
            "line_item_hints": {
                "service_id_column_label": "Circuit ID",
            }
        }
    }
    rows = [
        LayoutRowLine(page=2, row_id="p2.r0", text="Circuit ID : HEADER-SERVICE"),
        LayoutRowLine(page=2, row_id="p2.r8", text="Circuit ID : DETAIL-SERVICE 12000.00"),
    ]

    defaults = _line_item_defaults_from_context(
        rows,
        profile,
        default_field_schema(),
        excluded_row_ids={"p2.r8"},
    )

    assert defaults == {"service_id": "HEADER-SERVICE"}


def test_compact_payload_line_item_defaults_fill_missing_row_identifiers():
    raw = compact_to_legacy_raw(
        {
            "h": {"sn": "Airtel", "in": "INV-1", "cu": "INR"},
            "line_item_defaults": {
                "service_id": "TMPA1_988099",
                "billing_reference": "7038596705",
            },
            "rows": [{"r": "p2.r14", "a": 14160, "x": 2160, "p": 2}],
        },
        supplier_name="Airtel",
    )

    doc = build_extracted_from_ai_raw(raw, intake_id="airtel-defaults", metadata=_metadata())

    assert doc.line_items[0].service_id == "TMPA1_988099"
    assert doc.line_items[0].billing_reference == "7038596705"


def test_compact_payload_line_item_defaults_do_not_overwrite_row_values():
    raw = compact_to_legacy_raw(
        {
            "h": {"sn": "Supplier", "in": "INV-1", "cu": "USD"},
            "line_item_defaults": {
                "service_id": "HEADER-SERVICE",
                "billing_reference": "HEADER-REF",
            },
            "rows": [{"r": "p2.r14", "s": "ROW-SERVICE", "b": "ROW-REF", "a": 10, "p": 2}],
        },
        supplier_name="Supplier",
    )

    doc = build_extracted_from_ai_raw(raw, intake_id="row-wins", metadata=_metadata())

    assert doc.line_items[0].service_id == "ROW-SERVICE"
    assert doc.line_items[0].billing_reference == "ROW-REF"


def test_compact_payload_same_as_service_id_still_fills_missing_billing_reference():
    raw = compact_to_legacy_raw(
        {
            "h": {"sn": "Supplier", "in": "INV-1", "cu": "USD"},
            "line_item_defaults": {"service_id": "HEADER-SERVICE"},
            "rows": [{"r": "p2.r14", "a": 10, "p": 2}],
        },
        supplier_name="Supplier",
        line_item_hints={"billing_reference_preference": "same_as_service_id"},
    )

    doc = build_extracted_from_ai_raw(raw, intake_id="same-as", metadata=_metadata())

    assert doc.line_items[0].service_id == "HEADER-SERVICE"
    assert doc.line_items[0].billing_reference == "HEADER-SERVICE"


def test_ai_raw_unknown_currency_with_multiple_allowed_warns_and_does_not_crash():
    rules = {"allowed_codes": ["USD", "EUR"], "aliases": {}, "default_code": None}
    raw = {
        "header": {
            "supplier_name": "MultiCurrency",
            "invoice_number": "INV-1",
            "invoice_date": "2026-01-01",
            "currency": "mystery money",
        },
        "line_items": [
            {
                "line_number": 1,
                "description": "svc",
                "amount": 10,
                "currency": "mystery money",
            }
        ],
        "overall_confidence": 1,
    }

    doc = build_extracted_from_ai_raw(
        raw,
        intake_id="multi",
        metadata=_metadata(),
        currency_rules=rules,
    )

    assert "currency" not in doc.fields
    assert "currency" not in doc.rows[0]
    assert doc.line_items[0].currency == "USD"
    assert any("mystery money" in warning for warning in doc.warnings)


def test_compact_null_invoice_date_does_not_break_header_preview():
    raw = compact_to_legacy_raw(
        {
            "h": {
                "sn": "Granite",
                "in": "238526639",
                "id": None,
                "dd": "03/01/2026",
                "ac": "08918872",
                "cu": "USD",
            },
            "rows": [{"r": "p2.r10", "s": "08918899", "b": "Granite Branch", "a": 104.36, "x": 12.53}],
            "oc": 0.88,
        },
        supplier_name="Granite",
    )

    doc = build_extracted_from_ai_raw(raw, intake_id="preview", metadata=_metadata())

    assert doc.header.invoice_date.isoformat() == "2026-03-01"
    assert doc.header.invoice_number == "238526639"
    assert SYNTHETIC_INVOICE_DATE_WARNING in doc.warnings


def test_chunk_json_length_error_splits_and_retries(monkeypatch, tmp_path):
    original_runtime_value = runtime_settings.runtime_value
    monkeypatch.setattr(
        runtime_settings,
        "runtime_value",
        lambda name: 2 if name == "ai_chunk_max_retries" else original_runtime_value(name),
    )
    client = _FakeClient(
        [
            _response('{"rows":[{"r":"p1.r1","s":"unfinished}', finish_reason="length"),
            _response(
                json.dumps({"rows": [{"r": "p1.r1", "s": "A", "b": "Name A", "a": 10, "x": 1, "p": 1}]})
            ),
            _response(
                json.dumps({"rows": [{"r": "p1.r3", "s": "B", "b": "Name B", "a": 20, "x": 2, "p": 1}]})
            ),
        ]
    )
    calls = []
    chunk_artifacts = []
    chunk = LayoutChunk(
        chunk_id="0001",
        rows=[
            LayoutRowLine(page=1, row_id=f"p1.r{i}", text=f"p1.r{i} ACCOUNT {i} NAME Row {i} 10.00")
            for i in range(1, 5)
        ],
    )

    rows = _extract_chunk_rows(
        client,
        chunk,
        output_type="standard",
        supplier_profile_data=None,
        field_schema=default_field_schema(),
        row_fields={"service_id", "billing_reference", "amount", "tax_amount"},
        calls=calls,
        artifact_dir=tmp_path,
        chunk_artifacts=chunk_artifacts,
    )

    assert [row["s"] for row in rows] == ["A", "B"]
    assert [call.status for call in calls] == ["error", "success", "success"]
    assert (tmp_path / "chunk_0001a.compact.json").exists()
    assert (tmp_path / "chunk_0001b.compact.json").exists()


def test_large_layout_chunks_and_dedupes_rows(monkeypatch):
    original_runtime_value = runtime_settings.runtime_value
    monkeypatch.setattr(
        runtime_settings,
        "runtime_value",
        lambda name: {
            "ai_chunk_max_layout_chars": 180,
            "ai_chunk_overlap_rows": 2,
        }.get(name, original_runtime_value(name)),
    )
    rows = [
        LayoutRowLine(page=1, row_id=f"p1.r{i}", text=f"p1.r{i} ACCOUNT {i:04d} NAME Branch {i:04d} 10.00")
        for i in range(100)
    ]

    chunks = chunk_layout_rows(rows)
    merged = merge_compact_rows(
        [
            {"r": "p1.r1", "s": "A", "b": "Name A", "a": 10, "x": 1, "p": 1},
            {"r": "p1.r1", "s": "A", "b": "Name A", "a": 10, "x": 1, "p": 1},
            {"r": "p1.r2", "s": "B", "b": "Name B", "a": 20, "x": 2, "p": 1},
        ]
    )

    assert len(chunks) > 1
    assert all(chunk.rows for chunk in chunks)
    assert [row["s"] for row in merged] == ["A", "B"]


def test_candidate_detection_finds_granite_glued_account_rows():
    rows = [
        LayoutRowLine(
            page=13,
            row_id="p13.r7",
            text=(
                "p13.r7 0 $0.00 $1.30 $147.65 CHALMETTE, LA "
                "0.00 $0.0005127710 $146.35 4839 PARIS RD"
            ),
        )
    ]

    candidates = detect_candidate_rows(rows, _granite_profile_for_candidates())

    assert [(candidate.row_id, candidate.service_id) for candidate in candidates] == [("p13.r7", "05127710")]
    assert "05127710" in candidates[0].evidence


def test_candidate_detection_finds_granite_summary_rows_without_header_candidate():
    rows = [
        LayoutRowLine(
            page=3,
            row_id="p3.r5",
            text=(
                "p3.r5 role=detail [p3.r5.c0 identifier] ACCOUNT | [p3.r5.c1 text] NAME | "
                "[p3.r5.c2 text] CITY / STATE | [p3.r5.c7 text] TAX / SURCHRG | "
                "[p3.r5.c8 amount] CHARGES | [p3.r5.c10 amount] SUB-TOT"
            ),
        ),
        LayoutRowLine(
            page=3,
            row_id="p3.r6",
            text=(
                "p3.r6 role=service_start [p3.r6.c0 identifier] 01682617 | "
                "[p3.r6.c1 text] Branch One | [p3.r6.c7 amount] $14.43 | "
                "[p3.r6.c8 amount] $70.80 | [p3.r6.c9 amount] $0.00 | [p3.r6.c10 amount] $85.23"
            ),
        ),
        LayoutRowLine(
            page=3,
            row_id="p3.r7",
            text=(
                "p3.r7 role=service_start [p3.r7.c0 identifier] 01981119 | "
                "[p3.r7.c1 text] Branch Two | [p3.r7.c7 amount] $1.25 | "
                "[p3.r7.c8 amount] $16.40 | [p3.r7.c9 amount] $0.00 | [p3.r7.c10 amount] $17.65"
            ),
        ),
    ]

    candidates = detect_candidate_rows(rows, _granite_profile_for_candidates())

    assert [(candidate.row_id, candidate.service_id) for candidate in candidates] == [
        ("p3.r6", "01682617"),
        ("p3.r7", "01981119"),
    ]


def test_candidate_detection_ignores_granite_detail_charge_rows():
    rows = [
        LayoutRowLine(
            page=8,
            row_id="p8.r2",
            text="p8.r2 role=detail [p8.r2.c0 text] Number | [p8.r2.c1 text] Line Charges | [p8.r2.c2 amount] $104.36",
        ),
        LayoutRowLine(
            page=8,
            row_id="p8.r3",
            text="p8.r3 role=detail [p8.r3.c0 text] Goods and Service Tax (GST) | [p8.r3.c1 amount] $12.53",
        ),
        LayoutRowLine(
            page=8,
            row_id="p8.r4",
            text="p8.r4 role=detail [p8.r4.c0 text] CRT Levy | [p8.r4.c1 amount] $0.35",
        ),
        LayoutRowLine(
            page=8,
            row_id="p8.r5",
            text=(
                "p8.r5 role=service_start [p8.r5.c0 identifier] 08918899 | "
                "[p8.r5.c1 text] Valid Branch | [p8.r5.c7 amount] $12.53 | "
                "[p8.r5.c8 amount] $104.36"
            ),
        ),
    ]

    candidates = detect_candidate_rows(rows, _granite_profile_for_candidates())

    assert [(candidate.row_id, candidate.service_id) for candidate in candidates] == [("p8.r5", "08918899")]


def test_candidate_detection_finds_rogers_marker_service_rows():
    rows = [
        LayoutRowLine(page=41, row_id="p41.r0", text="p41.r0 PDFSPLITSTART-5-0579-9746_416-219-3396-PDFSPLITEND"),
        LayoutRowLine(page=41, row_id="p41.r17", text="p41.r17 Total before taxes | 70.00"),
        LayoutRowLine(page=41, row_id="p41.r19", text="p41.r19 Total for Wireless 416-219-3396 | $79.10"),
        LayoutRowLine(page=43, row_id="p43.r0", text="p43.r0 PDFSPLITSTART-5-0579-9746_437-860-8013-PDFSPLITEND"),
        LayoutRowLine(page=43, row_id="p43.r17", text="p43.r17 Total before taxes | 115.80"),
        LayoutRowLine(page=43, row_id="p43.r19", text="p43.r19 Total for Wireless 437-860-8013 | $130.85"),
    ]

    candidates = detect_candidate_rows(rows, _rogers_profile_for_marker_candidates())

    assert [(candidate.row_id, candidate.service_id) for candidate in candidates] == [
        ("p41.r17", "416-219-3396"),
        ("p43.r17", "437-860-8013"),
    ]
    assert "PDFSPLITSTART" in candidates[0].evidence


def test_marker_candidate_anchors_to_amount_row_when_chunk_boundary_splits_group():
    rows = [
        LayoutRowLine(page=23, row_id="p23.r0", text="p23.r0 PDFSPLITSTART-5-0579-9746_306-501-3846-PDFSPLITEND"),
        LayoutRowLine(page=23, row_id="p23.r6", text="p23.r6 5GBI REGINA"),
        LayoutRowLine(page=23, row_id="p23.r20", text="p23.r20 Total before taxes | 115.80"),
        LayoutRowLine(page=23, row_id="p23.r23", text="p23.r23 Total for Wireless 306-501-3846 | $128.54"),
    ]

    candidates = detect_candidate_rows(rows, _rogers_profile_for_marker_candidates())
    chunk = chunk_layout_rows(rows[1:], candidates=candidates)[0]

    assert [(candidate.row_id, candidate.service_id) for candidate in candidates] == [("p23.r20", "306-501-3846")]
    assert [(candidate.row_id, candidate.service_id) for candidate in chunk.candidates] == [("p23.r20", "306-501-3846")]


def test_labeled_service_candidate_carries_orange_id_to_following_total_row():
    rows = [
        LayoutRowLine(page=15, row_id="p15.r25", text="p15.r25 SITE : FRMUT60-C1"),
        LayoutRowLine(
            page=15,
            row_id="p15.r26",
            text="p15.r26 Internet Essential - Country : FRA - Installed Offer ID : 12-8479664",
        ),
        LayoutRowLine(page=15, row_id="p15.r27", text="p15.r27 RECURRING CHARGES"),
        LayoutRowLine(
            page=15,
            row_id="p15.r28",
            text="p15.r28 Access | 01/06/2026 30/06/2026 | 1 | 1 | 78.75 | 78.75 EUR | 20.00 | 78.75",
        ),
        LayoutRowLine(
            page=15,
            row_id="p15.r29",
            text="p15.r29 monthly service management bronze | 60.00 | 60.00 EUR | 20.00 | 60.00",
        ),
        LayoutRowLine(page=15, row_id="p15.r30", text="p15.r30 TOTAL | 20.00 | 138.75"),
        LayoutRowLine(
            page=15,
            row_id="p15.r32",
            text="p15.r32 Internet Essential - Country : FRA - Installed Offer ID : 12-4221978",
        ),
        LayoutRowLine(page=15, row_id="p15.r35", text="p15.r35 TOTAL | 20.00 | 100.00"),
    ]

    candidates = detect_candidate_rows(rows, _orange_profile_for_labeled_service_candidates())
    chunk = chunk_layout_rows(rows[2:6], candidates=candidates)[0]

    assert [(candidate.row_id, candidate.service_id, candidate.billing_reference) for candidate in candidates] == [
        ("p15.r30", "12-8479664", "Access"),
        ("p15.r35", "12-4221978", None),
    ]
    assert [(candidate.row_id, candidate.service_id) for candidate in chunk.candidates] == [
        ("p15.r30", "12-8479664")
    ]


def test_column_service_total_candidates_pair_interleaved_att_columns():
    rows = [
        LayoutRowLine(
            page=3,
            row_id="p3.r43",
            text="p3.r43 role=detail [p3.r43.c0 c0 text x=524-700] Circuit #: MLEC.943524..ATI",
        ),
        LayoutRowLine(
            page=3,
            row_id="p3.r44",
            text="p3.r44 role=detail [p3.r44.c0 c0 text x=524-650] AT&T VPN Service",
        ),
        LayoutRowLine(
            page=3,
            row_id="p3.r50",
            text=(
                "p3.r50 role=detail [p3.r50.c0 c0 text x=49-132] Port ID: 2841714 | "
                "[p3.r50.c1 c1 summary x=524-654] Total AT&T VPN Service | "
                "[p3.r50.c2 c2 amount x=921-954] 214.94"
            ),
        ),
        LayoutRowLine(
            page=3,
            row_id="p3.r51",
            text="p3.r51 role=detail [p3.r51.c0 c0 text x=49-192] Circuit #: DHEC.964077..ATI",
        ),
        LayoutRowLine(
            page=3,
            row_id="p3.r52",
            text=(
                "p3.r52 role=detail [p3.r52.c0 c0 text x=49-150] AT&T VPN Service | "
                "[p3.r52.c1 c1 summary x=524-557] Taxes"
            ),
        ),
        LayoutRowLine(
            page=3,
            row_id="p3.r61",
            text=(
                "p3.r61 role=detail [p3.r61.c0 c0 summary x=49-178] Total AT&T VPN Service | "
                "[p3.r61.c1 c1 amount x=457-478] 6.16"
            ),
        ),
    ]

    candidates = detect_candidate_rows(rows, _att_column_profile_for_candidates())

    assert [
        (candidate.row_id, candidate.service_id, candidate.billing_reference, candidate.amount)
        for candidate in candidates
    ] == [
        ("p3.r50", "MLEC.943524..ATI", "AT&T VPN Service", Decimal("214.94")),
        ("p3.r61", "DHEC.964077..ATI", "AT&T VPN Service", Decimal("6.16")),
    ]

    amount_chunk = chunk_layout_rows(rows[-1:], candidates=candidates)[0]
    assert [(candidate.row_id, candidate.service_id) for candidate in amount_chunk.candidates] == [
        ("p3.r61", "DHEC.964077..ATI")
    ]
    extracted = [{"r": "p3.r61", "s": None, "b": "AT&T VPN Service", "a": 6.16, "p": 3}]
    _apply_candidate_defaults(amount_chunk.candidates, extracted)
    assert extracted[0]["s"] == "DHEC.964077..ATI"


def test_column_service_total_candidates_handle_credit_and_page_continuation():
    rows = [
        LayoutRowLine(
            page=2,
            row_id="p2.r15",
            text="p2.r15 role=detail [p2.r15.c0 c0 text x=519-660] Circuit #: DHEC.486870..ATI",
        ),
        LayoutRowLine(
            page=2,
            row_id="p2.r16",
            text="p2.r16 role=detail [p2.r16.c0 c0 text x=519-647] AT&T VPN Service",
        ),
        LayoutRowLine(
            page=2,
            row_id="p2.r26",
            text=(
                "p2.r26 role=header [p2.r26.c0 c0 summary x=519-647] Total AT&T VPN Service | "
                "[p2.r26.c1 c1 identifier x=912-959] 116.84CR"
            ),
        ),
        LayoutRowLine(
            page=4,
            row_id="p4.r61",
            text="p4.r61 role=detail [p4.r61.c0 c0 text x=524-666] Circuit #: BKEC.999779..ATI",
        ),
        LayoutRowLine(
            page=5,
            row_id="p5.r17",
            text=(
                "p5.r17 role=detail [p5.r17.c0 c0 summary x=49-178] Total AT&T VPN Service | "
                "[p5.r17.c1 c1 amount x=452-478] 64.88"
            ),
        ),
    ]

    candidates = detect_candidate_rows(rows, _att_column_profile_for_candidates())

    assert [(candidate.row_id, candidate.service_id, candidate.amount) for candidate in candidates] == [
        ("p2.r26", "DHEC.486870..ATI", Decimal("-116.84")),
        ("p5.r17", "BKEC.999779..ATI", Decimal("64.88")),
    ]


def test_missing_column_service_total_candidate_recovers_grounded_row():
    candidate = CandidateRow(
        row_id="p3.r61",
        page=3,
        service_id="DHEC.964077..ATI",
        billing_reference="AT&T VPN Service",
        amount=Decimal("6.16"),
        evidence="Circuit #: DHEC.964077..ATI Total AT&T VPN Service 6.16",
    )

    recovered = _recover_missing_total_candidate_rows(
        _att_column_profile_for_candidates(),
        [candidate],
    )

    assert recovered == [{
        "r": "p3.r61",
        "s": "DHEC.964077..ATI",
        "a": "6.16",
        "p": 3,
        "c": "recurring",
        "b": "AT&T VPN Service",
    }]


def test_total_row_candidates_find_lumen_service_totals_and_skip_account_total():
    rows = [
        LayoutRowLine(
            page=5,
            row_id="p5.r10",
            text="p5.r10 role=group_total Total 441101218 608.48 393.12 1,001.60 441209888",
        ),
        LayoutRowLine(
            page=7,
            row_id="p7.r20",
            text="p7.r20 role=group_total Total 444182254 4,908.32 370.57 5,278.89 BDGG2023",
        ),
        LayoutRowLine(
            page=7,
            row_id="p7.r21",
            text="p7.r21 role=group_total Total BDGG2023 2,089.50 321.79 2,411.29 BDKR2022",
        ),
        LayoutRowLine(
            page=7,
            row_id="p7.r22",
            text=(
                "p7.r22 role=group_total Total BDKR2022 1,637.77 245.99 1,883.76 "
                "Total 1-C212CJ 25,551.43 3,368.85 28,920.28"
            ),
        ),
    ]

    candidates = detect_candidate_rows(rows, _lumen_profile_for_total_row_candidates())

    assert [(candidate.row_id, candidate.service_id) for candidate in candidates] == [
        ("p5.r10", "441101218"),
        ("p7.r20", "444182254"),
        ("p7.r21", "BDGG2023"),
        ("p7.r22", "BDKR2022"),
    ]
    assert candidates[0].amount == Decimal("608.48")
    assert candidates[0].tax_amount == Decimal("393.12")
    assert candidates[0].total_amount == Decimal("1001.60")
    assert candidates[2].billing_reference == "BDGG2023"


def test_missing_lumen_total_candidates_recover_compact_rows_from_layout():
    rows = [
        LayoutRowLine(
            page=7,
            row_id="p7.r20",
            text="p7.r20 role=group_total Total 444182254 4,908.32 370.57 5,278.89 BDGG2023",
        ),
        LayoutRowLine(
            page=7,
            row_id="p7.r21",
            text="p7.r21 role=group_total Total BDGG2023 2,089.50 321.79 2,411.29 BDKR2022",
        ),
    ]
    profile = _lumen_profile_for_total_row_candidates()
    candidates = detect_candidate_rows(rows, profile)

    recovered = _recover_missing_total_candidate_rows(profile, candidates[1:])

    assert recovered == [
        {
            "r": "p7.r21",
            "s": "BDGG2023",
            "a": "2089.50",
            "p": 7,
            "c": "recurring",
            "b": "BDGG2023",
            "x": "321.79",
        }
    ]


def test_lumen_total_candidate_reconciliation_warns_on_partial_service_rows():
    rows = [
        LayoutRowLine(
            page=7,
            row_id="p7.r21",
            text="p7.r21 role=group_total Total BDGG2023 2,089.50 321.79 2,411.29 BDKR2022",
        ),
        LayoutRowLine(
            page=7,
            row_id="p7.r22",
            text="p7.r22 role=group_total Total 1-C212CJ 25,551.43 3,368.85 28,920.28",
        ),
    ]
    profile = _lumen_profile_for_total_row_candidates()
    candidates = detect_candidate_rows(rows, profile)

    warnings = _total_candidate_reconciliation_warnings(candidates, {"ac": "1-C212CJ"}, rows, profile)

    assert warnings == [
        "Total-row candidate reconciliation mismatch: service total candidates sum to 2411.29, "
        "but the printed account total is 28920.28."
    ]


def test_candidate_defaults_fill_missing_orange_service_id_by_total_row_ref():
    candidates = [
        CandidateRow(
            row_id="p15.r30",
            page=15,
            service_id="12-8479664",
            evidence="Installed Offer ID : 12-8479664 Access TOTAL 138.75",
            billing_reference="Access",
        )
    ]
    rows = [{"a": 138.75, "b": "Access", "c": "recurring", "p": 15, "r": "p15.r30", "s": None, "x": 27.75}]

    _apply_candidate_defaults(candidates, rows)

    assert rows == [
        {
            "a": 138.75,
            "b": "Access",
            "c": "recurring",
            "p": 15,
            "r": "p15.r30",
            "s": "12-8479664",
            "x": 27.75,
        }
    ]


def test_marker_candidate_carries_billing_reference_from_full_group():
    rows = [
        LayoutRowLine(page=41, row_id="p41.r0", text="p41.r0 PDFSPLITSTART-5-0579-9746_416-917-4971-PDFSPLITEND"),
        LayoutRowLine(page=41, row_id="p41.r7", text="p41.r7 Monthly charges | Oct 05 - Nov 04 | $"),
        LayoutRowLine(
            page=41,
            row_id="p41.r9",
            text="p41.r9 5G Bus Intrnt Office Pro - 3yr | 70.00",
        ),
        LayoutRowLine(page=41, row_id="p41.r17", text="p41.r17 Total before taxes | 70.00"),
        LayoutRowLine(page=41, row_id="p41.r19", text="p41.r19 Total for Wireless 416-917-4971 | $79.10"),
    ]

    candidates = detect_candidate_rows(rows, _rogers_profile_for_marker_candidates())

    assert [(candidate.row_id, candidate.service_id, candidate.billing_reference) for candidate in candidates] == [
        ("p41.r17", "416-917-4971", "5G Bus Intrnt Office Pro - 3yr")
    ]


def test_marker_candidate_strips_layout_cell_metadata_from_billing_reference():
    rows = [
        LayoutRowLine(
            page=41,
            row_id="p41.r0",
            text="p41.r0 role=detail [p41.r0.c0 c0 text x=0-247] "
            "PDFSPLITSTART-5-0579-9746_416-917-4971-PDFSPLITEND",
        ),
        LayoutRowLine(page=41, row_id="p41.r7", text="p41.r7 Monthly charges | Oct 05 - Nov 04 | $"),
        LayoutRowLine(
            page=41,
            row_id="p41.r9",
            text="p41.r9 role=detail [p41.r9.c0 c0 text x=74-229] "
            "5G Bus Intrnt Office Pro - 3yr | [p41.r9.c1 c1 amount x=608-640] 70.00",
        ),
        LayoutRowLine(page=41, row_id="p41.r17", text="p41.r17 Total before taxes | 70.00"),
        LayoutRowLine(page=41, row_id="p41.r19", text="p41.r19 Total for Wireless 416-917-4971 | $79.10"),
    ]

    candidates = detect_candidate_rows(rows, _rogers_profile_for_marker_candidates())

    assert [(candidate.row_id, candidate.service_id, candidate.billing_reference) for candidate in candidates] == [
        ("p41.r17", "416-917-4971", "5G Bus Intrnt Office Pro - 3yr")
    ]


def test_candidate_defaults_fill_missing_billing_reference_from_service_candidate():
    candidates = [
        CandidateRow(
            row_id="p41.r17",
            page=41,
            service_id="416-917-4971",
            evidence="service group",
            billing_reference="5G Bus Intrnt Office Pro - 3yr",
        )
    ]
    rows = [{"r": "p41.r17", "s": "416-917-4971", "b": None, "a": 70, "x": 9.1, "p": 41}]

    _apply_candidate_defaults(candidates, rows)

    assert rows[0]["b"] == "5G Bus Intrnt Office Pro - 3yr"


def test_candidate_defaults_strip_layout_cell_metadata_from_existing_and_fallback_values():
    candidates = [
        CandidateRow(
            row_id="p41.r17",
            page=41,
            service_id="416-917-4971",
            evidence="service group",
            billing_reference="[p41.r9.c0 c0 text x=74-229] 5G Bus Intrnt Office Pro - 3yr | "
            "[p41.r9.c1 c1 amount x=608-640]",
        )
    ]
    rows = [
        {"r": "p41.r17", "s": "416-917-4971", "b": None, "a": 70, "x": 9.1, "p": 41},
        {
            "r": "p45.r17",
            "s": "437-215-1446",
            "b": "[p45.r9.c0 c0 text x=74-229] 5G Bus Intrnt Office Pro - 3yr | "
            "[p45.r9.c1 c1 amount x=608-640]",
            "a": 70,
            "x": 9.1,
            "p": 45,
        },
    ]

    _apply_candidate_defaults(candidates, rows)

    assert rows[0]["b"] == "5G Bus Intrnt Office Pro - 3yr"
    assert rows[1]["b"] == "5G Bus Intrnt Office Pro - 3yr"


def test_marker_candidate_detection_skips_zero_amount_groups_when_profile_excludes_zero_rows():
    rows = [
        LayoutRowLine(page=43, row_id="p43.r0", text="p43.r0 PDFSPLITSTART-5-0579-9746_418-456-4608-PDFSPLITEND"),
        LayoutRowLine(page=43, row_id="p43.r15", text="p43.r15 Total before taxes | 0.00"),
        LayoutRowLine(page=43, row_id="p43.r16", text="p43.r16 Total for Wireless 418-456-4608 | $0.00"),
        LayoutRowLine(page=45, row_id="p45.r0", text="p45.r0 PDFSPLITSTART-5-0579-9746_437-215-1446-PDFSPLITEND"),
        LayoutRowLine(page=45, row_id="p45.r17", text="p45.r17 Total before taxes | 70.00"),
        LayoutRowLine(page=45, row_id="p45.r19", text="p45.r19 Total for Wireless 437-215-1446 | $79.10"),
    ]

    candidates = detect_candidate_rows(rows, _rogers_profile_for_marker_candidates())

    assert [(candidate.row_id, candidate.service_id) for candidate in candidates] == [("p45.r17", "437-215-1446")]


def test_marker_candidate_detection_keeps_zero_amount_groups_when_profile_includes_zero_rows():
    profile = _rogers_profile_for_marker_candidates()
    profile["advanced"]["include_zero_amount_line_items"] = True
    profile["advanced"]["line_item_hints"]["include_zero_amount_line_items"] = True
    rows = [
        LayoutRowLine(page=43, row_id="p43.r0", text="p43.r0 PDFSPLITSTART-5-0579-9746_418-456-4608-PDFSPLITEND"),
        LayoutRowLine(page=43, row_id="p43.r15", text="p43.r15 Total before taxes | 0.00"),
        LayoutRowLine(page=43, row_id="p43.r16", text="p43.r16 Total for Wireless 418-456-4608 | $0.00"),
    ]

    candidates = detect_candidate_rows(rows, profile)

    assert [(candidate.row_id, candidate.service_id) for candidate in candidates] == [("p43.r15", "418-456-4608")]


def test_marker_candidate_detection_requires_explicit_service_pattern():
    rows = [
        LayoutRowLine(page=41, row_id="p41.r0", text="p41.r0 PDFSPLITSTART-5-0579-9746_416-219-3396-PDFSPLITEND"),
    ]

    candidates = detect_candidate_rows(rows, _rogers_profile_for_marker_candidates(include_pattern=False))

    assert candidates == []


def test_chunk_prompt_includes_candidate_accountability():
    rows = [
        LayoutRowLine(
            page=13,
            row_id="p13.r7",
            text="p13.r7 0 $0.00 $1.30 $147.65 0.00 $0.0005127710 $146.35",
        )
    ]
    candidates = detect_candidate_rows(rows, _granite_profile_for_candidates())
    chunk = chunk_layout_rows(rows, candidates=candidates)[0]

    prompt = _build_chunk_prompt(
        chunk,
        output_type="standard",
        supplier_profile_data=_granite_profile_for_candidates(),
        field_schema=default_field_schema(),
        row_fields={"service_id", "billing_reference", "amount", "tax_amount"},
    )

    assert "--- CANDIDATE ROW ACCOUNTABILITY ---" in prompt
    assert "This chunk contains 1 candidate delivered row(s)" in prompt
    assert "Emit one compact row for each candidate" in prompt
    assert "service_id=05127710" in prompt


def test_chunk_prompt_treats_marker_candidates_as_distinct_services():
    rows = [
        LayoutRowLine(page=41, row_id="p41.r0", text="p41.r0 PDFSPLITSTART-5-0579-9746_416-219-3396-PDFSPLITEND"),
        LayoutRowLine(page=41, row_id="p41.r17", text="p41.r17 Total before taxes | 70.00"),
        LayoutRowLine(page=41, row_id="p41.r19", text="p41.r19 Total for Wireless 416-219-3396 | $79.10"),
    ]
    profile = _rogers_profile_for_marker_candidates()
    candidates = detect_candidate_rows(rows, profile)
    chunk = chunk_layout_rows(rows, candidates=candidates)[0]

    prompt = _build_chunk_prompt(
        chunk,
        output_type="standard",
        supplier_profile_data=profile,
        field_schema=default_field_schema(),
        row_fields={"service_id", "billing_reference", "amount", "tax_amount"},
    )

    assert "This chunk contains 1 candidate delivered row(s)" in prompt
    assert "service_id=416-219-3396" in prompt
    assert "Each listed candidate service_id is a distinct delivered service" in prompt


def test_chunk_missing_candidates_triggers_strict_retry(monkeypatch, tmp_path):
    original_runtime_value = runtime_settings.runtime_value
    monkeypatch.setattr(
        runtime_settings,
        "runtime_value",
        lambda name: 2 if name == "ai_chunk_max_retries" else original_runtime_value(name),
    )
    rows = [
        LayoutRowLine(page=13, row_id="p13.r6", text="p13.r6 0 $0.00 $1.30 0.00 $0.0005127708 $146.35"),
        LayoutRowLine(page=13, row_id="p13.r7", text="p13.r7 0 $0.00 $1.30 0.00 $0.0005127710 $146.35"),
    ]
    candidates = detect_candidate_rows(rows, _granite_profile_for_candidates())
    client = _FakeClient(
        [
            _response(json.dumps({"rows": [{"r": "p13.r6", "s": "05127708", "b": "A", "a": 146.35, "x": 1.30}]})),
            _response(json.dumps({"rows": [{"r": "p13.r7", "s": "05127710", "b": "B", "a": 146.35, "x": 1.30}]})),
        ]
    )
    calls = []
    chunk_artifacts = []

    extracted = _extract_chunk_rows(
        client,
        LayoutChunk(chunk_id="0001", rows=rows, candidates=candidates),
        output_type="standard",
        supplier_profile_data=_granite_profile_for_candidates(),
        field_schema=default_field_schema(),
        row_fields={"service_id", "billing_reference", "amount", "tax_amount"},
        calls=calls,
        artifact_dir=tmp_path,
        chunk_artifacts=chunk_artifacts,
    )

    assert [row["s"] for row in extracted] == ["05127708", "05127710"]
    assert [call.details["strict_retry"] for call in calls] == [False, True]
    assert chunk_artifacts[0]["missing_candidate_examples"] == ["05127710"]
    assert (tmp_path / "chunk_0001r.compact.json").exists()


def test_rogers_marker_candidates_trigger_strict_retry(monkeypatch, tmp_path):
    original_runtime_value = runtime_settings.runtime_value
    monkeypatch.setattr(
        runtime_settings,
        "runtime_value",
        lambda name: 2 if name == "ai_chunk_max_retries" else original_runtime_value(name),
    )
    profile = _rogers_profile_for_marker_candidates()
    rows = [
        LayoutRowLine(page=41, row_id="p41.r0", text="p41.r0 PDFSPLITSTART-5-0579-9746_416-219-3396-PDFSPLITEND"),
        LayoutRowLine(page=41, row_id="p41.r17", text="p41.r17 Total before taxes | 70.00"),
        LayoutRowLine(page=41, row_id="p41.r19", text="p41.r19 Total for Wireless 416-219-3396 | $79.10"),
        LayoutRowLine(page=43, row_id="p43.r0", text="p43.r0 PDFSPLITSTART-5-0579-9746_437-860-8013-PDFSPLITEND"),
        LayoutRowLine(page=43, row_id="p43.r17", text="p43.r17 Total before taxes | 115.80"),
        LayoutRowLine(page=43, row_id="p43.r19", text="p43.r19 Total for Wireless 437-860-8013 | $130.85"),
    ]
    candidates = detect_candidate_rows(rows, profile)
    client = _FakeClient(
        [
            _response(
                json.dumps(
                    {
                        "rows": [
                            {
                                "r": "p41.r0",
                                "s": "416-219-3396",
                                "b": "5G Bus Intrnt Office Pro - 3yr",
                                "a": 70.0,
                                "x": 9.10,
                            }
                        ]
                    }
                )
            ),
            _response(
                json.dumps(
                    {
                        "rows": [
                            {
                                "r": "p43.r0",
                                "s": "437-860-8013",
                                "b": "5G Business Internet Premium",
                                "a": 115.80,
                                "x": 15.05,
                            }
                        ]
                    }
                )
            ),
        ]
    )
    calls = []
    chunk_artifacts = []

    extracted = _extract_chunk_rows(
        client,
        LayoutChunk(chunk_id="0001", rows=rows, candidates=candidates),
        output_type="standard",
        supplier_profile_data=profile,
        field_schema=default_field_schema(),
        row_fields={"service_id", "billing_reference", "amount", "tax_amount"},
        calls=calls,
        artifact_dir=tmp_path,
        chunk_artifacts=chunk_artifacts,
    )

    assert [row["s"] for row in extracted] == ["416-219-3396", "437-860-8013"]
    assert [call.details["strict_retry"] for call in calls] == [False, True]
    assert chunk_artifacts[0]["missing_candidate_examples"] == ["437-860-8013"]
    assert (tmp_path / "chunk_0001r.compact.json").exists()


def test_chunk_retry_failure_splits_missing_window(monkeypatch, tmp_path):
    original_runtime_value = runtime_settings.runtime_value
    monkeypatch.setattr(
        runtime_settings,
        "runtime_value",
        lambda name: 2 if name == "ai_chunk_max_retries" else original_runtime_value(name),
    )
    rows = [
        LayoutRowLine(page=13, row_id="p13.r6", text="p13.r6 0 $0.00 $1.30 0.00 $0.0005127708 $146.35"),
        LayoutRowLine(page=13, row_id="p13.r7", text="p13.r7 0 $0.00 $1.30 0.00 $0.0005127710 $146.35"),
    ]
    candidates = detect_candidate_rows(rows, _granite_profile_for_candidates())
    client = _FakeClient(
        [
            _response(json.dumps({"rows": []})),
            _response(json.dumps({"rows": []})),
            _response(json.dumps({"rows": [{"r": "p13.r6", "s": "05127708", "b": "A", "a": 146.35, "x": 1.30}]})),
            _response(json.dumps({"rows": [{"r": "p13.r7", "s": "05127710", "b": "B", "a": 146.35, "x": 1.30}]})),
        ]
    )
    calls = []
    chunk_artifacts = []

    extracted = _extract_chunk_rows(
        client,
        LayoutChunk(chunk_id="0001", rows=rows, candidates=candidates),
        output_type="standard",
        supplier_profile_data=_granite_profile_for_candidates(),
        field_schema=default_field_schema(),
        row_fields={"service_id", "billing_reference", "amount", "tax_amount"},
        calls=calls,
        artifact_dir=tmp_path,
        chunk_artifacts=chunk_artifacts,
    )

    assert [row["s"] for row in extracted] == ["05127708", "05127710"]
    assert [call.details["chunk_id"] for call in calls] == ["0001", "0001r", "0001ra", "0001rb"]
    assert calls[1].details["strict_retry"] is True


def test_chunk_split_after_strict_retry_focuses_only_still_missing_candidates(monkeypatch, tmp_path):
    original_runtime_value = runtime_settings.runtime_value
    monkeypatch.setattr(
        runtime_settings,
        "runtime_value",
        lambda name: 2 if name == "ai_chunk_max_retries" else original_runtime_value(name),
    )
    rows = [
        LayoutRowLine(page=13, row_id="p13.r6", text="p13.r6 0 $0.00 $1.30 0.00 $0.0005127708 $146.35"),
        LayoutRowLine(page=13, row_id="p13.r7", text="p13.r7 0 $0.00 $1.30 0.00 $0.0005127710 $146.35"),
    ]
    candidates = detect_candidate_rows(rows, _granite_profile_for_candidates())
    client = _FakeClient(
        [
            _response(json.dumps({"rows": []})),
            _response(json.dumps({"rows": [{"r": "p13.r6", "s": "05127708", "b": "A", "a": 146.35, "x": 1.30}]})),
            _response(json.dumps({"rows": [{"r": "p13.r7", "s": "05127710", "b": "B", "a": 146.35, "x": 1.30}]})),
        ]
    )
    calls = []
    chunk_artifacts = []

    extracted = _extract_chunk_rows(
        client,
        LayoutChunk(chunk_id="0001", rows=rows, candidates=candidates),
        output_type="standard",
        supplier_profile_data=_granite_profile_for_candidates(),
        field_schema=default_field_schema(),
        row_fields={"service_id", "billing_reference", "amount", "tax_amount"},
        calls=calls,
        artifact_dir=tmp_path,
        chunk_artifacts=chunk_artifacts,
    )

    assert [row["s"] for row in extracted] == ["05127708", "05127710"]
    assert [call.details["chunk_id"] for call in calls] == ["0001", "0001r", "0001rb"]
    assert calls[-1].details["candidate_rows"] == 1


def test_completeness_warnings_report_missing_candidates_after_merge():
    candidates = [
        CandidateRow(row_id="p13.r6", page=13, service_id="05127708", evidence="row 6"),
        CandidateRow(row_id="p13.r7", page=13, service_id="05127710", evidence="row 7"),
    ]
    rows = [{"r": "p13.r6", "s": "05127708", "a": 146.35, "x": 1.30}]

    warnings = _completeness_warnings(candidates, rows, [candidates[1]])

    assert warnings == [
        "Possible missing line items: detected 2 candidate rows, extracted 1. "
        "Missing 1 candidate row(s). Missing examples: 05127710"
    ]


def test_compact_completeness_exception_metadata_survives_canonical_mapping():
    raw = compact_to_legacy_raw(
        {
            "h": {"sn": "Granite", "in": "238526639", "cu": "USD"},
            "rows": [{"r": "p13.r6", "s": "05127708", "b": "Branch A", "a": 146.35, "x": 1.30}],
        },
        supplier_name="Granite",
    )
    warning = (
        "Possible missing line items: detected 2 candidate rows, extracted 1. "
        "Missing 1 candidate row(s). Missing examples: 05127710"
    )
    raw["warnings"] = [warning]
    raw["exceptions"] = [warning]

    doc = build_extracted_from_ai_raw(raw, intake_id="granite", metadata=_metadata())

    assert warning in doc.warnings
    assert doc.exceptions == [warning]


def test_detail_region_captures_every_repeating_service_region():
    """Per-service layouts (Rogers wireless) repeat the start/end markers once per
    service. An end marker must close the current region and keep scanning, not stop
    after the first service — otherwise only one line item is ever extracted."""
    rows: list[LayoutRowLine] = []
    for svc in range(3):
        rows.append(LayoutRowLine(page=svc + 1, row_id=f"s{svc}.start", text=f"PDFSPLITSTART-acct_{svc}-PDFSPLITEND"))
        rows.append(LayoutRowLine(page=svc + 1, row_id=f"s{svc}.charge", text=f"Wireless Bus Internet {svc} 159.99"))
        rows.append(LayoutRowLine(page=svc + 1, row_id=f"s{svc}.end", text=f"Total for Wireless {svc} 114.96"))

    profile = {"advanced": {"document_structure": {
        "detail_start_marker": "PDFSPLITSTART",
        "detail_end_marker": "Total for Wireless",
    }, "line_item_hints": {"line_item_granularity": "per_total_group"}}}
    selected = _detail_region_rows(rows, profile)

    # All three service charge rows survive (not just the first service's).
    charges = [r for r in selected if "Wireless Bus Internet" in r.text]
    assert len(charges) == 3
    # Grouped profiles keep closing total rows because they are often the delivered amount source.
    assert len([r for r in selected if "Total for Wireless" in r.text]) == 3


def test_detail_region_accepts_comma_separated_end_markers_and_excludes_outside_rows():
    rows = [
        LayoutRowLine(page=1, row_id="p1.summary", text="Wireless summary total 999.99"),
        LayoutRowLine(page=2, row_id="p2.start", text="PDFSPLITSTART-5-0346-0352_416-294-0179-PDFSPLITEND"),
        LayoutRowLine(page=2, row_id="p2.charge", text="Rogers 5G Business Internet 50.00"),
        LayoutRowLine(page=2, row_id="p2.total", text="Total for Wireless 416-294-0179 $56.50"),
        LayoutRowLine(page=2, row_id="p2.after", text="Usage detail Total used 0.00"),
        LayoutRowLine(page=3, row_id="p3.start", text="PDFSPLITSTART-5-0346-0352_416-111-2222-PDFSPLITEND"),
        LayoutRowLine(page=3, row_id="p3.charge", text="Phone service 20.00"),
        LayoutRowLine(page=3, row_id="p3.total", text="Total for Phone 416-111-2222 $22.60"),
    ]
    profile = {"advanced": {"document_structure": {
        "detail_start_marker": "PDFSPLITSTART",
        "detail_end_marker": "Total for Wireless,Total for Phone",
    }, "line_item_hints": {"line_item_granularity": "per_total_group"}}}

    selected = _detail_region_rows(rows, profile)

    assert [row.row_id for row in selected] == [
        "p2.start",
        "p2.charge",
        "p2.total",
        "p3.start",
        "p3.charge",
        "p3.total",
    ]


def test_detail_region_excludes_end_marker_without_grouped_policy():
    rows = [
        LayoutRowLine(page=1, row_id="p1.r1", text="PDFSPLITSTART-acct-PDFSPLITEND"),
        LayoutRowLine(page=1, row_id="p1.r2", text="Wireless Bus Internet 159.99"),
        LayoutRowLine(page=1, row_id="p1.r3", text="Total for Wireless 114.96"),
    ]
    profile = {"advanced": {"document_structure": {
        "detail_start_marker": "PDFSPLITSTART",
        "detail_end_marker": "Total for Wireless",
    }}}

    selected = _detail_region_rows(rows, profile)

    assert [r.row_id for r in selected] == ["p1.r1", "p1.r2"]


def test_merge_keeps_distinct_rows_with_identical_values():
    # Two distinct source rows (different r) that happen to share every value —
    # e.g. repeated identical charges with no service_id/billing_reference. They
    # must both survive; only same-r overlap duplicates should be dropped.
    merged = merge_compact_rows(
        [
            {"r": "p3.r5", "s": "", "b": "", "a": 10, "x": 0, "p": 3},
            {"r": "p3.r6", "s": "", "b": "", "a": 10, "x": 0, "p": 3},
            {"r": "p3.r6", "s": "", "b": "", "a": 10, "x": 0, "p": 3},  # overlap dup of r6
        ]
    )

    assert [row["r"] for row in merged] == ["p3.r5", "p3.r6"]


def test_merge_value_dedupes_rows_without_row_ref():
    # Rows lacking an r fall back to value-based dedup so chunk overlap without
    # ids still collapses.
    merged = merge_compact_rows(
        [
            {"s": "A", "b": "", "a": 10, "x": 0, "p": 1},
            {"s": "A", "b": "", "a": 10, "x": 0, "p": 1},
            {"s": "B", "b": "", "a": 20, "x": 0, "p": 1},
        ]
    )

    assert [row["s"] for row in merged] == ["A", "B"]


def test_merge_restores_source_document_order_from_row_refs():
    merged = merge_compact_rows([
        {"r": "p21.r4", "s": "LATE", "a": 20, "p": 21},
        {"r": "p5.r9", "s": "EARLY-2", "a": 10, "p": 5},
        {"r": "p5.r2", "s": "EARLY-1", "a": 5, "p": 5},
    ])

    assert [row["s"] for row in merged] == ["EARLY-1", "EARLY-2", "LATE"]


def test_period_context_carries_across_page_boundary_until_next_anchor():
    layout = [
        LayoutRowLine(page=21, row_id="p21.r8", text="période du 01/06/2026 au 30/06/2026"),
        LayoutRowLine(page=22, row_id="p22.r1", text="n° : 0825809109 60.00"),
        LayoutRowLine(page=22, row_id="p22.r9", text="période du 01/07/2026 au 31/07/2026"),
        LayoutRowLine(page=22, row_id="p22.r10", text="n° : 0820009050 30.00"),
    ]
    rows = [
        {"r": "p22.r1", "s": "0825809109", "a": 60, "p": 22},
        {"r": "p22.r10", "s": "0820009050", "a": 30, "p": 22},
    ]
    plan = ExtractionPlan(row_context_rules=[RowContextRule(
        anchor_pattern=(
            r"(?i)période\s+du\s+(\d{1,2}/\d{1,2}/\d{4})"
            r"\s+au\s+(\d{1,2}/\d{1,2}/\d{4})"
        ),
        field_groups={"billing_period_start": 1, "billing_period_end": 2},
    )])

    _apply_row_context_rules(rows, layout, plan)

    assert rows[0]["bp0"] == "01/06/2026"
    assert rows[0]["bp1"] == "30/06/2026"
    assert rows[1]["bp0"] == "01/07/2026"
    assert rows[1]["bp1"] == "31/07/2026"


def test_granite_compact_rows_map_to_temforce_output():
    raw = compact_to_legacy_raw(
        {
            "h": {
                "sn": "Granite",
                "in": "238526639",
                "id": "2026-03-01",
                "dd": "2026-03-01",
                "ac": "08918872",
                "cu": "USD",
            },
            "rows": [
                {"r": "p2.r10", "s": "08918899", "b": "Granite Branch", "a": 104.36, "x": 12.53, "p": 2}
            ],
            "oc": 1,
        },
        supplier_name="Granite",
    )
    doc = build_extracted_from_ai_raw(raw, intake_id="granite", metadata=_metadata())
    spec = OutputSpec(
        spec_id="temforce.test",
        columns=[
            OutputColumn(header="EXT_SERVICEID", source="row.service_id"),
            OutputColumn(header="EXT_BILLINGREFERENCE", source="row.billing_reference"),
            OutputColumn(header="EXT_AMOUNT", source="row.amount"),
            OutputColumn(header="EXT_TAX", source="computed.line_tax"),
        ],
    )

    rows = build_output_rows(doc, spec)

    assert rows == [["08918899", "Granite Branch", 104.36, 12.53]]


def test_time_material_optional_cost_fields_have_compact_numeric_aliases():
    schema = build_compact_line_item_schema({"plan_cost", "equipment_cost"})
    properties = schema["schema"]["properties"]["rows"]["items"]["properties"]

    assert properties["pl"]["type"] == ["number", "null"]
    assert properties["eq"]["type"] == ["number", "null"]


def test_compact_charge_type_filters_fee_and_surcharge_output_rows():
    raw = compact_to_legacy_raw(
        {
            "h": {
                "sn": "AT&T",
                "in": "4608955112",
                "id": "2026-05-09",
                "dd": "2026-07-08",
                "ac": "831-001-2984 751",
                "cu": "USD",
            },
            "rows": [
                {
                    "r": "p1.r20",
                    "s": "BFEC565162",
                    "b": "BFEC565162",
                    "c": "recurring",
                    "a": 1910.18,
                    "x": 80.64,
                    "p": 1,
                },
                {"r": "p1.r23", "s": "BFEC565162", "b": "BFEC565162", "c": "surcharge", "a": 871.10, "x": None, "p": 1},
                {"r": "p1.r30", "s": "BFEC565162", "b": "BFEC565162", "c": "tax", "a": 80.64, "x": None, "p": 1},
            ],
            "oc": 1,
        },
        supplier_name="AT&T",
    )
    doc = build_extracted_from_ai_raw(raw, intake_id="att", metadata=_metadata())
    spec = OutputSpec(
        spec_id="temforce.test",
        columns=[
            OutputColumn(header="EXT_SERVICEID", source="row.service_id"),
            OutputColumn(header="EXT_AMOUNT", source="row.amount"),
            OutputColumn(header="EXT_TAX", source="computed.line_tax"),
        ],
    )

    rows = build_output_rows(doc, spec)

    assert rows == [["BFEC565162", 1910.18, 80.64]]


def test_chunk_prompt_renders_delivered_policy_and_structured_hints():
    profile = {
        "identity": {"canonical_name": "Granite"},
        "classification": {"output_type": "standard"},
        "advanced": {
            "line_item_hints": {
                "line_item_granularity": "per_service_group",
                "service_id_column_label": "ACCOUNT",
                "billing_reference_column_label": "NAME, CITY/PROVINCE",
                "amount_column_label": "SUB-TOT",
                "tax_amount_column_label": "TAX / SURCHRG",
                "tax_output_mode": "calculate",
                "tax_rate_source": "invoice_tax_divided_by_subtotal",
            }
        },
    }
    prompt = _build_chunk_prompt(
        LayoutChunk(
            chunk_id="0001",
            rows=[LayoutRowLine(page=1, row_id="p1.r1", text="p1.r1 ACCOUNT Branch SUB-TOT 10.00")],
        ),
        output_type="standard",
        supplier_profile_data=profile,
        field_schema=default_field_schema(),
        row_fields={"service_id", "billing_reference", "amount", "tax_amount"},
    )

    assert "--- DELIVERED ROW POLICY ---" in prompt
    assert "line_item_granularity is 'per_service_group'" in prompt
    assert "service_id source: column/field labeled 'ACCOUNT'" in prompt
    assert "amount source: column/field/row labeled 'SUB-TOT'" in prompt
    assert "tax_amount source: column/field/row labeled 'TAX / SURCHRG'" in prompt
    assert "delivered EXT_TAX policy: calculate" in prompt
    assert "tax rate source for calculated EXT_TAX: invoice_tax_divided_by_subtotal" in prompt
    assert "- row.c: charge_type" in prompt


def test_scalar_prompt_renders_document_label_hints_as_required_targets():
    field_schema = merge_field_schema(
        default_field_schema(),
        [
            FieldDef(
                name="invoice_date",
                scope=FieldScope.DOCUMENT,
                type=FieldType.DATE,
                label_hint="Bill Date",
                required=True,
            ),
            FieldDef(
                name="due_date",
                scope=FieldScope.DOCUMENT,
                type=FieldType.DATE,
                label_hint="Your payment is due by:",
            ),
        ],
    )

    prompt = _build_scalar_prompt(
        supplier_name="Bell",
        output_type="standard",
        supplier_profile_data={
            "identity": {"canonical_name": "Bell"},
            "classification": {"output_type": "standard"},
        },
        field_schema=field_schema,
        doc_fields={"invoice_date", "due_date", "invoice_number"},
        rows=[
            LayoutRowLine(page=1, row_id="p1.r1", text="p1.r1 Bill date | March 31, 2026"),
            LayoutRowLine(page=1, row_id="p1.r2", text="p1.r2 Your payment is due by: | Apr 21, 2026"),
        ],
    )

    assert "Document field labels for this source" in prompt
    assert "document.invoice_date: extract from label/field 'Bill Date'" in prompt
    assert "document.due_date: extract from label/field 'Your payment is due by:'" in prompt
    assert "do not copy due_date into invoice_date" in prompt


def test_legacy_ai_mode_still_uses_document_extraction_schema(monkeypatch):
    fake_client = _FakeClient(
        [
            _response(
                json.dumps(
                    {
                        "header": {
                            "supplier_name": "Acme",
                            "supplier_aliases": [],
                            "invoice_number": "INV-1",
                            "invoice_date": "2026-01-01",
                            "due_date": None,
                            "account_number": None,
                            "ban": None,
                            "billing_period_start": None,
                            "billing_period_end": None,
                            "currency": "USD",
                            "po_number": None,
                            "payment_terms": None,
                            "source_page": None,
                            "confidence": 1,
                        },
                        "line_items": [],
                        "overall_confidence": 1,
                        "subtotal": None,
                        "tax": None,
                        "fees": None,
                        "current_charges": None,
                        "total_due": None,
                        "tax_rate": None,
                    }
                )
            )
        ]
    )
    monkeypatch.setattr(runtime_settings, "ai_extraction_mode", lambda: "legacy")
    monkeypatch.setattr(settings, "extraction_use_layout_text", False)
    monkeypatch.setattr(settings, "extraction_send_page_images", False)
    monkeypatch.setattr(extractor, "get_ai_client", lambda *a, **k: fake_client)
    monkeypatch.setattr(extractor, "_extract_flat_text", lambda _pdf_path: ["Invoice Number INV-1"])

    result = extractor.extract_invoice(Path("unused.pdf"), field_schema=default_field_schema())

    assert result.raw_data["header"]["invoice_number"] == "INV-1"
    response_format = fake_client.chat.completions.calls[0]["response_format"]
    assert response_format["json_schema"]["name"] == "document_extraction"


# ── charge-row candidates for section-heading layouts (Lumen) ─────────────────


def _lumen_profile_for_candidates():
    return {
        "identity": {"canonical_name": "Lumen"},
        "classification": {"output_type": "standard"},
        "notes": "",
        "advanced": {
            "document_structure": {
                "detail_start_marker": "SERVICE LEVEL ACTIVITY",
                "detail_end_marker": "NOTICE OF RATE INCREASE",
            },
            "line_item_hints": {
                "line_item_granularity": "per_charge_row",
                "service_id_preference": "child_identifier",
                "service_id_column_label": "Service ID",
                "service_id_value_pattern": r"^(?!5-)\d{6,12}$",
                "table_column_labels": ["Service ID", "Description", "Amount"],
            },
            "include_zero_amount_line_items": True,
        },
    }


def _lumen_rows():
    return [
        LayoutRowLine(page=5, row_id="p5.r7", text=(
            "p5.r7 role=header [p5.r7.c0 c0 text x=32-80] Service ID | "
            "[p5.r7.c1 c1 text x=170-240] Description | [p5.r7.c2 c2 text x=500-560] Amount")),
        LayoutRowLine(page=5, row_id="p5.r8", text=(
            "p5.r8 role=service_start [p5.r8.c0 c0 identifier x=32-80] 445715812")),
        LayoutRowLine(page=5, row_id="p5.r9", text=(
            "p5.r9 role=service_start [p5.r9.c0 c0 identifier x=50-98] 347207739")),
        LayoutRowLine(page=5, row_id="p5.r10", text=(
            "p5.r10 role=detail [p5.r10.c0 c0 text x=104-200] Access GigE | "
            "[p5.r10.c1 c1 amount x=500-560] 378.00 | [p5.r10.c2 c2 amount x=600-650] 21.06")),
        LayoutRowLine(page=5, row_id="p5.r11", text=(
            "p5.r11 role=group_total [p5.r11.c0 c0 summary x=457-520] Total 347207739 | "
            "[p5.r11.c1 c1 amount x=560-610] 378.00")),
    ]


def test_charge_row_candidates_carry_the_child_id_to_the_charge_row():
    candidates = detect_candidate_rows(_lumen_rows(), _lumen_profile_for_candidates())

    assert [(c.row_id, c.service_id) for c in candidates] == [("p5.r10", "347207739")]


def test_charge_row_candidates_skip_headers_and_total_rows():
    candidates = detect_candidate_rows(_lumen_rows(), _lumen_profile_for_candidates())

    assert all(c.row_id not in {"p5.r7", "p5.r11"} for c in candidates)


def test_charge_row_candidates_require_a_money_amount():
    rows = _lumen_rows()[:3]  # header + parent + child, no charge row
    assert detect_candidate_rows(rows, _lumen_profile_for_candidates()) == []


def test_charge_row_candidates_ignore_identifiers_outside_the_service_column():
    rows = _lumen_rows()
    rows.append(LayoutRowLine(page=5, row_id="p5.r12", text=(
        "p5.r12 role=detail [p5.r12.c0 c0 identifier x=700-760] 792243845 | "
        "[p5.r12.c1 c1 amount x=500-560] 99.00")))

    candidates = detect_candidate_rows(rows, _lumen_profile_for_candidates())

    # the far-right invoice number must not hijack the carried child id
    assert [c.service_id for c in candidates] == ["347207739", "347207739"]


def test_charge_row_candidate_detection_is_opt_in():
    profile = _lumen_profile_for_candidates()
    profile["advanced"]["line_item_hints"]["service_id_preference"] = "leftmost_identifier"

    assert detect_candidate_rows(_lumen_rows(), profile) == []


def test_column_label_never_becomes_a_candidate_service_id():
    """A label candidate can never be satisfied: the model will not emit a row
    whose service_id is the word "Description". Each one keeps its chunk in the
    missing-candidate state, driving the strict-retry/split loop to the depth cap
    — the GTT storm (~75 sequential AI calls, 47 minutes, 6%-complete workbook).
    """
    hints = _lumen_profile_for_candidates()["advanced"]["line_item_hints"]
    candidates = [
        CandidateRow(row_id="p5.r10", page=5, service_id="347207739", evidence="ok"),
        CandidateRow(row_id="p5.r95", page=5, service_id="Description", evidence="label"),
        CandidateRow(row_id="p5.r96", page=5, service_id="  service id ", evidence="label"),
    ]

    kept = _reject_label_candidates(candidates, hints)

    assert [c.service_id for c in kept] == ["347207739"]


def test_label_rejection_is_a_no_op_without_declared_labels():
    candidates = [CandidateRow(row_id="p1.r1", page=1, service_id="Description", evidence="x")]

    assert _reject_label_candidates(candidates, {}) == candidates
