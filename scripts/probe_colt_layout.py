"""Probe coordinate-aware extraction on the Colt sample invoices.

This is a read-only Phase 0 helper. It compares pymupdf4llm markdown output
with a lightweight custom reconstruction based on PyMuPDF word coordinates.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path


import pymupdf as fitz  # noqa: E402


AMOUNT_RE = re.compile(r"^-?\d[\d,]*\.\d{2}$")
ID_RE = re.compile(r"^\d{6,}$")


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@dataclass
class VisualRow:
    y: float
    text: str
    cells: dict[str, str] = field(default_factory=dict)


@dataclass
class ServiceItem:
    service_id: str
    billing_reference: str | None
    description: str
    amount: str
    page: int
    source_rows: list[str]


def parse_decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def words_for_page(page: fitz.Page) -> list[Word]:
    words = []
    for raw in page.get_text("words"):
        x0, y0, x1, y1, text = raw[:5]
        words.append(Word(float(x0), float(y0), float(x1), float(y1), str(text)))
    return words


def cluster_rows(words: list[Word], tolerance: float = 3.0) -> list[list[Word]]:
    rows: list[list[Word]] = []
    centers: list[float] = []
    for word in sorted(words, key=lambda w: (w.y0, w.x0)):
        cy = (word.y0 + word.y1) / 2
        match = None
        for idx, center in enumerate(centers):
            if abs(cy - center) <= tolerance:
                match = idx
                break
        if match is None:
            centers.append(cy)
            rows.append([word])
        else:
            rows[match].append(word)
            centers[match] = (centers[match] * (len(rows[match]) - 1) + cy) / len(rows[match])
    return [sorted(row, key=lambda w: w.x0) for _, row in sorted(zip(centers, rows), key=lambda item: item[0])]


def classify_cell(word: Word) -> str:
    if word.x0 < 90:
        return "left_id"
    if word.x0 >= 740:
        return "amount"
    if word.x0 >= 600:
        return "rollup"
    if word.x0 >= 380:
        return "right_mid"
    return "description"


def visual_rows_for_page(page: fitz.Page) -> list[VisualRow]:
    rows = []
    for row_words in cluster_rows(words_for_page(page)):
        cells: dict[str, list[str]] = {}
        for word in row_words:
            cells.setdefault(classify_cell(word), []).append(word.text)
        cell_text = {name: " ".join(parts) for name, parts in cells.items()}
        text = " ".join(word.text for word in row_words)
        y = sum((word.y0 + word.y1) / 2 for word in row_words) / len(row_words)
        rows.append(VisualRow(y=y, text=text, cells=cell_text))
    return rows


def service_section(rows: list[VisualRow]) -> list[VisualRow]:
    start = None
    for idx, row in enumerate(rows):
        if "SERVICE LEVEL ACTIVITY" in row.text.upper():
            start = idx + 1
            break
    if start is None:
        return []
    end = len(rows)
    for idx in range(start, len(rows)):
        text = rows[idx].text.upper()
        if text.startswith("SUMMARY") or "INVOICE TOTAL" in text or text.startswith("TAX SUMMARY"):
            end = idx
            break
    return rows[start:end]


def extract_custom_items(pdf_path: Path) -> list[ServiceItem]:
    doc = fitz.open(str(pdf_path))
    items: list[ServiceItem] = []
    for page_index, page in enumerate(doc):
        rows = service_section(visual_rows_for_page(page))
        pending: dict[str, str | None] = {}
        source_rows: list[str] = []
        for row in rows:
            text = row.text.strip()
            if not text:
                continue
            left_id = row.cells.get("left_id", "").strip()
            amount_text = row.cells.get("amount", "").replace(" ", "").strip()
            rollup = row.cells.get("rollup", "").strip()
            desc = row.cells.get("description", "").strip()

            if ID_RE.fullmatch(left_id):
                pending = {
                    "parent_id": left_id,
                    "description": desc or None,
                }
                source_rows = [text]
                continue

            if pending and desc and not pending.get("description") and not ID_RE.fullmatch(desc):
                pending["description"] = desc
                source_rows.append(text)

            if pending and rollup.lower().startswith("total") and AMOUNT_RE.fullmatch(amount_text):
                total_parts = rollup.split()
                parent_id = total_parts[1] if len(total_parts) >= 2 and ID_RE.fullmatch(total_parts[1]) else str(pending["parent_id"])
                amount = parse_decimal(amount_text)
                if amount is not None:
                    items.append(
                        ServiceItem(
                            service_id=parent_id,
                            billing_reference=parent_id,
                            description=str(pending.get("description") or ""),
                            amount=f"{amount:.2f}",
                            page=page_index + 1,
                            source_rows=source_rows + [text],
                        )
                    )
                pending = {}
                source_rows = []
                continue

            if pending:
                source_rows.append(text)
    doc.close()
    return items


def pymupdf4llm_excerpt(pdf_path: Path) -> str:
    try:
        import pymupdf4llm
    except Exception as exc:  # pragma: no cover - probe script
        return f"pymupdf4llm unavailable: {exc}"
    markdown = pymupdf4llm.to_markdown(str(pdf_path))
    upper = markdown.upper()
    idx = upper.find("SERVICE LEVEL ACTIVITY")
    if idx == -1:
        return markdown[:2000]
    return markdown[max(0, idx - 500): idx + 2500]


def page_table_detection(pdf_path: Path) -> list[int]:
    doc = fitz.open(str(pdf_path))
    counts = []
    for page in doc:
        finder = page.find_tables()
        counts.append(len(finder.tables))
    doc.close()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_dir", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args()

    pdfs = sorted(args.pdf_dir.glob("*.pdf"))
    results = []
    for pdf in pdfs:
        items = extract_custom_items(pdf)
        result = {
            "file": pdf.name,
            "find_tables_counts": page_table_detection(pdf),
            "custom_items": [asdict(item) for item in items],
            "custom_count": len(items),
            "custom_total": f"{sum((parse_decimal(item.amount) or Decimal('0')) for item in items):.2f}",
        }
        if args.markdown:
            result["pymupdf4llm_excerpt"] = pymupdf4llm_excerpt(pdf)
        results.append(result)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for result in results:
            print(f"\n== {result['file']} ==")
            print(f"find_tables per page: {result['find_tables_counts']}")
            print(f"custom count: {result['custom_count']} total: {result['custom_total']}")
            for item in result["custom_items"]:
                print(f"  {item['service_id']} | {item['amount']} | {item['description']}")
            if args.markdown:
                print("\n-- pymupdf4llm excerpt --")
                print(result["pymupdf4llm_excerpt"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
