"""Only a missing amount may drop a line item.

Regression for the euNetworks IE-SI54403 failure: the AI marked
billing_period_start required (every item on the training invoice had one),
then two one-off task items without billing periods were silently dropped —
950.00 missing from the delivered total.
"""

from decimal import Decimal

from stencil.extraction.layout import BBox, LayoutCell, LayoutDocument, LayoutPage, VisualRow
from stencil.models.authoring import build_model_from_rules
from stencil.models.interpreter import execute_model
from stencil.models.schema import (
    ExtractionModel,
    FieldSource,
    FieldTransform,
    GroupingRule,
    ItemFieldRule,
    RegionRule,
    RowClassifier,
    RowMatch,
)
from stencil.profiles.schema import (
    ClassificationSignals,
    SupplierIdentity,
    SupplierProfile,
)


def _cell(text: str, x0: float, x1: float, row_index: int, column_index: int) -> LayoutCell:
    return LayoutCell(
        text=text,
        bbox=BBox(x0=x0, y0=0, x1=x1, y1=10),
        normalized_bbox=BBox(x0=x0, y0=0, x1=x1, y1=12),
        row_index=row_index,
        column_index=column_index,
    )


def _row(page: int, index: int, *cells: tuple[str, float, float]) -> VisualRow:
    layout_cells = [_cell(text, x0, x1, index, ci) for ci, (text, x0, x1) in enumerate(cells)]
    return VisualRow(
        row_id=f"p{page}.r{index}",
        page=page,
        row_index=index,
        text=" ".join(text for text, _x0, _x1 in cells),
        bbox=BBox(x0=0, y0=index * 10, x1=1000, y1=index * 10 + 8),
        cells=layout_cells,
    )


def _document() -> LayoutDocument:
    page = LayoutPage(
        page_id="p1", page_number=1, width=595, height=842,
        visual_rows=[
            # Item WITH a billing period.
            _row(1, 0, ("S00000001", 30, 130)),
            _row(1, 1, ("01/02/2026 - 28/02/2026", 30, 200)),
            _row(1, 2, ("Wave service", 30, 200), ("700.00", 800, 900)),
            # One-off item WITHOUT a billing period (per-task work).
            _row(1, 3, ("S00000002", 30, 130)),
            _row(1, 4, ("Per Task remote hands", 30, 250), ("250.00", 800, 900)),
        ],
    )
    return LayoutDocument(pages=[page])


def _model() -> ExtractionModel:
    return ExtractionModel(
        model_id="m", supplier_profile_id="m", supplier="Test",
        region=RegionRule(columns=[
            {"name": "service", "x0": 0, "x1": 300},
            {"name": "net_value", "x0": 700, "x1": 1000},
        ]),
        row_classifiers=[
            RowClassifier(role="service_start", where=RowMatch(column="service", pattern=r"^S\d{8}$")),
            RowClassifier(role="period", where=RowMatch(column="service", pattern=r"^\d{2}/\d{2}/\d{4}")),
            RowClassifier(role="amount_row", where=RowMatch(has_amount_in_column="net_value")),
        ],
        grouping=GroupingRule(mode="role_transition", start_role="service_start",
                              emit="one_item_per_role_row", emit_role="amount_row"),
        item_fields=[
            ItemFieldRule(name="service_id", required=True,
                          source=FieldSource(rows="role", row_role="service_start", column="service")),
            ItemFieldRule(name="amount", required=True,
                          source=FieldSource(rows="emit_row", column="net_value"),
                          transform=FieldTransform(type="currency")),
            # The trap: required billing period, absent on the one-off item.
            ItemFieldRule(name="billing_period_start", required=True,
                          source=FieldSource(rows="role", row_role="period", column="service",
                                             pattern=r"(\d{2}/\d{2}/\d{4})"),
                          transform=FieldTransform(type="date", date_format="%d/%m/%Y")),
        ],
    )


def test_missing_required_auxiliary_field_does_not_drop_the_item():
    invoice = execute_model(_model(), pdf_path=None, intake_id="t", document=_document())
    amounts = sorted(item.amount for item in invoice.line_items)
    assert amounts == [Decimal("250.00"), Decimal("700.00")]

    one_off = next(i for i in invoice.line_items if i.amount == Decimal("250.00"))
    assert one_off.service_id == "S00000002"
    assert one_off.billing_period_start is None


def test_missing_amount_still_drops_the_row():
    model = _model()
    document = _document()
    # Make the one-off row's amount cell unparseable -> no amount -> dropped.
    document.pages[0].visual_rows[4].cells[1].text = "TBD"
    document.pages[0].visual_rows[4].text = "Per Task remote hands TBD"
    invoice = execute_model(model, pdf_path=None, intake_id="t", document=document)
    assert [item.amount for item in invoice.line_items] == [Decimal("700.00")]


def test_authoring_clamps_required_to_amount_only():
    profile = SupplierProfile(
        profile_id="p.v1",
        identity=SupplierIdentity(canonical_name="Test"),
        classification=ClassificationSignals(output_type="standard"),
    )
    raw = {
        "item_fields": [
            {"name": "amount", "source": None, "transform": {}, "literal": None,
             "same_as": None, "required": True},
            {"name": "billing_period_start", "source": None, "transform": {}, "literal": None,
             "same_as": None, "required": True},
            {"name": "quantity", "source": None, "transform": {}, "literal": None,
             "same_as": None, "required": True},
        ],
    }
    model = build_model_from_rules(
        raw, profile=profile, fingerprint="fp", layout_family_key=None, intake_id="i",
    )
    required = {rule.name: rule.required for rule in model.item_fields}
    assert required == {"amount": True, "billing_period_start": False, "quantity": False}
