from decimal import Decimal

from stencil.extraction.layout import (
    BBox,
    LayoutCell,
    LayoutDocument,
    LayoutPage,
    PageTextScan,
    PDFTextScan,
    VisualRow,
)
from stencil.extraction.plan_executor import (
    execute_extraction_plan,
    resolve_plan_page_numbers,
)
from stencil.profiles.plan import effective_extraction_plan
from stencil.profiles.schema import (
    ExtractionPlan,
    ReconciliationRule,
    RegionRule,
    RowSelectorRule,
    SupplierProfile,
    ValueExpression,
)
from stencil.profiles.validation import grade_authoring_evidence


def _row(page: int, index: int, text: str, values: list[str]) -> VisualRow:
    cells = [
        LayoutCell(
            text=value,
            bbox=BBox(x0=column * 100, y0=0, x1=column * 100 + 90, y1=10),
            row_index=index,
            column_index=column,
        )
        for column, value in enumerate(values)
    ]
    return VisualRow(
        row_id=f"p{page}.r{index}",
        page=page,
        row_index=index,
        text=text,
        bbox=BBox(x0=0, y0=0, x1=400, y1=10),
        cells=cells,
    )


def test_plan_resolves_bounded_pages_and_executes_formula():
    plan = ExtractionPlan(
        document_family="time_and_material",
        regions=[RegionRule(start_markers=["ANNEXURE"], end_markers=["GRAND TOTAL"])],
        row_selector=RowSelectorRule(include_pattern=r"EMP\d+", identifier_pattern=r"EMP\d+"),
        row_field_rules={
            "service_id": ValueExpression(
                op="regex_extract",
                pattern=r"(EMP\d+)",
                args=[ValueExpression(op="row_text")],
            ),
            "quantity": ValueExpression(op="row_column", value=1),
            "unit_rate": ValueExpression(op="row_column", value=2),
            "amount": ValueExpression(op="row_column", value=3),
        },
        reconciliation_rules=[ReconciliationRule(
            name="quantity x rate",
            left=ValueExpression(op="multiply", args=[
                ValueExpression(op="field", field="quantity"),
                ValueExpression(op="field", field="unit_rate"),
            ]),
            right=ValueExpression(op="field", field="amount"),
        )],
    )
    scan = PDFTextScan(
        page_count=4,
        text_chars=40,
        pages=[
            PageTextScan(page_number=1, text="header", text_chars=6),
            PageTextScan(page_number=2, text="ANNEXURE EMP1", text_chars=13),
            PageTextScan(page_number=3, text="EMP2", text_chars=4),
            PageTextScan(page_number=4, text="GRAND TOTAL", text_chars=11),
        ],
    )
    assert resolve_plan_page_numbers(plan, scan) == [2, 3]

    rows = [
        _row(2, 1, "ANNEXURE", ["ANNEXURE"]),
        _row(2, 2, "EMP1 2 50 100", ["EMP1", "2", "50", "100"]),
        _row(3, 1, "EMP2 4 25 100", ["EMP2", "4", "25", "100"]),
        _row(4, 1, "GRAND TOTAL", ["GRAND TOTAL"]),
    ]
    layout = LayoutDocument(pages=[
        LayoutPage(page_number=page, width=500, height=800, visual_rows=[r for r in rows if r.page == page])
        for page in (2, 3, 4)
    ])
    result = execute_extraction_plan(plan, layout)
    assert [row["service_id"] for row in result.rows] == ["EMP1", "EMP2"]
    assert result.rows[0]["amount"] == Decimal("100")
    assert result.reconciliation_failures == []


def test_legacy_profile_compiles_to_in_memory_plan():
    profile = SupplierProfile.model_validate({
        "profile_id": "legacy.v1",
        "identity": {"canonical_name": "Legacy"},
        "classification": {"output_type": "wireless"},
        "advanced": {
            "document_structure": {"detail_start_marker": "DETAIL"},
            "line_item_hints": {
                "service_id_value_pattern": r"\d{10}",
                "amount_column_label": "Total",
            },
        },
    })
    assert profile.advanced.extraction_plan is None
    plan = effective_extraction_plan(profile)
    assert plan.document_family == "wireless"
    assert plan.row_selector.identifier_pattern == r"\d{10}"
    assert profile.advanced.extraction_plan is None


def test_evidence_policy_never_verifies_invoice_only_and_fails_variance():
    invoice_only = grade_authoring_evidence(
        evidence_level="invoice_only",
        sample_results=[{"reconciled": True, "reconciliation_variance": 0}],
        category_confidence=0.9,
        engine_version="1.0",
    )
    assert invoice_only.status == "review_required"

    failed = grade_authoring_evidence(
        evidence_level="paired_blueprint",
        sample_results=[{
            "reconciled": False,
            "reconciliation_variance": "0.02",
            "output_diff_count": 0,
        }],
        category_confidence=0.9,
        engine_version="1.0",
    )
    assert failed.status == "failed"


def test_plan_parses_french_decimal_comma():
    plan = ExtractionPlan(
        row_selector=RowSelectorRule(include_pattern="total sous-compte"),
        row_field_rules={"amount": ValueExpression(op="row_column", value=1)},
    )
    row = _row(1, 1, "total sous-compte : 804284207 38,65", ["804284207", "38,65"])
    layout = LayoutDocument(pages=[
        LayoutPage(page_number=1, width=500, height=800, visual_rows=[row]),
    ])

    result = execute_extraction_plan(plan, layout)

    assert result.rows[0]["amount"] == Decimal("38.65")
