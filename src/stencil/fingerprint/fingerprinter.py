"""PDF layout fingerprinting using pymupdf.

Generates a structural hash of a PDF's layout features so we can route
known layouts to saved extraction models (fast path) without calling AI.

When a supplier profile includes ``layout_fingerprint`` config, the hash uses
profile-scoped anchors and normalization (regional tax labels, optional columns,
currency codes, etc.). Otherwise the legacy generic heuristic hash is used.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # pymupdf

from stencil.profiles.schema import LayoutFingerprintRules, SupplierProfile


@dataclass
class ResolvedFingerprintConfig:
    header_anchors: list[str]
    table_anchors: list[str]
    summary_anchors: list[str]
    tax_role_keywords: list[str]
    currency_codes: list[str]
    ignore_label_patterns: list[str]
    optional_column_patterns: list[str]
    exclude_span_patterns: list[str]


@dataclass
class LayoutSpan:
    text: str
    page: int
    x0: float
    y0: float
    font_size: float


@dataclass
class LayoutFeatures:
    page_count: int = 0
    first_page_labels: list[str] = field(default_factory=list)
    table_header_texts: list[str] = field(default_factory=list)
    text_block_positions: list[tuple[int, int]] = field(default_factory=list)
    font_sizes_used: list[float] = field(default_factory=list)


_DEFAULT_EXCLUDE_SPAN_PATTERNS = (
    r"(?i)\biban\b",
    r"(?i)\bswift\b",
    r"(?i)\bbeneficiary\b",
    r"(?i)\bbank\b",
    r"(?i)\bunicredit\b",
)

_LABEL_KEYWORDS = {
    "invoice", "account", "date", "due", "total", "amount", "number",
    "bill", "period", "customer", "payment", "service", "description",
    "charge", "tax", "subtotal", "credit", "balance",
}

_TABLE_HEADER_KEYWORDS = {
    "service", "description", "amount", "charge", "quantity", "rate",
    "circuit", "plan", "usage", "device", "number", "period", "mrc",
    "nrc", "total",
}


def extract_layout_data(
    pdf_path: Path,
    max_pages: int = 30,
) -> tuple[LayoutFeatures, list[LayoutSpan], list[tuple[float, float]]]:
    """Extract layout features and span metadata from a PDF."""
    doc = fitz.open(str(pdf_path))
    features = LayoutFeatures(page_count=len(doc))
    spans: list[LayoutSpan] = []
    page_sizes: list[tuple[float, float]] = []

    pages_to_scan = min(max_pages, len(doc))

    for page_idx in range(pages_to_scan):
        page = doc[page_idx]
        page_sizes.append((page.rect.width, page.rect.height))
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

        for block in blocks:
            if block["type"] != 0:
                continue

            x0 = int(block["bbox"][0] / 10) * 10
            y0 = int(block["bbox"][1] / 10) * 10
            features.text_block_positions.append((x0, y0))

            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"].strip()
                    size = round(span["size"], 1)
                    span_bbox = span.get("bbox", block["bbox"])

                    if size not in features.font_sizes_used:
                        features.font_sizes_used.append(size)

                    spans.append(
                        LayoutSpan(
                            text=text,
                            page=page_idx,
                            x0=span_bbox[0],
                            y0=span_bbox[1],
                            font_size=size,
                        )
                    )

                    if _is_label(text):
                        features.first_page_labels.append(text)

                    if _is_table_header(text):
                        features.table_header_texts.append(text)

    doc.close()

    features.font_sizes_used.sort()
    features.first_page_labels.sort()
    features.table_header_texts.sort()

    return features, spans, page_sizes


def extract_layout_features(pdf_path: Path, max_pages: int = 30) -> LayoutFeatures:
    """Extract structural features from a PDF."""
    features, _, _ = extract_layout_data(pdf_path, max_pages=max_pages)
    return features


def fingerprint_pdf(
    pdf_path: Path,
    *,
    profile: SupplierProfile | None = None,
    max_pages: int = 30,
) -> tuple[str, LayoutFeatures]:
    """Extract features and compute the layout fingerprint.

    Uses profile-scoped fingerprinting when ``profile.layout_fingerprint`` is
    configured; otherwise falls back to the legacy generic heuristic hash.
    """
    features, spans, _ = extract_layout_data(pdf_path, max_pages=max_pages)

    if profile is not None and profile.layout_fingerprint is not None:
        config = layout_fingerprint_config_from_profile(profile)
        fingerprint = compute_fingerprint_profile(features, spans, config)
    else:
        fingerprint = compute_fingerprint_legacy(features)

    return fingerprint, features


def compute_fingerprint(features: LayoutFeatures) -> str:
    """Compute the legacy structural fingerprint."""
    return compute_fingerprint_legacy(features)


def compute_fingerprint_legacy(features: LayoutFeatures) -> str:
    """Compute a SHA-256 hash of the layout identity using generic heuristics.

    Font sizes are deliberately excluded (presentation noise — see
    ``compute_fingerprint_profile``).
    """
    payload = {
        "labels": _legacy_structural_tokens(features.first_page_labels)[:30],
        "table_headers": _legacy_structural_tokens(features.table_header_texts)[:20],
    }
    return _hash_payload(payload)


def compute_fingerprint_profile(
    features: LayoutFeatures,
    spans: list[LayoutSpan],
    config: ResolvedFingerprintConfig,
) -> str:
    """Profile-scoped structural fingerprint driven by supplier profile config.

    Keyed on the anchor labels/headers only. Font sizes are deliberately NOT
    hashed: they are presentation noise (a shorter bill that omits a fine-print
    section uses fewer font sizes), which caused same-layout invoices to drift to
    different fingerprints.
    """
    labels, headers = _profile_structural_tokens(spans, config)
    payload = {
        "labels": labels[:30],
        "table_headers": headers[:20],
    }
    return _hash_payload(payload)


def layout_fingerprint_config_from_profile(profile: SupplierProfile) -> ResolvedFingerprintConfig:
    """Build the effective fingerprint config from profile fields + layout rules."""
    from stencil.fields.loader import resolve_merged_field_schema

    hints = profile.line_item_hints
    rules = profile.layout_fingerprint or LayoutFingerprintRules()
    schema = resolve_merged_field_schema(profile)

    table_anchors = _dedupe_strings(
        list(hints.detail_table_anchors) + list(hints.table_column_labels)
    )
    summary_anchors = _dedupe_strings(
        ["Due Date", "Billing Account Number", *rules.summary_anchors]
    )

    return ResolvedFingerprintConfig(
        header_anchors=_non_empty(
            *[
                field.label_hint
                for field in schema.document_fields()
                if field.label_hint
            ]
        ),
        table_anchors=table_anchors,
        summary_anchors=summary_anchors,
        tax_role_keywords=list(hints.tax_keywords),
        currency_codes=list(rules.currency_codes),
        ignore_label_patterns=list(rules.ignore_label_patterns),
        optional_column_patterns=list(rules.optional_column_patterns),
        exclude_span_patterns=list(rules.exclude_span_patterns or _DEFAULT_EXCLUDE_SPAN_PATTERNS),
    )


def compute_layout_family_key(
    features: LayoutFeatures,
    *,
    supplier: str,
    output_type: str,
) -> str:
    """Compute a softer supplier/layout grouping for dry-run candidate lookup."""
    payload = {
        "supplier": supplier.strip().lower(),
        "output_type": output_type.strip().lower(),
        "labels": _legacy_structural_tokens(features.first_page_labels)[:30],
        "table_headers": _legacy_structural_tokens(features.table_header_texts)[:20],
    }
    return "family:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _hash_payload(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def _non_empty(*values: str | None) -> list[str]:
    return [value for value in values if value]


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def _compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(pattern) for pattern in patterns]


def _matches_anchor(text: str, anchor: str) -> bool:
    lower = text.lower().strip().rstrip(":")
    anchor_lower = anchor.lower().strip().rstrip(":")
    if not anchor_lower:
        return False
    if lower == anchor_lower:
        return True
    if lower.startswith(anchor_lower) and len(lower) <= len(anchor_lower) + 25:
        if len(lower.split()) <= max(len(anchor_lower.split()) + 2, 4):
            return True
    return False


def _contains_tax_role(text: str, keywords: list[str]) -> bool:
    lower = text.lower()
    return any(re.search(rf"\b{re.escape(keyword.lower())}\b", lower) for keyword in keywords)


def _normalize_profile_token(text: str, config: ResolvedFingerprintConfig) -> str:
    token = " ".join(text.lower().split()).strip(": ")
    if not token or re.search(r"\d", token):
        return ""

    for keyword in config.tax_role_keywords:
        token = re.sub(rf"\b{re.escape(keyword.lower())}\b", "tax", token)
    token = re.sub(r"\bexcluding\b", "exclusive of", token)
    for code in config.currency_codes:
        token = re.sub(rf"\b{re.escape(code.lower())}\b", "", token)
    token = re.sub(r"\s+", " ", token).strip()
    if token in {"tax"}:
        return ""
    if re.search(r"(?i)purposes only", token):
        return ""
    return token


def _profile_structural_tokens(
    spans: list[LayoutSpan],
    config: ResolvedFingerprintConfig,
) -> tuple[list[str], list[str]]:
    ignore_res = _compile_patterns(config.ignore_label_patterns)
    optional_res = _compile_patterns(config.optional_column_patterns)
    exclude_res = _compile_patterns(config.exclude_span_patterns)

    labels: set[str] = set()
    headers: set[str] = set()

    for span in spans:
        text = span.text.strip()
        if not text or len(text) > 60 or _should_exclude_span(text, exclude_res):
            continue
        if any(pattern.search(text) for pattern in ignore_res):
            continue
        if any(pattern.search(text) for pattern in optional_res):
            continue

        matches_table = any(_matches_anchor(text, anchor) for anchor in config.table_anchors)
        matches_label = any(
            _matches_anchor(text, anchor)
            for anchor in config.header_anchors + config.summary_anchors
        )
        matches_tax = _contains_tax_role(text, config.tax_role_keywords)

        if not matches_table and not matches_label and not matches_tax:
            continue

        token = _normalize_profile_token(text, config)
        if not token:
            continue

        if matches_table or (matches_tax and _looks_like_table_context(text)):
            headers.add(token)
        if matches_label or matches_tax:
            labels.add(token)

    return sorted(labels), sorted(headers)


def _looks_like_table_context(text: str) -> bool:
    lower = text.lower()
    return any(keyword in lower for keyword in ("rate", "amount", "exclusive", "net charges", "current charges"))


def _should_exclude_span(text: str, exclude_res: list[re.Pattern[str]]) -> bool:
    if not text:
        return True
    stripped = text.strip()
    if stripped.endswith(".") or stripped.endswith("?"):
        return True
    if len(stripped.split()) > 4 and not stripped.rstrip().endswith(":"):
        return True
    if "@" in stripped.lower():
        return True
    lower = stripped.lower()
    return any(pattern.search(lower) for pattern in exclude_res)


def _legacy_structural_tokens(values: list[str]) -> list[str]:
    """Normalized, digit-free, deduplicated label texts (legacy mode)."""
    tokens: set[str] = set()
    for value in values:
        normalized = " ".join(value.lower().split()).strip(": ")
        if not normalized or re.search(r"\d", normalized):
            continue
        tokens.add(normalized)
    return sorted(tokens)


def _is_label(text: str) -> bool:
    if not text or len(text) > 60 or len(text) < 3:
        return False
    if "(" in text or ")" in text or len(text.split()) > 6:
        return False
    lower = text.lower().rstrip(":").strip()
    return any(keyword in lower for keyword in _LABEL_KEYWORDS)


def _is_table_header(text: str) -> bool:
    if not text or len(text) > 30 or len(text) < 2:
        return False
    if "(" in text or ")" in text or len(text.split()) > 4:
        return False
    lower = text.lower().strip()
    return any(keyword in lower for keyword in _TABLE_HEADER_KEYWORDS)
