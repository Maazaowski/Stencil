"""Deterministic evidence discovery for profile authoring."""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from stencil.config import settings
from stencil.extraction.layout import PDFTextScan, scan_pdf_pages
from stencil.output.spec import OutputSpec
from stencil.profiles.schema import (
    AuthoringEvidence,
    ExtractionPlan,
    ReconciliationRule,
    RegionRule,
    RowContextRule,
    RowSelectorRule,
    ValueExpression,
)

ENGINE_VERSION = "1.0"


class BlueprintSignature(BaseModel):
    filename: str
    row_count: int
    identifiers: list[str] = Field(default_factory=list)
    invoice_numbers: list[str] = Field(default_factory=list)
    accounts: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    amount_total: str | None = None
    tax_total: str | None = None
    linked_invoice: str | None = None
    match_kind: str = "historical"


class InvoiceDiscovery(BaseModel):
    label: str
    page_count: int
    text_chars: int
    selected_page_numbers: list[int] = Field(default_factory=list)
    matched_identifier_count: int = 0
    blueprint_identifier_count: int = 0
    start_marker: str | None = None
    end_marker: str | None = None
    predicted_full_chunks: int = 0
    predicted_bounded_chunks: int = 0


class DiscoveryReport(BaseModel):
    engine_version: str = ENGINE_VERSION
    inferred_document_family: str
    category_confidence: float
    category_reasons: list[str] = Field(default_factory=list)
    blueprints: list[BlueprintSignature] = Field(default_factory=list)
    invoices: list[InvoiceDiscovery] = Field(default_factory=list)
    candidate_plan: ExtractionPlan
    evidence: AuthoringEvidence
    inferred_date_formats: dict[str, str] = Field(default_factory=dict)
    elapsed_ms: int = 0


@dataclass(frozen=True)
class DiscoveryInput:
    label: str
    pdf_path: Path


def discover_profile(
    *,
    invoices: list[DiscoveryInput],
    blueprint_contexts: list[dict],
    output_spec: OutputSpec,
    supplier_name: str | None = None,
    document_family_override: str | None = None,
) -> DiscoveryReport:
    started = time.monotonic()
    concurrency = max(1, int(settings.profile_discovery_invoice_concurrency))
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        scans = list(pool.map(lambda item: scan_pdf_pages(item.pdf_path), invoices))

    signatures = [_blueprint_signature(context, output_spec) for context in blueprint_contexts]
    _link_blueprints(signatures, invoices, scans)
    family, confidence, reasons = infer_document_family(
        scans=scans,
        output_spec=output_spec,
        supplier_name=supplier_name,
        override=document_family_override,
    )

    invoice_reports = [
        _discover_invoice(item.label, scan, _identifiers_for_scan(signatures, scan))
        for item, scan in zip(invoices, scans, strict=True)
    ]
    plan = _candidate_plan(
        family=family,
        invoice_reports=invoice_reports,
        scans=scans,
        signatures=signatures,
    )
    evidence_level = _evidence_level(signatures)
    inferred_date_formats = _inferred_date_formats(scans)
    unresolved: list[str] = []
    hard_blockers: list[str] = []
    status = "review_required"
    if not signatures:
        unresolved.append("No blueprint was supplied; output mappings require review.")
    if not any(signature.identifiers for signature in signatures):
        unresolved.append("No blueprint identifiers were available for region coverage validation.")
    if any(not report.selected_page_numbers for report in invoice_reports):
        unresolved.append("At least one invoice has no high-confidence bounded line-item region.")
    for context in blueprint_contexts:
        if context.get("contract_compatible") is False:
            missing = ", ".join(context.get("missing_output_headers") or []) or "unknown"
            hard_blockers.append(
                f"Blueprint headers do not match the selected output spec; missing: {missing}."
            )
    metrics = {
        "invoice_count": len(invoices),
        "blueprint_count": len(signatures),
        "full_chunks": sum(report.predicted_full_chunks for report in invoice_reports),
        "bounded_chunks": sum(report.predicted_bounded_chunks for report in invoice_reports),
        "max_ai_chunks": settings.profile_discovery_max_ai_chunks,
    }
    evidence = AuthoringEvidence(
        evidence_level=evidence_level,
        status="failed" if hard_blockers else status,
        category_confidence=confidence,
        metrics=metrics,
        unresolved_risks=unresolved,
        hard_blockers=hard_blockers,
        review_warnings=unresolved,
        engine_version=ENGINE_VERSION,
    )
    return DiscoveryReport(
        inferred_document_family=family,
        category_confidence=confidence,
        category_reasons=reasons,
        blueprints=signatures,
        invoices=invoice_reports,
        candidate_plan=plan,
        evidence=evidence,
        inferred_date_formats=inferred_date_formats,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


def infer_document_family(
    *,
    scans: list[PDFTextScan],
    output_spec: OutputSpec,
    supplier_name: str | None,
    override: str | None,
) -> tuple[str, float, list[str]]:
    if override in {"standard", "wireless", "time_and_material"}:
        return override, 1.0, ["User override"]
    text = "\n".join(page.text for scan in scans[:3] for page in scan.pages[:20]).lower()
    headers = " ".join(column.header.lower() for column in output_spec.columns)
    supplier = str(supplier_name or "").lower()
    tm_signals = sum(token in text or token in headers for token in (
        "quantity", "unit rate", "rate (usd)", "timesheet", "annexure", "ext_usagecost", "ext_usage",
    ))
    wireless_signals = sum(token in text for token in (
        "wireless", "monthly charges", "service activity", "usage summary", "device",
        "ligne téléphonique", "synthèse des services", "facture - annexe",
    ))
    telecom_supplier = any(token in supplier or token in text[:8000] for token in (
        "at&t", "verizon", "bell", "rogers", "t-mobile", "orange",
    ))
    if "time_and_material" in output_spec.spec_id or tm_signals >= 2:
        return "time_and_material", min(0.98, 0.72 + tm_signals * 0.06), [
            "Time-and-material output/column signals",
        ]
    if wireless_signals >= 2 or (telecom_supplier and wireless_signals >= 1):
        return "wireless", min(0.98, 0.72 + wireless_signals * 0.07), [
            "Wireless section and supplier signals",
        ]
    if telecom_supplier and re.search(r"\b(?:service|location)\s+(?:summary|index)\b", text):
        return "wireless", 0.80, ["Telecom supplier with per-service/location summary"]
    return "standard", 0.78, ["No specialized wireless or time-and-material signature"]


def compact_discovery_payload(report: DiscoveryReport) -> dict:
    """LLM-safe evidence bundle: candidates and metrics, never full page text."""
    return report.model_dump(mode="json")


def _blueprint_signature(context: dict, spec: OutputSpec) -> BlueprintSignature:
    headers = [str(value or "").strip().upper() for value in context.get("aligned_headers") or []]
    rows = context.get("aligned_rows") or []
    source_indexes = {column.source: index for index, column in enumerate(spec.columns)}

    def values(source: str) -> list[str]:
        index = source_indexes.get(source)
        if index is None:
            return []
        return sorted({str(row[index]).strip() for row in rows if index < len(row) and str(row[index]).strip()})

    # Header fallback keeps custom specs useful when sources are non-standard.
    id_values = values("row.service_id")
    if not id_values and "EXT_SERVICEID" in headers:
        index = headers.index("EXT_SERVICEID")
        id_values = sorted({str(row[index]).strip() for row in rows if index < len(row) and str(row[index]).strip()})
    totals = context.get("totals") or {}
    return BlueprintSignature(
        filename=str(context.get("filename") or "blueprint"),
        row_count=int(context.get("row_count") or len(rows)),
        identifiers=id_values,
        invoice_numbers=values("field.invoice_number"),
        accounts=values("field.account_number"),
        dates=values("field.invoice_date") or values("field.billing_period_start"),
        amount_total=totals.get("amount"),
        tax_total=totals.get("tax"),
        linked_invoice=context.get("_paired_invoice"),
        match_kind="paired" if context.get("_paired_invoice") else "historical",
    )


def _link_blueprints(
    signatures: list[BlueprintSignature],
    invoices: list[DiscoveryInput],
    scans: list[PDFTextScan],
) -> None:
    for signature in signatures:
        if signature.match_kind == "paired" and signature.linked_invoice:
            continue
        best_label: str | None = None
        best_score = 0
        for invoice, scan in zip(invoices, scans, strict=True):
            text = "\n".join(page.text for page in scan.pages[:5])
            score = sum(value in text for value in signature.invoice_numbers)
            score += sum(value in text for value in signature.accounts)
            score += sum(value in text for value in signature.dates)
            if score > best_score:
                best_label, best_score = invoice.label, score
        signature.linked_invoice = best_label
        signature.match_kind = "paired" if best_score and any(
            number and any(number in page.text for page in scan.pages[:5])
            for invoice, scan in zip(invoices, scans, strict=True)
            if invoice.label == best_label
            for number in signature.invoice_numbers
        ) else "historical"


def _discover_invoice(label: str, scan: PDFTextScan, identifiers: list[str]) -> InvoiceDiscovery:
    counts: dict[int, int] = {}
    unique_by_page: dict[int, set[str]] = {}
    for page in scan.pages:
        normalized_text = re.sub(r"[^A-Z0-9]", "", page.text.upper())
        found = {
            identifier for identifier in identifiers
            if identifier and (
                identifier in page.text
                or re.sub(r"[^A-Z0-9]", "", identifier.upper()) in normalized_text
            )
        }
        if found:
            unique_by_page[page.page_number] = found
            counts[page.page_number] = len(found)
    non_toc_counts = {
        page: count for page, count in counts.items()
        if "sommaire" not in scan.pages[page - 1].text.lower()
    }
    if non_toc_counts:
        counts = non_toc_counts
    # Blueprint identifiers are delivery evidence, so cover their complete page
    # span instead of selecting only the densest cluster. The old behavior could
    # silently omit valid middle pages from long annex invoices.
    selected = (
        list(range(min(counts), max(counts) + 1))
        if counts
        else _structural_page_cluster(scan)
    )
    matched = len({identifier for page in selected for identifier in unique_by_page.get(page, set())})
    start_marker = _start_marker(scan, selected, identifiers)
    end_marker = _end_marker(scan, selected, identifiers)
    full_chunks = max(1, (scan.text_chars + 23_999) // 24_000)
    bounded_chars = sum(scan.pages[number - 1].text_chars for number in selected)
    bounded_chunks = max(1, (bounded_chars + 23_999) // 24_000) if selected else full_chunks
    return InvoiceDiscovery(
        label=label,
        page_count=scan.page_count,
        text_chars=scan.text_chars,
        selected_page_numbers=selected,
        matched_identifier_count=matched,
        blueprint_identifier_count=len(identifiers),
        start_marker=start_marker,
        end_marker=end_marker,
        predicted_full_chunks=full_chunks,
        predicted_bounded_chunks=bounded_chunks,
    )


def _identifiers_for_scan(
    signatures: list[BlueprintSignature], scan: PDFTextScan,
) -> list[str]:
    header_text = "\n".join(page.text for page in scan.pages[:5])
    relevant = [
        signature for signature in signatures
        if any(value and value in header_text for value in [
            *signature.accounts,
            *signature.invoice_numbers,
        ])
    ]
    selected = relevant or signatures
    return sorted({
        identifier
        for signature in selected
        for identifier in signature.identifiers
        if identifier
    })


def _dense_page_cluster(counts: dict[int, int]) -> list[int]:
    if not counts:
        return []
    peak = max(counts.values())
    threshold = max(2, int(peak * 0.25))
    dense = sorted(page for page, count in counts.items() if count >= threshold)
    clusters: list[list[int]] = []
    for page in dense:
        if not clusters or page > clusters[-1][-1] + 1:
            clusters.append([page])
        else:
            clusters[-1].append(page)
    return max(clusters, key=lambda cluster: (sum(counts[p] for p in cluster), -len(cluster)))


def _structural_page_cluster(scan: PDFTextScan) -> list[int]:
    """Find conventional detail pages when no blueprint identifiers exist."""
    scores: dict[int, int] = {}
    headings = (
        "description", "line items", "invoice details", "service activity",
        "annexure", "quantity", "unit rate", "rate (usd)", "amount", "charges",
    )
    for page in scan.pages:
        lower = page.text.lower()
        heading_score = sum(2 for heading in headings if heading in lower)
        money_rows = len(re.findall(r"(?m)^.*(?:[$€£]\s*)?\(?-?\d[\d,]*\.\d{2}\)?.*$", page.text))
        scores[page.page_number] = heading_score + min(8, money_rows // 3)
    peak = max(scores.values(), default=0)
    if peak < 2:
        return list(range(1, min(scan.page_count, 5) + 1))
    dense = {page: score for page, score in scores.items() if score >= max(2, peak // 3)}
    return _dense_page_cluster(dense)


def _start_marker(scan: PDFTextScan, pages: list[int], identifiers: list[str]) -> str | None:
    if not pages:
        return None
    text = scan.pages[pages[0] - 1].text
    first_positions = [text.find(value) for value in identifiers if value and text.find(value) >= 0]
    prefix = text[:min(first_positions)] if first_positions else text[:4000]
    return _safe_structural_marker(_preferred_heading(prefix, reverse=True, kind="start"))


def _end_marker(scan: PDFTextScan, pages: list[int], identifiers: list[str]) -> str | None:
    if not pages:
        return None
    text = scan.pages[pages[-1] - 1].text
    last = max((text.rfind(value) for value in identifiers if value), default=-1)
    suffix = text[last:] if last >= 0 else text[-4000:]
    return _safe_structural_marker(_preferred_heading(suffix, reverse=False, kind="end"))


def _safe_structural_marker(value: str | None) -> str | None:
    """Reject invoice-specific values masquerading as reusable section markers."""
    text = str(value or "").strip()
    if not text:
        return None
    if re.search(r"\d{4,}|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}", text):
        return None
    if len(re.findall(r"[A-Za-zÀ-ÿ]{3,}", text)) < 2:
        return None
    return text


def _preferred_heading(text: str, *, reverse: bool, kind: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if 2 <= len(line.strip()) <= 100]
    preferred = (
        ("service activity", "location / service index", "annexure", "line items", "invoice details")
        if kind == "start"
        else ("usage summary", "taxes and surcharge summary", "total :", "grand total")
    )
    for token in preferred:
        matches = [line for line in lines if token in line.lower()]
        if matches:
            return matches[0]
    if not lines:
        return None
    candidates = list(reversed(lines)) if reverse else lines
    return next(
        (
            line for line in candidates
            if not _looks_like_value(line) and _safe_structural_marker(line)
        ),
        None,
    )


def _candidate_plan(
    *,
    family: str,
    invoice_reports: list[InvoiceDiscovery],
    scans: list[PDFTextScan],
    signatures: list[BlueprintSignature],
) -> ExtractionPlan:
    start_markers = _stable_values(report.start_marker for report in invoice_reports)
    end_markers = _stable_values(report.end_marker for report in invoice_reports)
    identifiers = [identifier for signature in signatures for identifier in signature.identifiers]
    identifier_pattern = _identifier_pattern(identifiers)
    dense_text = "\n".join(
        scan.pages[number - 1].text
        for scan, report in zip(scans, invoice_reports, strict=True)
        for number in report.selected_page_numbers[:20]
    )
    if identifier_pattern and re.search(
        rf"(?i)\bn\s*[°ºo]\s*:?\s*{identifier_pattern}", dense_text
    ):
        identifier_pattern = rf"(?i)\bn\s*[°ºo]\s*:?\s*({identifier_pattern})"
    total_rows = bool(re.search(r"(?mi)^\s*Total\s+\S+", dense_text))
    scope = "group_footer" if total_rows else "row"
    include_pattern = (
        rf"(?i)\bTotal\s+({identifier_pattern})\b"
        if total_rows and identifier_pattern
        else identifier_pattern
    )
    if not include_pattern:
        include_pattern = r"(?:[$€£]\s*)?\(?-?\d[\d,]*\.\d{2}\)?"
    row_rules: dict[str, ValueExpression] = {}
    if identifier_pattern:
        row_rules["service_id"] = ValueExpression(
            op="regex_extract", pattern=identifier_pattern, args=[ValueExpression(op="row_text")])
        row_rules["billing_reference"] = ValueExpression(
            op="copy", args=[ValueExpression(op="field", field="service_id")])

    lower = dense_text.lower()
    if "quantity" in lower and ("rate (usd)" in lower or "unit rate" in lower):
        row_rules.update({
            "quantity": ValueExpression(op="row_column", label="Quantity"),
            "unit_rate": ValueExpression(op="row_column", label="Rate (USD)"),
            "amount": ValueExpression(op="row_column", label="Amount (USD)"),
        })
        reconciliations = [ReconciliationRule(
            name="quantity times rate equals amount",
            left=ValueExpression(op="multiply", args=[
                ValueExpression(op="field", field="quantity"),
                ValueExpression(op="field", field="unit_rate"),
            ]),
            right=ValueExpression(op="field", field="amount"),
        )]
    else:
        tax_labels = [label for label in (
            "Taxes & Surcharges", "Company fees & surcharges", "Government fees & taxes", "Tax",
        ) if label.lower() in lower]
        if "total (usd)" in lower:
            total_label = "Total (USD)"
        elif not total_rows and "amount (usd)" in lower:
            total_label = "Amount (USD)"
        elif not total_rows and "amount" in lower:
            total_label = "Amount"
        else:
            total_label = "Total"
        tax_args = [ValueExpression(op="row_column", label=label) for label in tax_labels]
        tax_expression = (
            ValueExpression(op="sum", args=tax_args) if len(tax_args) > 1
            else (tax_args[0] if tax_args else ValueExpression(op="literal", value=0))
        )
        if "company fees & surcharges" in lower and "government fees & taxes" in lower:
            tax_expression = ValueExpression(op="abs", args=[tax_expression])
        row_rules["tax_amount"] = tax_expression
        row_rules["amount"] = ValueExpression(op="subtract", args=[
            ValueExpression(op="row_column", label=total_label),
            ValueExpression(op="field", field="tax_amount"),
        ])
        reconciliations = [ReconciliationRule(
            name="amount plus tax equals printed total",
            left=ValueExpression(op="sum", args=[
                ValueExpression(op="field", field="amount"),
                ValueExpression(op="field", field="tax_amount"),
            ]),
            right=ValueExpression(op="row_column", label=total_label),
        )]

    regions = []
    if start_markers or end_markers:
        regions.append(RegionRule(
            start_markers=start_markers,
            end_markers=end_markers,
            occurrence="all",
            include_start=True,
            include_end=False,
            max_pages=None,
        ))
    context_rules: list[RowContextRule] = []
    date_token = r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}"
    period_pattern = rf"(?i)(?:p[ée]riode|period)\s+(?:du|from)?\s*({date_token})\s+(?:au|to|[-–])\s*({date_token})"
    if re.search(period_pattern, dense_text):
        context_rules.append(RowContextRule(
            anchor_pattern=period_pattern,
            field_groups={"billing_period_start": 1, "billing_period_end": 2},
            reset_patterns=[],
        ))
    return ExtractionPlan(
        document_family=family,
        regions=regions,
        row_selector=RowSelectorRule(
            scope=scope,
            include_pattern=include_pattern,
            exclude_patterns=[
                r"(?i)\bsub\s*total\b",
                r"(?i)\bgrand\s+total\b",
                r"(?i)\btotal\s+due\b",
            ],
            identifier_pattern=identifier_pattern,
        ),
        row_context_rules=context_rules,
        row_field_rules=row_rules,
        reconciliation_rules=reconciliations,
    )


def _identifier_pattern(values: list[str]) -> str | None:
    values = [value.strip() for value in values if value and value.strip()]
    if not values:
        return None
    if all(re.fullmatch(r"\d{3}[.\-]\d{3}[.\-]\d{4}", value) for value in values):
        return r"\d{3}[.\-]\d{3}[.\-]\d{4}"
    if all(re.fullmatch(r"[A-Z0-9]+C", value, re.IGNORECASE) for value in values):
        return r"[A-Z0-9]+C"
    if all(value.isdigit() for value in values):
        lengths = {len(value) for value in values}
        return rf"\d{{{next(iter(lengths))}}}" if len(lengths) == 1 else r"\d+"
    prefix = "".join(character for character in values[0] if not character.isdigit())
    return rf"{re.escape(prefix)}[A-Z0-9._/\-]+" if prefix else r"[A-Z0-9][A-Z0-9._/\-]{3,}"


def _inferred_date_formats(scans: list[PDFTextScan]) -> dict[str, str]:
    for scan in scans:
        text = "\n".join(page.text for page in scan.pages)
        for first, second in re.findall(
            r"(\d{1,2})/(\d{1,2})/\d{4}",
            text,
        ):
            if int(first) > 12:
                return {
                    "row.billing_period_start": "%d/%m/%Y",
                    "row.billing_period_end": "%d/%m/%Y",
                }
            if int(second) > 12:
                return {
                    "row.billing_period_start": "%m/%d/%Y",
                    "row.billing_period_end": "%m/%d/%Y",
                }
    return {}


def _stable_values(values) -> list[str]:
    cleaned = [str(value).strip() for value in values if value and str(value).strip()]
    if not cleaned:
        return []
    counts = {value: cleaned.count(value) for value in set(cleaned)}
    threshold = max(1, len(cleaned) // 2)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [value for value, count in ordered if count >= threshold][:2]


def _evidence_level(signatures: list[BlueprintSignature]) -> str:
    if any(signature.match_kind == "paired" for signature in signatures):
        return "paired_blueprint"
    if signatures:
        return "historical_blueprint"
    return "invoice_only"


def _looks_like_value(value: str) -> bool:
    return bool(re.fullmatch(r"[-$()\d,./:% ]+", value))
