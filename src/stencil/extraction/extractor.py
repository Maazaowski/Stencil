"""AI-based invoice extraction using structured LLM outputs."""

import base64
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz  # pymupdf
import structlog

from stencil import runtime_settings
from stencil.ai_debug import traced_chat_completion
from stencil.config import settings
from stencil.extraction.ai_json import completion_to_json
from stencil.extraction.layout import extract_layout_document, render_layout_text
from stencil.extraction.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    build_extraction_user_prompt,
)
from stencil.fields.loader import resolve_merged_field_schema
from stencil.fields.schema_builder import build_extraction_json_schema
from stencil.llm import get_ai_client
from stencil.profiles.loader import get_profile
from stencil.validation.document_io import build_extracted_from_ai_raw
from stencil.validation.schema import ExtractedDocument

logger = structlog.get_logger()

# Legacy static schema - default invoice.standard built-in equivalent
EXTRACTION_JSON_SCHEMA = build_extraction_json_schema(
    __import__("stencil.fields.loader", fromlist=["default_field_schema"]).default_field_schema()
)


@dataclass
class ExtractionResult:
    raw_data: dict
    tokens_input: int = 0
    tokens_output: int = 0
    duration_ms: int = 0
    ai_model_name: str = ""
    ai_calls: list[dict[str, Any]] | None = None
    artifacts: dict[str, Any] | None = None


def extract_invoice(pdf_path: Path, supplier_name: str | None = None,
                    output_type: str = "standard",
                    supplier_profile_id: str | None = None,
                    field_schema=None,
                    artifact_dir: Path | None = None,
                    supplier_profile=None,
                    page_numbers: list[int] | None = None) -> ExtractionResult:
    """Extract invoice data via OpenAI Structured Outputs.

    Text-first: the PDF's embedded text layer is the primary input (accurate
    for numbers). Page images are attached ONLY for pages that lack a usable
    text layer (scanned/image-only pages that genuinely need vision). For a
    born-digital invoice this means zero images - far faster and cheaper.

    ``supplier_profile`` is an optional in-memory profile that takes precedence
    over ``supplier_profile_id`` — used to re-extract with an unsaved draft
    profile (e.g. the profile-authoring assistant) without persisting it first.

    ``page_numbers`` (1-based) restricts extraction to a subset of pages. Used by
    the sample-authoring path to read a dozen representative pages of a very long
    document instead of all of them; ``None`` reads everything, as before.
    """
    client = get_ai_client(purpose="extraction")

    profile = supplier_profile
    profile_data = None
    schema = field_schema
    if profile is not None:
        profile_data = profile.model_dump()
        if schema is None:
            schema = resolve_merged_field_schema(profile)
    elif supplier_profile_id:
        profile = get_profile(supplier_profile_id)
        if profile:
            profile_data = profile.model_dump()
            if schema is None:
                schema = resolve_merged_field_schema(profile)

    if schema is None:
        from stencil.fields.loader import default_field_schema
        schema = default_field_schema()

    mode = (runtime_settings.ai_extraction_mode() or "compact_chunked").lower()
    if mode == "compact_chunked":
        from stencil.extraction.compact_chunked import extract_invoice_compact_chunked

        return extract_invoice_compact_chunked(
            pdf_path,
            client=client,
            supplier_name=supplier_name,
            output_type=output_type,
            supplier_profile=profile,
            supplier_profile_data=profile_data,
            field_schema=schema,
            artifact_dir=artifact_dir,
            page_numbers=page_numbers,
        )
    if mode != "legacy":
        raise ValueError(
            f"Unsupported ST_AI_EXTRACTION_MODE={mode!r}; "
            "expected 'compact_chunked' or 'legacy'."
        )

    json_schema = build_extraction_json_schema(schema)

    # Extract raw text for scanned-page detection, then use the shared layout
    # representation as the default AI substrate for born-digital pages.
    raw_page_texts = _extract_flat_text(pdf_path)
    layout_markdown = None
    if settings.extraction_use_layout_text:
        layout_document = extract_layout_document(pdf_path)
        page_texts = render_layout_text(layout_document)
        layout_markdown = layout_document.markdown
    else:
        page_texts = raw_page_texts

    # Only render images for pages whose text layer is too thin to trust
    # (likely scanned). Born-digital pages are handled by text alone.
    image_page_indexes: list[int] = []
    if settings.extraction_send_page_images:
        image_page_indexes = [
            i for i, text in enumerate(raw_page_texts)
            if len((text or "").strip()) < settings.extraction_min_page_text_chars
        ]
    page_images = _render_pages(pdf_path, image_page_indexes) if image_page_indexes else []

    user_prompt = build_extraction_user_prompt(
        supplier_profile=profile_data,
        supplier_name=supplier_name,
        output_type=output_type,
        field_schema=schema,
    )

    # Append extracted text - this is the source of truth for the extraction
    text_section = "\n\n--- COORDINATE-AWARE PDF TEXT (source of truth for all values) ---\n"
    text_section += (
        "Rows and cells are reconstructed from PDF coordinates. Extract line items from these "
        "visual rows/tables, not from flat reading order.\n"
    )
    for i, text in enumerate(page_texts):
        text_section += f"\n=== PAGE {i + 1} ===\n{text}\n"
    if layout_markdown:
        text_section += "\n--- OPTIONAL MARKDOWN VIEW (layout supplement, not authoritative) ---\n"
        text_section += layout_markdown[:60000]
    user_prompt += text_section
    if page_images:
        user_prompt += (
            "\n\nNote: a few pages had no readable text layer and are attached as images below - "
            "use them to read those pages."
        )

    content: list[dict] = [{"type": "text", "text": user_prompt}]
    for img in page_images:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img}",
                          "detail": settings.extraction_image_detail},
        })

    messages = [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]

    model_name = runtime_settings.openai_model_extraction()
    start = time.time()
    response = traced_chat_completion(
        client,
        call_type="extraction",
        context=supplier_name,
        model=model_name,
        messages=messages,
        response_format={"type": "json_schema", "json_schema": json_schema},
        max_completion_tokens=runtime_settings.openai_max_output_tokens(),
        timeout=runtime_settings.openai_timeout(),
    )
    duration_ms = int((time.time() - start) * 1000)

    data = _completion_to_json(response)
    usage = response.usage

    result = ExtractionResult(
        raw_data=data,
        tokens_input=usage.prompt_tokens if usage else 0,
        tokens_output=usage.completion_tokens if usage else 0,
        duration_ms=duration_ms,
        ai_model_name=model_name,
    )

    logger.info("extraction.completed", supplier=supplier_name,
                line_items=len(data.get("line_items", [])),
                tokens=result.tokens_input + result.tokens_output,
                pages=len(page_texts),
                images_sent=len(page_images),
                duration_ms=duration_ms)

    return result


def build_extracted_document(raw_data: dict, intake_id: str,
                             extraction_result: ExtractionResult,
                             output_type: str = "standard",
                             supplier_profile_id: str | None = None,
                             layout_fingerprint: str | None = None,
                             page_count: int = 0,
                             field_schema=None) -> ExtractedDocument:
    """Convert raw AI extraction output into a validated ExtractedDocument."""

    from stencil.fields.loader import default_field_schema, resolve_merged_field_schema
    from stencil.pricing import estimate_cost_usd
    from stencil.profiles.loader import get_profile
    from stencil.validation.schema import ExtractionMetadata, ExtractionPath

    schema = field_schema
    currency_rules = None
    if supplier_profile_id:
        profile = get_profile(supplier_profile_id)
        if profile:
            currency_rules = profile.advanced.currency
            if schema is None:
                schema = resolve_merged_field_schema(profile)
    if schema is None:
        schema = default_field_schema()

    estimated_cost = estimate_cost_usd(
        extraction_result.ai_model_name,
        extraction_result.tokens_input,
        extraction_result.tokens_output,
    )

    metadata = ExtractionMetadata(
        extraction_path=ExtractionPath.AI,
        ai_model_name=extraction_result.ai_model_name,
        supplier_profile_id=supplier_profile_id,
        layout_fingerprint=layout_fingerprint,
        total_pages=page_count,
        tokens_input=extraction_result.tokens_input,
        tokens_output=extraction_result.tokens_output,
        estimated_cost_usd=estimated_cost,
        extraction_duration_ms=extraction_result.duration_ms,
        overall_confidence=raw_data.get("overall_confidence", 0.0),
    )

    doc = build_extracted_from_ai_raw(
        raw_data,
        intake_id=intake_id,
        field_schema=schema,
        metadata=metadata,
        output_type=output_type,
        currency_rules=currency_rules,
    )
    return doc


def build_canonical_invoice(raw_data: dict, intake_id: str,
                            extraction_result: ExtractionResult,
                            output_type: str = "standard",
                            supplier_profile_id: str | None = None,
                            layout_fingerprint: str | None = None,
                            page_count: int = 0,
                            field_schema=None) -> ExtractedDocument:
    """Backward-compatible alias for ``build_extracted_document``."""
    return build_extracted_document(
        raw_data, intake_id, extraction_result,
        output_type=output_type,
        supplier_profile_id=supplier_profile_id,
        layout_fingerprint=layout_fingerprint,
        page_count=page_count,
        field_schema=field_schema,
    )


def _completion_to_json(response) -> dict:
    """Parse a chat-completion into JSON, with a clear error when the model
    returned no content (e.g. the token budget was exhausted by reasoning, or
    the model refused). Avoids the cryptic 'Expecting value: line 1 column 1'.
    """
    return completion_to_json(
        response,
        max_output_tokens=runtime_settings.openai_max_output_tokens(),
    )



def _render_pages(pdf_path: Path, page_indexes: list[int],
                  max_pages: int = 30) -> list[str]:
    """Render only the given page indexes to base64 PNG (for pages lacking text)."""
    doc = fitz.open(str(pdf_path))
    images = []
    dpi = settings.extraction_image_dpi
    for i in page_indexes[:max_pages]:
        if i >= len(doc):
            continue
        pix = doc[i].get_pixmap(dpi=dpi)
        images.append(base64.b64encode(pix.tobytes("png")).decode())
    doc.close()
    return images


def _extract_all_text(pdf_path: Path, max_pages: int | None = None) -> list[str]:
    """Extract the configured text substrate for extraction/model authoring."""
    if settings.extraction_use_layout_text:
        return render_layout_text(extract_layout_document(pdf_path, max_pages=max_pages))
    return _extract_flat_text(pdf_path, max_pages=max_pages)


def _extract_flat_text(pdf_path: Path, max_pages: int | None = None) -> list[str]:
    """Extract raw text from PDF pages using pymupdf."""
    doc = fitz.open(str(pdf_path))
    texts = []
    for i in range(len(doc) if max_pages is None else min(max_pages, len(doc))):
        texts.append(doc[i].get_text())
    doc.close()
    return texts
