"""AI-based invoice classification."""

import base64
import time
from dataclasses import dataclass
from pathlib import Path

import fitz  # pymupdf
import structlog

from stencil import runtime_settings
from stencil.ai_debug import traced_chat_completion
from stencil.extraction.prompts import (
    CLASSIFICATION_SYSTEM_PROMPT,
    CLASSIFICATION_USER_PROMPT,
)
from stencil.llm import get_ai_client
from stencil.profiles.loader import get_active_profiles

logger = structlog.get_logger()


@dataclass
class ClassificationResult:
    supplier_name: str
    output_type: str
    confidence: float
    tokens_input: int = 0
    tokens_output: int = 0
    duration_ms: int = 0


def classify_invoice(pdf_path: Path, max_pages: int = 2) -> ClassificationResult:
    """Send first page(s) of a PDF to the active LLM provider for supplier classification."""
    client = get_ai_client(purpose="classification")

    page_images = _render_pages_to_base64(pdf_path, max_pages)

    known_suppliers = [p.identity.canonical_name for p in get_active_profiles()]
    user_prompt = CLASSIFICATION_USER_PROMPT.format(
        known_suppliers=", ".join(known_suppliers) if known_suppliers else "None configured"
    )

    messages = [
        {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                *[
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img}", "detail": "high"},
                    }
                    for img in page_images
                ],
            ],
        },
    ]

    start = time.time()
    response = traced_chat_completion(
        client,
        call_type="classification",
        context=pdf_path.name,
        model=runtime_settings.openai_model_classification(),
        messages=messages,
        response_format={"type": "json_schema", "json_schema": {
            "name": "classification_result",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "supplier_name": {"type": "string"},
                    "output_type": {"type": "string", "description": "Short document category label, e.g. 'standard'"},
                    "confidence": {"type": "number"},
                },
                "required": ["supplier_name", "output_type", "confidence"],
                "additionalProperties": False,
            },
        }},
        max_completion_tokens=2048,
        timeout=runtime_settings.openai_timeout(),
    )
    duration_ms = int((time.time() - start) * 1000)

    import json
    content = response.choices[0].message.content
    if not (isinstance(content, str) and content.strip()):
        finish_reason = getattr(response.choices[0], "finish_reason", None)
        raise ValueError(f"Classification returned no content (finish_reason={finish_reason})")
    data = json.loads(content)
    usage = response.usage

    result = ClassificationResult(
        supplier_name=data["supplier_name"],
        output_type=data["output_type"],
        confidence=data["confidence"],
        tokens_input=usage.prompt_tokens if usage else 0,
        tokens_output=usage.completion_tokens if usage else 0,
        duration_ms=duration_ms,
    )

    logger.info("classification.completed", supplier=result.supplier_name,
                output_type=result.output_type, confidence=result.confidence,
                tokens=result.tokens_input + result.tokens_output)

    return result


def _render_pages_to_base64(pdf_path: Path, max_pages: int) -> list[str]:
    """Render PDF pages to PNG base64 strings for the vision API."""
    doc = fitz.open(str(pdf_path))
    images = []
    for i in range(min(max_pages, len(doc))):
        page = doc[i]
        pix = page.get_pixmap(dpi=200)
        images.append(base64.b64encode(pix.tobytes("png")).decode())
    doc.close()
    return images
