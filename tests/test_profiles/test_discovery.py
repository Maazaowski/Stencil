from stencil.extraction.layout import PageTextScan, PDFTextScan
from stencil.output.spec import OutputColumn, OutputSpec
from stencil.profiles.discovery import (
    BlueprintSignature,
    _candidate_plan,
    _discover_invoice,
    _identifiers_for_scan,
    _safe_structural_marker,
    infer_document_family,
)


def _scan(text: str) -> PDFTextScan:
    return PDFTextScan(
        page_count=1,
        text_chars=len(text),
        pages=[PageTextScan(page_number=1, text=text, text_chars=len(text))],
    )


def test_discovery_scopes_blueprint_identifiers_to_invoice_account():
    signatures = [
        BlueprintSignature(filename="one.xls", row_count=1, accounts=["111"], identifiers=["A1"]),
        BlueprintSignature(filename="two.xls", row_count=1, accounts=["222"], identifiers=["B1"]),
    ]

    assert _identifiers_for_scan(signatures, _scan("compte client : 222")) == ["B1"]


def test_orange_french_fixed_line_invoice_is_wireless_family():
    spec = OutputSpec(
        spec_id="temforce.orange",
        columns=[OutputColumn(header="EXT_AMOUNT", source="row.amount")],
    )

    family, confidence, reasons = infer_document_family(
        scans=[_scan("Orange Business facture - annexe synthèse des services ligne téléphonique")],
        output_spec=spec,
        supplier_name="Orange Business",
        override=None,
    )

    assert family == "wireless"
    assert confidence >= 0.9
    assert reasons


def test_discovery_covers_complete_identifier_page_span_and_builds_period_context():
    pages = []
    identifiers = ["0800450400", "0825889265"]
    for number in range(1, 28):
        text = "header"
        if number == 5:
            text = "détail des produits et services\nn° : 0800450400"
        elif number == 21:
            text = "période du 01/06/2026 au 30/06/2026\nn° : 0825889265"
        pages.append(PageTextScan(page_number=number, text=text, text_chars=len(text)))
    scan = PDFTextScan(page_count=27, text_chars=sum(page.text_chars for page in pages), pages=pages)

    report = _discover_invoice("orange.pdf", scan, identifiers)
    plan = _candidate_plan(
        family="wireless",
        invoice_reports=[report],
        scans=[scan],
        signatures=[BlueprintSignature(filename="expected.xlsx", row_count=2, identifiers=identifiers)],
    )

    assert report.selected_page_numbers == list(range(5, 22))
    assert plan.regions[0].occurrence == "all"
    assert plan.regions[0].max_pages is None
    assert plan.row_context_rules[0].field_groups == {
        "billing_period_start": 1,
        "billing_period_end": 2,
    }


def test_sample_specific_numeric_marker_is_rejected():
    assert _safe_structural_marker("Guide Vocal - Ref Orange 0025159386") is None
    assert _safe_structural_marker("Détail des produits et services") == "Détail des produits et services"
