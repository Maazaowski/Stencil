"""Migrate supplier profile JSON files to the simplified schema.

Strips removed fields and bumps version.  Safe to run multiple times.
"""

import json
import sys
from pathlib import Path

PROFILES_DIR = Path(__file__).resolve().parent.parent / "supplier_profiles"

REMOVED_IDENTITY_FIELDS = {"domains", "invoice_number_patterns", "account_number_patterns"}
REMOVED_CLASSIFICATION_FIELDS = {"keywords", "page_count_hint"}
REMOVED_DOCUMENT_STRUCTURE_FIELDS = {"header_pages", "table_header_keywords"}
REMOVED_LINE_ITEM_HINTS_FIELDS = {"service_id_patterns"}
REMOVED_TOP_LEVEL_FIELDS = {"reconciliation", "extraction_model_template"}


def migrate_profile(path: Path) -> bool:
    """Migrate a single profile file.  Returns True if the file was modified."""
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False

    # Identity
    if "identity" in data:
        for field in REMOVED_IDENTITY_FIELDS:
            if field in data["identity"]:
                del data["identity"][field]
                changed = True

    # Classification
    if "classification" in data:
        for field in REMOVED_CLASSIFICATION_FIELDS:
            if field in data["classification"]:
                del data["classification"][field]
                changed = True

    # Document structure
    if "document_structure" in data:
        for field in REMOVED_DOCUMENT_STRUCTURE_FIELDS:
            if field in data["document_structure"]:
                del data["document_structure"][field]
                changed = True

    # Line item hints
    if "line_item_hints" in data:
        for field in REMOVED_LINE_ITEM_HINTS_FIELDS:
            if field in data["line_item_hints"]:
                del data["line_item_hints"][field]
                changed = True

    # Top-level
    for field in REMOVED_TOP_LEVEL_FIELDS:
        if field in data:
            del data[field]
            changed = True

    if changed:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return changed


def main() -> None:
    profiles = sorted(PROFILES_DIR.glob("*.json"))
    if not profiles:
        print(f"No profiles found in {PROFILES_DIR}")
        sys.exit(1)

    for path in profiles:
        modified = migrate_profile(path)
        status = "MIGRATED" if modified else "unchanged"
        print(f"  {path.name}: {status}")

    print(f"\nDone — processed {len(profiles)} profile(s).")


if __name__ == "__main__":
    main()
