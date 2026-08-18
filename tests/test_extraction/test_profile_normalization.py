from datetime import date
from decimal import Decimal
from pathlib import Path

from stencil.extraction.layout import (
    BBox,
    LayoutCell,
    LayoutDocument,
    LayoutPage,
    VisualRow,
    extract_layout_document,
)
from stencil.extraction.normalization import (
    _apply_value_pattern,
    _child_service_id_by_row,
    _value_from_group_pattern,
    apply_layout_profile_hints,
)
from stencil.output.xlsx_writer import build_output_rows
from stencil.profiles.schema import LineItemHints, SupplierProfile
from stencil.validation.schema import (
    SYNTHETIC_INVOICE_DATE_WARNING,
    CanonicalInvoice,
    ChargeType,
    ExtractionMetadata,
    ExtractionPath,
    InvoiceHeader,
    LineItem,
)

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "corpus" / "colt.standard"


def test_value_patterns_without_capture_groups_do_not_crash():
    assert _apply_value_pattern("Circuit ABC-123", r"ABC-\d+") == "ABC-123"
    assert _apply_value_pattern("Circuit ABC-123", r"Circuit ([A-Z]+-\d+)") == "ABC-123"
    assert _apply_value_pattern("Circuit ABC-123", r"(") == "Circuit ABC-123"


def test_group_value_patterns_without_capture_groups_do_not_crash():
    group = [_row("p1.r1", 1, "detail", "Service ABC-123 charge", [])]

    assert _value_from_group_pattern(group, r"ABC-\d+") == "ABC-123"
    assert _value_from_group_pattern(group, r"Service ([A-Z]+-\d+)") == "ABC-123"
    assert _value_from_group_pattern(group, r"(") is None


def test_profile_document_field_defaults_override_extracted_values():
    invoice = _invoice()
    invoice.fields["account_number"] = "000027731"
    profile = SupplierProfile.model_validate({
        "profile_id": "ltm.test",
        "identity": {"canonical_name": "LTM Limited"},
        "advanced": {
            "document_field_defaults": {"account_number": "MindTreeTimeMaterial"},
        },
    })

    apply_layout_profile_hints(invoice, LayoutDocument(pages=[]), profile)

    assert invoice.fields["account_number"] == "MindTreeTimeMaterial"


def test_per_total_group_profile_normalizes_child_ai_rows_to_parent_total_rows(corpus_profile):
    invoice = CanonicalInvoice(
        intake_id="i1",
        header=InvoiceHeader(
            supplier_name="Colt",
            invoice_number="708205451/605959",
            invoice_date=date(2024, 10, 1),
            due_date=date(2024, 10, 31),
            account_number="5-HLBHFCGL",
        ),
        line_items=[
            _item(1, "336619691", "442117492", "3996.34"),
            _item(2, "336673851", "442135798", "3716.60"),
            _item(3, "348171502", "446036137", "956.00"),
            _item(4, "348171503", "446036137", "130.76"),
            _item(5, "348702717", "446207898", "1802.00"),
            _item(6, "348702720", "446207899", "1802.00"),
            LineItem(
                line_number=7,
                service_id=None,
                billing_reference=None,
                description="Total VAT",
                charge_type=ChargeType.TAX,
                amount=Decimal("2852.85"),
            ),
        ],
        current_charges=Decimal("12403.70"),
        tax=Decimal("2852.85"),
        tax_rate=Decimal("0.23"),
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )

    # The live Colt profile is now child-level (per_charge_row); this test still
    # exercises the per_total_group PARENT-collapse path via an explicit override.
    base = corpus_profile("colt.standard")
    hints = base.line_item_hints.model_copy(update={
        "line_item_granularity": "per_total_group",
        "service_id_preference": "total_row_identifier",
    })
    profile = base.model_copy(update={"line_item_hints": hints})
    document = extract_layout_document(CORPUS_ROOT / "invoices" / "708205451_20241001.pdf")

    apply_layout_profile_hints(invoice, document, profile, hints)

    service_rows = [item for item in invoice.line_items if item.charge_type != ChargeType.TAX]
    assert [item.service_id for item in service_rows] == [
        "442117492",
        "442135798",
        "446036137",
        "446207898",
        "446207899",
    ]
    assert [item.billing_reference for item in service_rows] == [item.service_id for item in service_rows]
    assert sum((item.amount for item in service_rows), Decimal("0")) == Decimal("12403.70")


def test_profile_hints_can_preserve_compact_ai_line_items(corpus_profile):
    invoice = CanonicalInvoice(
        intake_id="i1",
        header=InvoiceHeader(
            supplier_name="Colt",
            invoice_number="708205451/605959",
            invoice_date=date(2024, 10, 1),
            account_number="5-HLBHFCGL",
        ),
        line_items=[
            _item(1, "336619691", "442117492", "3996.34"),
            _item(2, "336673851", "442135798", "3716.60"),
            _item(3, "348171502", "446036137", "956.00"),
        ],
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )
    base = corpus_profile("colt.standard")
    hints = base.line_item_hints.model_copy(update={
        "line_item_granularity": "per_total_group",
        "service_id_preference": "total_row_identifier",
    })
    profile = base.model_copy(update={"line_item_hints": hints})
    document = extract_layout_document(CORPUS_ROOT / "invoices" / "708205451_20241001.pdf")

    apply_layout_profile_hints(invoice, document, profile, hints, preserve_existing_line_items=True)

    assert [item.service_id for item in invoice.line_items] == ["336619691", "336673851", "348171502"]
    assert [item.amount for item in invoice.line_items] == [Decimal("3996.34"), Decimal("3716.60"), Decimal("956.00")]


def test_table_total_row_profile_rebuilds_missing_compact_rows_from_layout():
    invoice = CanonicalInvoice(
        intake_id="i1",
        header=InvoiceHeader(
            supplier_name="Lumen",
            invoice_number="788263904",
            invoice_date=date(2026, 6, 1),
            account_number="1-C212CJ",
            currency="USD",
        ),
        line_items=[_item(1, "441101218", "441101218", "608.48")],
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )
    hints = LineItemHints(
        detail_table_anchors=["SERVICE LEVEL ACTIVITY"],
        line_item_granularity="per_total_group",
        service_id_preference="total_row_identifier",
        billing_reference_preference="same_as_service_id",
        amount_source="table_charges_column",
        tax_source="table_tax_column",
    )
    document = LayoutDocument(pages=[
        LayoutPage(
            page_id="p7",
            page_number=7,
            width=800,
            height=600,
            visual_rows=[
                _row("p7.r0", 0, "anchor", "SERVICE LEVEL ACTIVITY", [_cell("SERVICE LEVEL ACTIVITY", "text")]),
                _row("p7.r1", 1, "service_start", "441101218", [_cell("441101218", "identifier")]),
                _row(
                    "p7.r2",
                    2,
                    "period_amount",
                    "Ethernet 01 Jun 2026 - 30 Jun 2026 608.48",
                    [
                        _cell("Ethernet", "text"),
                        _cell("01 Jun 2026 - 30 Jun 2026", "billing_period"),
                        _cell("608.48", "amount"),
                    ],
                ),
                _row(
                    "p7.r3",
                    3,
                    "group_total",
                    "Total 441101218 608.48 393.12 1,001.60 441209888",
                    [
                        _cell("Total 441101218", "summary"),
                        _cell("608.48", "amount"),
                        _cell("393.12", "amount"),
                        _cell("1,001.60", "amount"),
                        _cell("441209888", "identifier"),
                    ],
                ),
                _row("p7.r4", 4, "service_start", "441209888", [_cell("441209888", "identifier")]),
                _row(
                    "p7.r5",
                    5,
                    "period_amount",
                    "Ethernet 01 Jun 2026 - 30 Jun 2026 1,727.21",
                    [
                        _cell("Ethernet", "text"),
                        _cell("01 Jun 2026 - 30 Jun 2026", "billing_period"),
                        _cell("1,727.21", "amount"),
                    ],
                ),
                _row(
                    "p7.r6",
                    6,
                    "group_total",
                    "Total 441209888 1,727.21 120.98 1,848.19",
                    [
                        _cell("Total 441209888", "summary"),
                        _cell("1,727.21", "amount"),
                        _cell("120.98", "amount"),
                        _cell("1,848.19", "amount"),
                    ],
                ),
            ],
        )
    ])

    apply_layout_profile_hints(invoice, document, None, hints, preserve_existing_line_items=True)

    assert [item.service_id for item in invoice.line_items] == ["441101218", "441209888"]
    assert [item.billing_reference for item in invoice.line_items] == ["441101218", "441209888"]
    assert [item.amount for item in invoice.line_items] == [Decimal("608.48"), Decimal("1727.21")]
    assert [item.tax_amount for item in invoice.line_items] == [Decimal("393.12"), Decimal("120.98")]


def test_colt_subtotal_group_with_multiple_services_expands_to_service_rows(corpus_profile):
    invoice = CanonicalInvoice(
        intake_id="i1",
        header=InvoiceHeader(
            supplier_name="Colt",
            invoice_number="740813578",
            invoice_date=date(2025, 6, 24),
            account_number="0205324323",
        ),
        line_items=[
            _item(1, "0205324323", "0205324323", "1191.86"),
            LineItem(
                line_number=2,
                service_id=None,
                billing_reference=None,
                description="Total BTW",
                charge_type=ChargeType.TAX,
                amount=Decimal("250.29"),
            ),
        ],
        subtotal=Decimal("1191.86"),
        tax=Decimal("250.29"),
        tax_rate=Decimal("0.21"),
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )
    profile = corpus_profile("colt.standard")
    document = LayoutDocument(
        pages=[
            LayoutPage(
                page_id="p5",
                page_number=5,
                width=800,
                height=600,
                visual_rows=[
                    _row("p5.r5", 5, "anchor", "SERVICE LEVEL ACTIVITY", [_cell("SERVICE LEVEL ACTIVITY", "text")]),
                    _row(
                        "p5.r10",
                        10,
                        "service_start",
                        "FRO2006256138 DEDICATED INTERNET",
                        [_cell("FRO2006256138", "identifier"), _cell("DEDICATED INTERNET", "text")],
                    ),
                    _row(
                        "p5.r12",
                        12,
                        "period_amount",
                        "DIA COMMIT 24 Jun 2025 - 23 Jul 2025 1 348.56",
                        [
                            _cell("DIA COMMIT", "text"),
                            _cell("24 Jun 2025 - 23 Jul 2025", "billing_period"),
                            _cell("1", "amount"),
                            _cell("348.56", "amount"),
                        ],
                    ),
                    _row(
                        "p5.r15",
                        15,
                        "service_start",
                        "FRO2007419582GIG DEDICATED INTERNET",
                        [_cell("FRO2007419582GIG", "identifier"), _cell("DEDICATED INTERNET", "text")],
                    ),
                    _row(
                        "p5.r17",
                        17,
                        "period_amount",
                        "DIA LOCAL ACCESS 24 Jun 2025 - 23 Jul 2025 1 843.30",
                        [
                            _cell("DIA LOCAL ACCESS", "text"),
                            _cell("24 Jun 2025 - 23 Jul 2025", "billing_period"),
                            _cell("1", "amount"),
                            _cell("843.30", "amount"),
                        ],
                    ),
                    _row(
                        "p5.r18",
                        18,
                        "group_total",
                        "Total AMSTERDAM, NL 1,191.86",
                        [_cell("Total AMSTERDAM, NL", "summary"), _cell("1,191.86", "amount")],
                    ),
                ],
            )
        ]
    )

    apply_layout_profile_hints(invoice, document, profile)

    service_rows = [item for item in invoice.line_items if item.charge_type != ChargeType.TAX]
    assert [item.service_id for item in service_rows] == ["FRO2006256138", "FRO2007419582GIG"]
    assert [item.billing_reference for item in service_rows] == [item.service_id for item in service_rows]
    assert [item.amount for item in service_rows] == [Decimal("348.56"), Decimal("843.30")]
    assert len(build_output_rows(invoice)) == 2


def test_per_charge_row_profile_uses_layout_dates_and_only_real_tax_rows(corpus_profile):
    invoice = CanonicalInvoice(
        intake_id="i1",
        header=InvoiceHeader(
            supplier_name="GTT",
            invoice_number="INV8852316",
            invoice_date=date(2024, 3, 1),
            account_number="T171404",
        ),
        line_items=[
            LineItem(
                line_number=1,
                service_id="GTT/002153160",
                billing_reference="GTT/002153160",
                description="AI hallucinated stale period/tax",
                charge_type=ChargeType.RECURRING,
                amount=Decimal("1284.81"),
                tax_amount=Decimal("0.24"),
                billing_period_start=date(2024, 3, 1),
                billing_period_end=date(2024, 3, 30),
            )
        ],
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )
    profile = corpus_profile("gtt.standard")
    document = LayoutDocument(
        pages=[
            LayoutPage(
                page_id="p1",
                page_number=1,
                width=800,
                height=600,
                visual_rows=[
                    _row("p1.r0", 0, "anchor", "Detail of Charges", [_cell("Detail of Charges", "text")]),
                    _row(
                        "p1.r1",
                        1,
                        "service_start",
                        "GTT/002153160 2010824-2172668",
                        [_cell("GTT/002153160", "identifier"), _cell("2010824-2172668", "identifier")],
                    ),
                    _row(
                        "p1.r2",
                        2,
                        "period_amount",
                        "1832405-5663543 Ethernet 01-Apr-24 30-Apr-24 MRC 1 $1,284.81 $1,284.81",
                        [
                            _cell("1832405-5663543", "identifier"),
                            _cell("01-Apr-24 30-Apr-24 MRC", "billing_period"),
                            _cell("1", "amount"),
                            _cell("$1,284.81", "amount"),
                            _cell("$1,284.81", "amount"),
                        ],
                    ),
                    _row(
                        "p1.r3",
                        3,
                        "detail",
                        "Cost Recovery Surcharge Surcharge $192.08",
                        [
                            _cell("Cost Recovery Surcharge", "text"),
                            _cell("Surcharge", "text"),
                            _cell("$192.08", "amount"),
                        ],
                    ),
                    _row(
                        "p1.r4",
                        4,
                        "group_total",
                        "Subtotal $1,476.89",
                        [_cell("Subtotal", "summary"), _cell("$1,476.89", "amount")],
                    ),
                    _row(
                        "p1.r5",
                        5,
                        "service_start",
                        "GTT/002352515 1845575-1996481",
                        [_cell("GTT/002352515", "identifier"), _cell("1845575-1996481", "identifier")],
                    ),
                    _row(
                        "p1.r6",
                        6,
                        "period_amount",
                        "1996481-6127452 Dedicated Internet Access 01-Apr-24 30-Apr-24 MRC 1 $1,050.00 $1,050.00",
                        [
                            _cell("1996481-6127452", "identifier"),
                            _cell("01-Apr-24 30-Apr-24 MRC", "billing_period"),
                            _cell("1", "amount"),
                            _cell("$1,050.00", "amount"),
                            _cell("$1,050.00", "amount"),
                        ],
                    ),
                    _row(
                        "p1.r7",
                        7,
                        "tax_total",
                        "P.U.C. Fee Tax $7.32",
                        [_cell("P.U.C. Fee", "text"), _cell("Tax", "text"), _cell("$7.32", "amount")],
                    ),
                    _row(
                        "p1.r8",
                        8,
                        "group_total",
                        "Subtotal $1,057.32",
                        [_cell("Subtotal", "summary"), _cell("$1,057.32", "amount")],
                    ),
                ],
            )
        ]
    )

    apply_layout_profile_hints(invoice, document, profile)

    by_service = {item.service_id: item for item in invoice.line_items}
    assert by_service["GTT/002153160"].billing_period_start == date(2024, 4, 1)
    assert by_service["GTT/002153160"].billing_period_end == date(2024, 4, 30)
    assert by_service["GTT/002153160"].tax_amount is None
    assert by_service["GTT/002352515"].tax_amount == Decimal("7.32")


def test_zayo_labeled_service_blocks_emit_delivered_service_order_rows():
    invoice = _invoice()
    hints = LineItemHints(
        line_item_granularity="per_service_group",
        service_id_column_label="Service Order Number",
        billing_reference_column_label="Description / Speed",
        amount_column_label="Current Charges for",
        tax_amount_column_label="Taxes",
        amount_source="label_amount",
        tax_source="label_amount",
    )
    document = LayoutDocument(pages=[LayoutPage(
        page_id="p1",
        page_number=1,
        width=800,
        height=600,
        visual_rows=[
            _row("p1.r1", 1, "detail", "Service Order Number 2578205 MRC: $1,000.00", [
                _cell("Service Order Number", "text"), _cell("2578205", "identifier"), _cell("$1,000.00", "amount"),
            ]),
            _row("p1.r2", 2, "detail", "Description / Speed Dark Fiber - Point to Point Other Fees: $82.42", [
                _cell("Description / Speed", "text"), _cell("Dark Fiber - Point to Point", "text"),
                _cell("$82.42", "amount"),
            ]),
            _row("p1.r3", 3, "group_total", "Current Charges for FBDK123 $1,082.42", [
                _cell("Current Charges for FBDK123", "summary"), _cell("$1,082.42", "amount"),
            ]),
            _row("p1.r4", 4, "detail", "Service Order Number 2383781 MRC: $250.00", [
                _cell("Service Order Number", "text"), _cell("2383781", "identifier"), _cell("$250.00", "amount"),
            ]),
            _row("p1.r5", 5, "detail", "Description / Speed 10G Taxes: $12.50", [
                _cell("Description / Speed", "text"), _cell("10G", "text"), _cell("$12.50", "amount"),
            ]),
            _row("p1.r6", 6, "group_total", "Current Charges for ABCD456 $250.00", [
                _cell("Current Charges for ABCD456", "summary"), _cell("$250.00", "amount"),
            ]),
        ],
    )])

    apply_layout_profile_hints(invoice, document, None, hints)

    assert [(item.service_id, item.billing_reference, item.amount, item.tax_amount) for item in invoice.line_items] == [
        ("2578205", "Dark Fiber - Point to Point", Decimal("1082.42"), None),
        ("2383781", "10G", Decimal("250.00"), Decimal("12.50")),
    ]


def test_zayo_labeled_service_block_can_net_tax_from_current_charges():
    invoice = _invoice()
    hints = LineItemHints(
        line_item_granularity="per_service_group",
        service_id_column_label="Service Order Number",
        billing_reference_column_label="Description / Speed",
        amount_column_label="Current Charges for",
        tax_amount_column_label="Taxes",
        amount_source="label_amount_minus_tax",
        tax_source="label_amount",
    )
    document = LayoutDocument(pages=[LayoutPage(
        page_id="p1",
        page_number=1,
        width=800,
        height=600,
        visual_rows=[
            _row("p1.r1", 1, "detail", "Service Order Number 2253862 MRC: $1,200.00", [
                _cell("Service Order Number", "text"), _cell("2253862", "identifier"), _cell("$1,200.00", "amount"),
            ]),
            _row("p1.r2", 2, "detail", "Description / Speed 10G Other Fees: $102.36", [
                _cell("Description / Speed", "text"), _cell("10G", "text"), _cell("$102.36", "amount"),
            ]),
            _row("p1.r3", 3, "tax_total", "Term 24 Taxes: $553.02", [
                _cell("Term", "text"), _cell("24", "amount"), _cell("Taxes:", "summary"), _cell("$553.02", "amount"),
            ]),
            _row("p2.r4", 4, "section_total", "Current Charges for OGYX/379464//ZYO $1,855.38", [
                _cell("Current Charges for OGYX/379464//ZYO", "summary"), _cell("$1,855.38", "amount"),
            ]),
        ],
    )])

    apply_layout_profile_hints(invoice, document, None, hints)

    assert len(invoice.line_items) == 1
    item = invoice.line_items[0]
    assert item.service_id == "2253862"
    assert item.billing_reference == "10G"
    assert item.amount == Decimal("1302.36")
    assert item.tax_amount == Decimal("553.02")


def test_att_circuit_total_group_uses_service_total_and_tax_difference_without_ai_rows():
    invoice = _invoice()
    hints = LineItemHints(
        line_item_granularity="per_total_group",
        service_id_preference="first_identifier",
        billing_reference_preference="same_as_service_id",
        amount_column_label="Total Ethernet",
        amount_source="label_amount",
        tax_source="group_total_minus_amount",
        service_id_value_pattern=r"Circuit #([A-Z0-9]+)",
    )
    profile = _profile(
        detail_start_marker="Charges for Circuit",
        detail_end_marker="Total Circuit",
        hints=hints,
    )
    document = LayoutDocument(pages=[LayoutPage(
        page_id="p1",
        page_number=1,
        width=800,
        height=600,
        visual_rows=[
            _row("p1.r1", 1, "service_start", "Charges for Circuit #BFEC565162 ATI", [
                _cell("BFEC565162", "identifier"),
            ]),
            _row("p1.r2", 2, "period_amount", "Ethernet Access MRC 1,829.54", [
                _cell("Ethernet Access MRC", "text"), _cell("1,829.54", "amount"),
            ]),
            _row("p1.r3", 3, "period_amount", "Total Ethernet Service 1,910.18", [
                _cell("Total Ethernet Service", "summary"), _cell("1,910.18", "amount"),
            ]),
            _row("p1.r4", 4, "tax_total", "Total Taxes 80.64", [
                _cell("Total Taxes", "summary"), _cell("80.64", "amount"),
            ]),
            _row("p1.r5", 5, "group_total", "Total Circuit #BFEC565162 ATI 3,306.04", [
                _cell("Total Circuit #BFEC565162 ATI", "summary"), _cell("3,306.04", "amount"),
            ]),
        ],
    )])

    apply_layout_profile_hints(invoice, document, profile)

    assert len(invoice.line_items) == 1
    item = invoice.line_items[0]
    assert item.service_id == "BFEC565162"
    assert item.billing_reference == "BFEC565162"
    assert item.amount == Decimal("1910.18")
    assert item.tax_amount == Decimal("1395.86")


def test_att_circuit_marker_groups_ignore_inner_group_totals_for_tax_difference():
    invoice = _invoice()
    profile = _profile(
        detail_start_marker="Charges for Circuit",
        detail_end_marker="Total Circuit",
        hints=LineItemHints(
            line_item_granularity="per_total_group",
            service_id_preference="first_identifier",
            billing_reference_preference="same_as_service_id",
            amount_column_label="Total Ethernet",
            amount_source="label_amount",
            tax_source="group_total_minus_amount",
            service_id_value_pattern=r"Circuit #([A-Z0-9]+)",
        ),
    )
    document = LayoutDocument(pages=[LayoutPage(
        page_id="p1",
        page_number=1,
        width=800,
        height=600,
        visual_rows=[
            _row("p1.r1", 1, "anchor", "Charges for Circuit #BFEC565162 ATI", [
                _cell("BFEC565162", "identifier"),
            ]),
            _row("p1.r2", 2, "section_total", "Total Ethernet Service 1,910.18", [
                _cell("Total Ethernet Service", "summary"), _cell("1,910.18", "amount"),
            ]),
            _row("p1.r3", 3, "group_total", "Total Surcharges and Other Fees 1,315.22", [
                _cell("Total Surcharges and Other Fees", "summary"), _cell("1,315.22", "amount"),
            ]),
            _row("p1.r4", 4, "group_total", "Total Taxes 80.64", [
                _cell("Total Taxes", "summary"), _cell("80.64", "amount"),
            ]),
            _row("p1.r5", 5, "group_total", "Total Circuit #BFEC565162 ATI 3,306.04", [
                _cell("Total Circuit #BFEC565162 ATI", "summary"), _cell("3,306.04", "amount"),
            ]),
        ],
    )])

    apply_layout_profile_hints(invoice, document, profile)

    assert len(invoice.line_items) == 1
    assert invoice.line_items[0].amount == Decimal("1910.18")
    assert invoice.line_items[0].tax_amount == Decimal("1395.86")


def test_att_circuit_total_ethernet_variant_uses_amount_cell_not_label_number():
    invoice = _invoice()
    profile = _profile(
        detail_start_marker="Charges for Circuit",
        detail_end_marker="Total Circuit",
        hints=LineItemHints(
            line_item_granularity="per_total_group",
            service_id_preference="first_identifier",
            billing_reference_preference="same_as_service_id",
            amount_column_label="Total Ethernet",
            amount_source="label_amount",
            tax_source="group_total_minus_amount",
            service_id_value_pattern=r"Circuit #([A-Z0-9]+)",
        ),
    )
    document = LayoutDocument(pages=[LayoutPage(
        page_id="p1",
        page_number=1,
        width=800,
        height=600,
        visual_rows=[
            _row("p1.r1", 1, "anchor", "Charges for Circuit #L4YS595327 ATI", [
                _cell("L4YS595327", "identifier"),
            ]),
            _row("p1.r2", 2, "group_total", "Total Ethernet 10 Gbps Basic Service 173.95", [
                _cell("Total Ethernet 10 Gbps Basic Service", "summary"), _cell("173.95", "amount"),
            ]),
            _row("p1.r3", 3, "group_total", "Total Surcharges and Other Fees 119.76", [
                _cell("Total Surcharges and Other Fees", "summary"), _cell("119.76", "amount"),
            ]),
            _row("p1.r4", 4, "group_total", "Total Taxes 14.70", [
                _cell("Total Taxes", "summary"), _cell("14.70", "amount"),
            ]),
            _row("p1.r5", 5, "detail", "Total Circuit #L4YS595327 ATI 308.41", [
                _cell("Total Circuit #L4YS595327 ATI", "summary"), _cell("308.41", "amount"),
            ]),
        ],
    )])

    apply_layout_profile_hints(invoice, document, profile)

    assert len(invoice.line_items) == 1
    assert invoice.line_items[0].service_id == "L4YS595327"
    assert invoice.line_items[0].amount == Decimal("173.95")
    assert invoice.line_items[0].tax_amount == Decimal("134.46")


def test_rogers_marker_group_uses_split_id_plan_and_total_before_taxes():
    invoice = _invoice()
    profile = _profile(
        detail_start_marker="PDFSPLITSTART",
        detail_end_marker="Total for Wireless",
        hints=LineItemHints(
            line_item_granularity="per_total_group",
            service_id_preference="first_identifier",
            amount_column_label="Total before taxes",
            amount_source="label_amount",
            tax_source="group_total_minus_amount",
            service_id_value_pattern=r"PDFSPLITSTART-[^_]+_(.+?)-PDFSPLITEND",
            billing_reference_value_pattern=r"(Wireless Bus\. Internet \d+)",
        ),
    )
    document = LayoutDocument(pages=[LayoutPage(
        page_id="p7",
        page_number=7,
        width=800,
        height=600,
        visual_rows=[
            _row("p7.r0", 0, "detail", "PDFSPLITSTART-5-0534-8514_100-502-0019-PDFSPLITEND", [
                _cell("PDFSPLITSTART-5-0534-8514_100-502-0019-PDFSPLITEND", "text"),
            ]),
            _row("p7.r1", 1, "detail", "Wireless Bus. Internet 50 159.99", [
                _cell("Wireless Bus. Internet 50", "text"), _cell("159.99", "amount"),
            ]),
            _row("p7.r2", 2, "period_amount", "Total monthly charges 99.99", [
                _cell("Total monthly charges", "summary"), _cell("99.99", "amount"),
            ]),
            _row("p7.r3", 3, "period_amount", "Total before taxes 99.99", [
                _cell("Total before taxes", "summary"), _cell("99.99", "amount"),
            ]),
            _row("p7.r4", 4, "tax_total", "GST 5.00", [_cell("GST", "text"), _cell("5.00", "amount")]),
            _row("p7.r5", 5, "group_total", "Total for Wireless 100-502-0019 $114.96", [
                _cell("Total for Wireless 100-502-0019", "summary"), _cell("$114.96", "amount"),
            ]),
        ],
    )])

    apply_layout_profile_hints(invoice, document, profile)

    assert len(invoice.line_items) == 1
    item = invoice.line_items[0]
    assert item.service_id == "100-502-0019"
    assert item.billing_reference == "Wireless Bus. Internet 50"
    assert item.amount == Decimal("99.99")
    assert item.tax_amount == Decimal("14.97")


def test_rogers_marker_group_extracts_5g_plan_name_and_tax_difference():
    invoice = _invoice()
    profile = _profile(
        detail_start_marker="PDFSPLITSTART",
        detail_end_marker="Total for Wireless",
        hints=LineItemHints(
            line_item_granularity="per_total_group",
            service_id_preference="first_identifier",
            amount_column_label="Total before taxes",
            amount_source="label_amount",
            tax_source="group_total_minus_amount",
            service_id_value_pattern=r"PDFSPLITSTART-[^_]+_(.+?)-PDFSPLITEND",
            billing_reference_value_pattern=r"(Wireless Bus\. Internet \d+|5G Business Internet Premium)",
        ),
    )
    document = LayoutDocument(pages=[LayoutPage(
        page_id="p7",
        page_number=7,
        width=800,
        height=600,
        visual_rows=[
            _row("p7.r0", 0, "detail", "PDFSPLITSTART-5-0579-9746_236-638-5480-PDFSPLITEND", [
                _cell("PDFSPLITSTART-5-0579-9746_236-638-5480-PDFSPLITEND", "text"),
            ]),
            _row("p7.r9", 9, "detail", "5G Business Internet Premium 90.00", [
                _cell("5G Business Internet Premium", "text"), _cell("90.00", "amount"),
            ]),
            _row("p7.r20", 20, "group_total", "Total before taxes 115.80 5G Business Internet Premium", [
                _cell("Total before taxes", "summary"), _cell("115.80", "amount"),
                _cell("5G Business Internet Premium", "text"),
            ]),
            _row("p7.r21", 21, "detail", "GST: 815781448 5.79", [
                _cell("GST: 815781448", "text"), _cell("5.79", "amount"),
            ]),
            _row("p7.r22", 22, "detail", "PST 8.10", [_cell("PST", "text"), _cell("8.10", "amount")]),
            _row("p7.r23", 23, "group_total", "Total for Wireless 236-638-5480 $129.69", [
                _cell("Total for Wireless 236-638-5480", "summary"), _cell("$129.69", "amount"),
            ]),
        ],
    )])

    apply_layout_profile_hints(invoice, document, profile)

    assert len(invoice.line_items) == 1
    item = invoice.line_items[0]
    assert item.service_id == "236-638-5480"
    assert item.billing_reference == "5G Business Internet Premium"
    assert item.amount == Decimal("115.80")
    assert item.tax_amount == Decimal("13.89")


def test_rogers_comma_marker_group_uses_credit_adjusted_total_before_taxes():
    invoice = _invoice()
    profile = _profile(
        detail_start_marker="PDFSPLITSTART",
        detail_end_marker="Total for Wireless,Total for Phone",
        hints=LineItemHints(
            line_item_granularity="per_total_group",
            service_id_preference="first_identifier",
            amount_column_label="Total before taxes",
            amount_source="label_amount",
            tax_source="group_total_minus_amount",
            tax_output_mode="extract_exact",
            service_id_value_pattern=r"PDFSPLITSTART-[^_]+_(.+?)-PDFSPLITEND",
            billing_reference_value_pattern=(
                r"(Wireless Bus\. Internet \d+|5G Business Internet Premium|"
                r"Rogers 5G Business Internet|Flex Int'l SMS Roaming)"
            ),
        ),
    )
    document = LayoutDocument(pages=[LayoutPage(
        page_id="p5",
        page_number=5,
        width=800,
        height=600,
        visual_rows=[
            _row("p5.r0", 0, "detail", "PDFSPLITSTART-5-0346-0352_416-294-0179-PDFSPLITEND", [
                _cell("PDFSPLITSTART-5-0346-0352_416-294-0179-PDFSPLITEND", "text"),
            ]),
            _row("p5.r9", 9, "detail", "Rogers 5G Business Internet 50.00", [
                _cell("Rogers 5G Business Internet", "text"), _cell("50.00", "amount"),
            ]),
            _row("p5.r11", 11, "detail", "Credit: Financing Program Promotion -12.59", [
                _cell("Credit: Financing Program Promotion", "text"), _cell("-12.59", "amount"),
            ]),
            _row("p5.r12", 12, "group_total", "Total monthly charges 37.41", [
                _cell("Total monthly charges", "summary"), _cell("37.41", "amount"),
            ]),
            _row("p5.r18", 18, "detail", "Total used 0.00", [
                _cell("Total used", "summary"), _cell("0.00", "amount"),
            ]),
            _row("p5.r20", 20, "group_total", "Total before taxes 37.41", [
                _cell("Total before taxes", "summary"), _cell("37.41", "amount"),
            ]),
            _row("p5.r22", 22, "group_total", "Total for Wireless 416-294-0179 $56.50", [
                _cell("Total for Wireless 416-294-0179", "summary"), _cell("$56.50", "amount"),
            ]),
            _row("p5.r23", 23, "detail", "Unless otherwise stated, usage detail follows", [
                _cell("Unless otherwise stated, usage detail follows", "text"),
            ]),
        ],
    )])

    apply_layout_profile_hints(invoice, document, profile)

    assert len(invoice.line_items) == 1
    item = invoice.line_items[0]
    assert item.service_id == "416-294-0179"
    assert item.billing_reference == "Rogers 5G Business Internet"
    assert item.amount == Decimal("37.41")
    assert item.tax_amount == Decimal("19.09")


def test_preserved_rogers_ai_rows_are_enriched_without_rebuilding():
    invoice = _invoice()
    ai_row = _item(
        1,
        "416-294-0179",
        "[p5.r9.c0 c0 text x=74-220] Rogers 5G Business Internet | [p5.r9.c1 amount]",
        "50.00",
    )
    ai_row.tax_amount = Decimal("4.86")
    unmatched_row = _item(2, "999-000-0000", "Unmatched AI row", "12.34")
    unmatched_row.tax_amount = Decimal("1.23")
    invoice.line_items = [ai_row, unmatched_row]
    profile = _profile(
        detail_start_marker="PDFSPLITSTART",
        detail_end_marker="Total for Wireless,Total for Phone",
        hints=LineItemHints(
            line_item_granularity="per_total_group",
            service_id_preference="first_identifier",
            amount_column_label="Total before taxes",
            amount_source="label_amount",
            tax_source="group_total_minus_amount",
            tax_output_mode="extract_exact",
            service_id_value_pattern=r"PDFSPLITSTART-[^_]+_(.+?)-PDFSPLITEND",
            billing_reference_value_pattern=(
                r"(Wireless Bus\. Internet \d+|5G Business Internet Premium|"
                r"Rogers 5G Business Internet|Flex Int'l SMS Roaming)"
            ),
        ),
    )
    document = LayoutDocument(pages=[LayoutPage(
        page_id="p5",
        page_number=5,
        width=800,
        height=600,
        visual_rows=[
            _row("p5.r0", 0, "detail", "PDFSPLITSTART-5-0346-0352_416-294-0179-PDFSPLITEND", [
                _cell("PDFSPLITSTART-5-0346-0352_416-294-0179-PDFSPLITEND", "text"),
            ]),
            _row("p5.r9", 9, "detail", "Rogers 5G Business Internet 50.00", [
                _cell("Rogers 5G Business Internet", "text"), _cell("50.00", "amount"),
            ]),
            _row("p5.r11", 11, "detail", "Credit: Financing Program Promotion -12.59", [
                _cell("Credit: Financing Program Promotion", "text"), _cell("-12.59", "amount"),
            ]),
            _row("p5.r20", 20, "group_total", "Total before taxes 37.41", [
                _cell("Total before taxes", "summary"), _cell("37.41", "amount"),
            ]),
            _row("p5.r22", 22, "group_total", "Total for Wireless 416-294-0179 $56.50", [
                _cell("Total for Wireless 416-294-0179", "summary"), _cell("$56.50", "amount"),
            ]),
        ],
    )])

    apply_layout_profile_hints(invoice, document, profile, preserve_existing_line_items=True)

    assert len(invoice.line_items) == 2
    assert invoice.line_items[0].service_id == "416-294-0179"
    assert invoice.line_items[0].billing_reference == "Rogers 5G Business Internet"
    assert invoice.line_items[0].amount == Decimal("37.41")
    assert invoice.line_items[0].tax_amount == Decimal("19.09")
    assert invoice.line_items[1].service_id == "999-000-0000"
    assert invoice.line_items[1].billing_reference == "Unmatched AI row"
    assert invoice.line_items[1].amount == Decimal("12.34")
    assert invoice.line_items[1].tax_amount == Decimal("1.23")


def test_rogers_flex_group_uses_total_before_taxes_and_footer_tax():
    invoice = _invoice()
    profile = _profile(
        detail_start_marker="PDFSPLITSTART",
        detail_end_marker="Total for Wireless,Total for Phone",
        hints=LineItemHints(
            line_item_granularity="per_total_group",
            service_id_preference="first_identifier",
            amount_column_label="Total before taxes",
            amount_source="label_amount",
            tax_source="group_total_minus_amount",
            service_id_value_pattern=r"PDFSPLITSTART-[^_]+_(.+?)-PDFSPLITEND",
            billing_reference_value_pattern=(
                r"(Wireless Bus\. Internet \d+|5G Business Internet Premium|"
                r"Rogers 5G Business Internet|Flex Int'l SMS Roaming)"
            ),
        ),
    )
    document = LayoutDocument(pages=[LayoutPage(
        page_id="p13",
        page_number=13,
        width=800,
        height=600,
        visual_rows=[
            _row("p13.r0", 0, "detail", "PDFSPLITSTART-5-0346-0352_437-332-6295-PDFSPLITEND", [
                _cell("PDFSPLITSTART-5-0346-0352_437-332-6295-PDFSPLITEND", "text"),
            ]),
            _row("p13.r9", 9, "detail", "Flex Int'l SMS Roaming 0.00", [
                _cell("Flex Int'l SMS Roaming", "text"), _cell("0.00", "amount"),
            ]),
            _row("p13.r12", 12, "detail", "Rogers 5G Business Internet 50.00", [
                _cell("Rogers 5G Business Internet", "text"), _cell("50.00", "amount"),
            ]),
            _row("p13.r14", 14, "group_total", "Total monthly charges 50.00", [
                _cell("Total monthly charges", "summary"), _cell("50.00", "amount"),
            ]),
            _row("p13.r20", 20, "group_total", "Total before taxes 50.00", [
                _cell("Total before taxes", "summary"), _cell("50.00", "amount"),
            ]),
            _row("p13.r22", 22, "group_total", "Total for Wireless 437-332-6295 $56.50", [
                _cell("Total for Wireless 437-332-6295", "summary"), _cell("$56.50", "amount"),
            ]),
        ],
    )])

    apply_layout_profile_hints(invoice, document, profile)

    assert len(invoice.line_items) == 1
    item = invoice.line_items[0]
    assert item.service_id == "437-332-6295"
    assert item.billing_reference == "Flex Int'l SMS Roaming"
    assert item.amount == Decimal("50.00")
    assert item.tax_amount == Decimal("6.50")


def test_granite_summary_table_uses_charges_and_tax_columns():
    invoice = _invoice(currency="CAD")
    hints = LineItemHints(
        detail_table_anchors=["Branch Billing Summary - Parent Pays"],
        skip_row_keywords=["Breakdown - Payments and Adjustments"],
        line_item_granularity="per_service_group",
        service_id_preference="leftmost_identifier",
        billing_reference_preference="separate_column",
        amount_source="table_charges_column",
        tax_source="table_tax_column",
    )
    document = LayoutDocument(pages=[LayoutPage(
        page_id="p3",
        page_number=3,
        width=800,
        height=600,
        visual_rows=[
            _row("p3.r1", 1, "anchor", "Branch Billing Summary - Parent Pays", [
                _cell("Branch Billing Summary - Parent Pays", "text"),
            ]),
            _row("p3.r2", 2, "header", "ACCOUNT NAME CITY/PROVINCE TAX / SURCHRG CHARGES ADJUST SUB-TOT", [
                _cell("ACCOUNT", "text"), _cell("NAME", "text"), _cell("CITY/PROVINCE", "text"),
                _cell("TAX / SURCHRG", "text"), _cell("CHARGES", "text"), _cell("ADJUST", "text"),
                _cell("SUB-TOT", "text"),
            ]),
            _row("p3.r3", 3, "service_start", (
                "08918899 811602 KELOWNA Kelowna, BC "
                "0 0.00 - 12.53 104.36 - 116.89"
            ), [
                _cell("08918899", "identifier"),
                _cell("811602 KELOWNA", "text"),
                _cell("Kelowna, BC", "text"),
                _cell("0", "amount"), _cell("0.00", "amount"), _cell("-", "text"),
                _cell("12.53", "amount"), _cell("104.36", "amount"), _cell("-", "text"),
                _cell("116.89", "amount"),
            ]),
            _row("p3.r4", 4, "detail", "AIRPORT", [_cell("AIRPORT", "text")]),
        ],
    )])

    apply_layout_profile_hints(invoice, document, None, hints)

    assert len(invoice.line_items) == 1
    item = invoice.line_items[0]
    assert item.service_id == "08918899"
    assert item.billing_reference == "811602 KELOWNA AIRPORT"
    assert item.amount == Decimal("104.36")
    assert item.tax_amount == Decimal("12.53")


def test_profile_date_label_overrides_today_fallback_for_granite():
    invoice = _invoice()
    profile = SupplierProfile.model_validate({
        "profile_id": "granite.test",
        "identity": {"canonical_name": "Granite"},
        "field_overrides": [{
            "name": "invoice_date",
            "scope": "document",
            "type": "date",
            "role": "none",
            "label_hint": "INVOICE DATE:",
        }],
        "advanced": {
            "line_item_hints": {
                "line_item_granularity": "per_service_group",
                "amount_source": "table_charges_column",
                "detail_table_anchors": ["Branch Billing Summary - Parent Pays"],
            }
        },
    })
    document = LayoutDocument(pages=[LayoutPage(
        page_id="p1",
        page_number=1,
        width=800,
        height=600,
        visual_rows=[
            _row("p1.r1", 1, "detail", "Invoice: 238526639 INVOICE DATE: 2026/03/01", [
                _cell("Invoice: 238526639", "text"), _cell("INVOICE DATE: 2026/03/01", "text"),
            ]),
        ],
    )])

    apply_layout_profile_hints(invoice, document, profile)

    assert invoice.header.invoice_date == date(2026, 3, 1)


def test_profile_date_label_uses_next_row_month_first_value_for_bell():
    invoice = _invoice()
    invoice.header.due_date = date(2026, 4, 21)
    invoice.fields = {"due_date": date(2026, 4, 21)}
    invoice.warnings = [SYNTHETIC_INVOICE_DATE_WARNING]
    profile = SupplierProfile.model_validate({
        "profile_id": "bell.test",
        "identity": {"canonical_name": "Bell Aliant"},
        "field_overrides": [
            {
                "name": "invoice_date",
                "scope": "document",
                "type": "date",
                "role": "none",
                "label_hint": "Bill date",
            },
            {
                "name": "due_date",
                "scope": "document",
                "type": "date",
                "role": "none",
                "label_hint": "Your payment is due by:",
            },
        ],
    })
    document = LayoutDocument(pages=[LayoutPage(
        page_id="p1",
        page_number=1,
        width=800,
        height=600,
        visual_rows=[
            _row("p1.r1", 1, "detail", "Bill date", [_cell("Bill date", "text")]),
            _row("p1.r2", 2, "detail", "March 31, 2026", [_cell("March 31, 2026", "text")]),
            _row("p1.r3", 3, "detail", "Your payment is due by:", [_cell("Your payment is due by:", "text")]),
            _row("p1.r4", 4, "detail", "Apr 21, 2026", [_cell("Apr 21, 2026", "text")]),
        ],
    )])

    apply_layout_profile_hints(invoice, document, profile)

    assert invoice.header.invoice_date == date(2026, 3, 31)
    assert invoice.header.due_date == date(2026, 4, 21)
    assert invoice.fields["invoice_date"] == date(2026, 3, 31)
    assert SYNTHETIC_INVOICE_DATE_WARNING not in invoice.warnings


def test_profile_date_label_uses_supplier_day_first_date_format():
    invoice = _invoice()
    profile = SupplierProfile.model_validate({
        "profile_id": "eunetworks.test",
        "identity": {"canonical_name": "euNetworks"},
        "field_overrides": [
            {
                "name": "invoice_date",
                "scope": "document",
                "type": "date",
                "role": "none",
                "label_hint": "Invoice Date",
                "date_format": "%d/%m/%Y",
            },
            {
                "name": "due_date",
                "scope": "document",
                "type": "date",
                "role": "none",
                "label_hint": "Due Date",
                "date_format": "%d/%m/%Y",
            },
        ],
    })
    document = LayoutDocument(pages=[LayoutPage(
        page_id="p1",
        page_number=1,
        width=800,
        height=600,
        visual_rows=[
            _row(
                "p1.r1",
                1,
                "detail",
                "Document No. Customer ID Invoice Date Due Date",
                [
                    _cell("Document No.", "text", x0=0, x1=100),
                    _cell("Customer ID", "text", x0=110, x1=200),
                    _cell("Invoice Date", "text", x0=210, x1=310),
                    _cell("Due Date", "text", x0=320, x1=420),
                ],
            ),
            _row(
                "p1.r2",
                2,
                "detail",
                "IE-SI53999 A837737 05/01/2026 04/02/2026",
                [
                    _cell("IE-SI53999", "text", x0=0, x1=100),
                    _cell("A837737", "text", x0=110, x1=200),
                    _cell("05/01/2026", "text", x0=210, x1=310),
                    _cell("04/02/2026", "text", x0=320, x1=420),
                ],
            ),
        ],
    )])

    apply_layout_profile_hints(invoice, document, profile)

    assert invoice.header.invoice_date == date(2026, 1, 5)
    assert invoice.header.due_date == date(2026, 2, 4)


def test_profile_date_label_parses_dotted_month_abbreviation():
    invoice = _invoice()
    profile = SupplierProfile.model_validate({
        "profile_id": "coltjapan.test",
        "identity": {"canonical_name": "Colt Japan"},
        "field_overrides": [
            {
                "name": "due_date",
                "scope": "document",
                "type": "date",
                "role": "none",
                "label_hint": "Payment Due Date",
            },
        ],
    })
    document = LayoutDocument(pages=[LayoutPage(
        page_id="p1",
        page_number=1,
        width=1000,
        height=600,
        visual_rows=[
            _row(
                "p1.r1",
                1,
                "detail",
                "Customer Number (BCN) 909742 Payment Due Date Jun. 30, 2026",
                [
                    _cell("Customer Number (BCN)", "text", x0=95, x1=273),
                    _cell("909742", "identifier", x0=310, x1=382),
                    _cell("Payment Due Date", "text", x0=524, x1=695),
                    _cell("Jun. 30, 2026", "text", x0=778, x1=902),
                ],
            ),
        ],
    )])

    apply_layout_profile_hints(invoice, document, profile)

    assert invoice.header.due_date == date(2026, 6, 30)
    assert invoice.fields["due_date"] == date(2026, 6, 30)


def _item(line_number: int, service_id: str, billing_reference: str, amount: str) -> LineItem:
    return LineItem(
        line_number=line_number,
        service_id=service_id,
        billing_reference=billing_reference,
        description="Wavelengths",
        charge_type=ChargeType.RECURRING,
        amount=Decimal(amount),
        billing_period_start=date(2024, 10, 1),
        billing_period_end=date(2024, 10, 31),
        quantity=Decimal("1"),
        unit_rate=Decimal(amount),
    )


def _invoice(*, currency: str = "USD") -> CanonicalInvoice:
    return CanonicalInvoice(
        intake_id="i1",
        header=InvoiceHeader(
            supplier_name="Test",
            invoice_number="INV-1",
            invoice_date=date(2026, 1, 1),
            currency=currency,
        ),
        line_items=[],
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
    )


def _profile(*, detail_start_marker: str, detail_end_marker: str, hints: LineItemHints) -> SupplierProfile:
    return SupplierProfile.model_validate({
        "profile_id": "test.profile",
        "identity": {"canonical_name": "Test"},
        "delivery": {},
        "advanced": {
            "document_structure": {
                "detail_start_marker": detail_start_marker,
                "detail_end_marker": detail_end_marker,
            },
            "line_item_hints": hints.model_dump(),
        },
    })


def _row(row_id: str, row_index: int, role: str, text: str, cells: list[LayoutCell]) -> VisualRow:
    return VisualRow(
        row_id=row_id,
        page=1,
        row_index=row_index,
        row_role=role,
        text=text,
        bbox=BBox(x0=0, y0=row_index * 10, x1=100, y1=row_index * 10 + 8),
        cells=[
            cell.model_copy(update={"cell_id": f"{row_id}.c{i}", "row_index": row_index, "column_index": i})
            for i, cell in enumerate(cells)
        ],
    )


def _cell(text: str, role: str, *, x0: float = 0, x1: float = 10) -> LayoutCell:
    return LayoutCell(
        text=text,
        role=role,
        bbox=BBox(x0=x0, y0=0, x1=x1, y1=8),
        row_index=0,
        column_index=0,
    )


# ── child_identifier: innermost printed service id, carried down ──────────────


def _sid_hints(**overrides) -> LineItemHints:
    base = dict(
        line_item_granularity="per_charge_row",
        service_id_preference="child_identifier",
        service_id_column_label="Service ID",
        service_id_value_pattern=r"^(?!5-)\d{6,12}$",
    )
    base.update(overrides)
    return LineItemHints(**base)


def _srow(page: int, row_index: int, role: str, cells: list[LayoutCell]) -> VisualRow:
    row_id = f"p{page}.r{row_index}"
    return VisualRow(
        row_id=row_id,
        page=page,
        row_index=row_index,
        row_role=role,
        text=" ".join(cell.text for cell in cells),
        bbox=BBox(x0=0, y0=row_index * 10, x1=800, y1=row_index * 10 + 8),
        cells=[
            cell.model_copy(update={"cell_id": f"{row_id}.c{i}", "row_index": row_index, "column_index": i})
            for i, cell in enumerate(cells)
        ],
    )


def _header_row(page: int = 1, row_index: int = 0) -> VisualRow:
    return _srow(page, row_index, "header", [
        _cell("Service ID", "text", x0=30, x1=80),
        _cell("Amount", "text", x0=500, x1=560),
    ])


def _page(page_number: int, rows: list[VisualRow]) -> LayoutPage:
    return LayoutPage(page_id=f"p{page_number}", page_number=page_number, width=800, height=1000, visual_rows=rows)


def test_child_identifier_prefers_child_over_parent():
    rows = [
        _header_row(),
        _srow(1, 1, "service_start", [_cell("445715812", "identifier", x0=32, x1=80)]),
        _srow(1, 2, "service_start", [_cell("347207739", "identifier", x0=50, x1=98)]),
        _srow(1, 3, "detail", [_cell("Access GigE", "text", x0=104, x1=200),
                               _cell("378.00", "amount", x0=500, x1=560)]),
    ]
    mapping = _child_service_id_by_row(LayoutDocument(pages=[_page(1, rows)]), _sid_hints())

    assert mapping["p1.r3"] == "347207739"
    assert mapping["p1.r1"] == "445715812"


def test_child_identifier_ignores_total_rows():
    rows = [
        _header_row(),
        _srow(1, 1, "service_start", [_cell("356139938", "identifier", x0=50, x1=98)]),
        _srow(1, 2, "detail", [_cell("1,840.00", "amount", x0=500, x1=560)]),
        _srow(1, 3, "group_total", [_cell("Total 356139938", "summary", x0=457, x1=520),
                                    _cell("1,840.00", "amount", x0=560, x1=610)]),
        _srow(1, 4, "service_start", [_cell("445715812", "identifier", x0=32, x1=80)]),
        _srow(1, 5, "detail", [_cell("709.00", "amount", x0=500, x1=560)]),
    ]
    mapping = _child_service_id_by_row(LayoutDocument(pages=[_page(1, rows)]), _sid_hints())

    assert mapping["p1.r2"] == "356139938"
    assert mapping["p1.r5"] == "445715812"


def test_child_identifier_falls_back_to_service_without_children():
    rows = [
        _header_row(),
        _srow(1, 1, "service_start", [_cell("356139938", "identifier", x0=50, x1=98)]),
        _srow(1, 2, "detail", [_cell("1,840.00", "amount", x0=500, x1=560)]),
    ]
    mapping = _child_service_id_by_row(LayoutDocument(pages=[_page(1, rows)]), _sid_hints())

    assert mapping["p1.r2"] == "356139938"


def test_child_identifier_carries_across_page_breaks():
    page1 = _page(1, [
        _header_row(),
        _srow(1, 1, "service_start", [_cell("445715812", "identifier", x0=32, x1=80)]),
        _srow(1, 2, "service_start", [_cell("347207739", "identifier", x0=50, x1=98)]),
    ])
    page2 = _page(2, [_srow(2, 0, "detail", [_cell("331.00", "amount", x0=500, x1=560)])])
    mapping = _child_service_id_by_row(LayoutDocument(pages=[page1, page2]), _sid_hints())

    assert mapping["p2.r0"] == "347207739"


def test_child_identifier_ignores_identifiers_outside_the_service_column():
    rows = [
        _header_row(),
        _srow(1, 1, "service_start", [_cell("347207739", "identifier", x0=50, x1=98)]),
        _srow(1, 2, "detail", [_cell("792243845", "identifier", x0=700, x1=760),
                               _cell("378.00", "amount", x0=500, x1=560)]),
    ]
    mapping = _child_service_id_by_row(LayoutDocument(pages=[_page(1, rows)]), _sid_hints())

    assert mapping["p1.r2"] == "347207739"


def test_child_identifier_overrides_the_ai_supplied_service_id():
    document = LayoutDocument(pages=[_page(1, [
        _header_row(),
        _srow(1, 1, "service_start", [_cell("445715812", "identifier", x0=32, x1=80)]),
        _srow(1, 2, "service_start", [_cell("347207739", "identifier", x0=50, x1=98)]),
        _srow(1, 3, "detail", [_cell("378.00", "amount", x0=500, x1=560)]),
    ])])
    invoice = _invoice()
    invoice.line_items = [
        LineItem(line_number=1, description="Access GigE", amount=Decimal("378.00"),
                 charge_type=ChargeType.RECURRING, service_id="445715812", source_row_id="p1.r3"),
    ]

    apply_layout_profile_hints(
        invoice, document,
        _profile(detail_start_marker="Service ID", detail_end_marker="", hints=_sid_hints()),
        preserve_existing_line_items=True,
    )

    assert invoice.line_items[0].service_id == "347207739"


def test_child_identifier_is_inert_for_other_preferences():
    document = LayoutDocument(pages=[_page(1, [
        _header_row(),
        _srow(1, 1, "service_start", [_cell("445715812", "identifier", x0=32, x1=80)]),
        _srow(1, 2, "service_start", [_cell("347207739", "identifier", x0=50, x1=98)]),
        _srow(1, 3, "detail", [_cell("378.00", "amount", x0=500, x1=560)]),
    ])])
    invoice = _invoice()
    invoice.line_items = [
        LineItem(line_number=1, description="Access GigE", amount=Decimal("378.00"),
                 charge_type=ChargeType.RECURRING, service_id="445715812", source_row_id="p1.r3"),
    ]

    apply_layout_profile_hints(
        invoice, document,
        _profile(detail_start_marker="Service ID", detail_end_marker="",
                 hints=_sid_hints(service_id_preference="leftmost_identifier")),
        preserve_existing_line_items=True,
    )

    assert invoice.line_items[0].service_id == "445715812"


# ── excluded_total: read a printed section total from the layout ──────────────


def test_excluded_total_read_from_labelled_layout_row():
    from stencil.extraction.normalization import _apply_excluded_total_hints
    from stencil.fields.schema import FieldDef, FieldRole, FieldScope, FieldType

    document = LayoutDocument(pages=[_page(4, [
        _srow(4, 0, "detail", [_cell("ACCOUNT LEVEL CHARGES", "text", x0=30, x1=200)]),
        _srow(4, 1, "group_total", [
            _cell("Total Account Level Charges", "summary", x0=30, x1=260),
            _cell("787.50", "amount", x0=500, x1=560),
            _cell("51.19", "amount", x0=600, x1=650),
            _cell("838.69", "amount", x0=700, x1=760),
        ]),
    ])])
    profile = SupplierProfile.model_validate({
        "profile_id": "t", "identity": {"canonical_name": "Lumen"}, "delivery": {},
        "field_overrides": [FieldDef(
            name="total_account_level_charges", scope=FieldScope.DOCUMENT, type=FieldType.CURRENCY,
            role=FieldRole.EXCLUDED_TOTAL, label_hint="Total Account Level Charges",
        ).model_dump()],
    })
    invoice = _invoice()

    _apply_excluded_total_hints(invoice, document, profile)

    # The Total column (last money value on the row), not the amount or tax column.
    assert invoice.fields["total_account_level_charges"] == "838.69"


def test_excluded_total_absent_label_is_a_no_op():
    from stencil.extraction.normalization import _apply_excluded_total_hints
    from stencil.fields.schema import FieldDef, FieldRole, FieldScope, FieldType

    document = LayoutDocument(pages=[_page(4, [
        _srow(4, 0, "detail", [_cell("SERVICE LEVEL ACTIVITY", "text", x0=30, x1=200)]),
    ])])
    profile = SupplierProfile.model_validate({
        "profile_id": "t", "identity": {"canonical_name": "Lumen"}, "delivery": {},
        "field_overrides": [FieldDef(
            name="total_account_level_charges", scope=FieldScope.DOCUMENT, type=FieldType.CURRENCY,
            role=FieldRole.EXCLUDED_TOTAL, label_hint="Total Account Level Charges",
        ).model_dump()],
    })
    invoice = _invoice()

    _apply_excluded_total_hints(invoice, document, profile)

    assert "total_account_level_charges" not in invoice.fields
