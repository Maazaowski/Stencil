"""One-shot migration of supplier profile JSON to the slim, output-spec schema.

Relocates legacy top-level keys into ``advanced`` / ``delivery`` / ``notes``.
Legacy ``header_mapping`` is converted to ``field_overrides`` and dropped.

Idempotent: a file already in the new shape round-trips unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

from stencil.profiles.schema import SupplierProfile

_HEADER_LABEL_FIELD_MAP = {
    "invoice_number_label": "invoice_number",
    "invoice_date_label": "invoice_date",
    "due_date_label": "due_date",
    "account_number_label": "account_number",
    "total_due_label": "total_due",
    "currency_label": "currency",
}


def _header_mapping_to_field_overrides(header_mapping: dict) -> list[dict]:
    overrides: list[dict] = []
    for label_key, field_name in _HEADER_LABEL_FIELD_MAP.items():
        label = header_mapping.get(label_key)
        if label:
            overrides.append({
                "name": field_name,
                "scope": "document",
                "type": "string",
                "role": "none",
                "label_hint": label,
                "required": False,
                "enum_values": [],
                "description": "",
            })
    return overrides


def _strip_header_mapping(advanced: dict) -> dict:
    cleaned = dict(advanced)
    cleaned.pop("header_mapping", None)
    return cleaned


def migrate_profile_dict(old: dict) -> dict:
    """Return the new-shape profile dict for an old- or new-shape input."""
    legacy_header = old.get("header_mapping") or (old.get("advanced") or {}).get("header_mapping") or {}

    if "advanced" in old:
        advanced = _strip_header_mapping(old.get("advanced") or {})
        delivery = old.get("delivery") or {}
        notes = old.get("notes")
    else:
        advanced = {
            "document_structure": old.get("document_structure") or {},
            "line_item_hints": old.get("line_item_hints") or {},
            "include_zero_amount_line_items": old.get("include_zero_amount_line_items", False),
        }
        delivery = {
            "inbound_path": old.get("inbound_path"),
            "output_path": old.get("output_path"),
        }
        notes = old.get("notes") or old.get("extraction_notes") or old.get("billing_reference_hint")

    field_overrides = old.get("field_overrides")
    if not field_overrides and legacy_header:
        field_overrides = _header_mapping_to_field_overrides(legacy_header)

    return {
        "profile_id": old["profile_id"],
        "version": old.get("version", 1),
        "status": old.get("status", "draft"),
        "layout_description": old.get("layout_description"),
        "identity": old.get("identity") or {"canonical_name": old["profile_id"]},
        "classification": {"output_type": (old.get("classification") or {}).get("output_type", "standard")},
        "output_spec_id": old.get("output_spec_id", "temforce.standard"),
        "field_schema_id": old.get("field_schema_id", "invoice.standard"),
        "field_overrides": field_overrides or [],
        "delivery": delivery,
        "notes": notes,
        "advanced": advanced,
        "layout_fingerprint": old.get("layout_fingerprint"),
        "training_config": old.get("training_config") or {},
        "owner": old.get("owner"),
        "created_date": old.get("created_date"),
        "last_updated_date": old.get("last_updated_date"),
    }


def migrate_profile_file(path: Path) -> bool:
    """Rewrite one profile JSON in place to the slim schema. Returns True on change."""
    old = json.loads(path.read_text(encoding="utf-8"))
    profile = SupplierProfile.model_validate(migrate_profile_dict(old))
    new_text = json.dumps(profile.model_dump(mode="json"), indent=2) + "\n"
    if new_text == path.read_text(encoding="utf-8"):
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def migrate_dir(directory: Path) -> list[str]:
    """Migrate every ``*.json`` profile in a directory. Returns changed file names."""
    changed: list[str] = []
    for path in sorted(directory.glob("*.json")):
        try:
            if migrate_profile_file(path):
                changed.append(path.name)
        except Exception as exc:  # noqa: BLE001 — report and continue
            print(f"  ! {path.name}: {exc}")
    return changed


if __name__ == "__main__":
    import sys

    targets = sys.argv[1:] or ["supplier_profiles"]
    for target in targets:
        p = Path(target)
        if p.is_dir():
            changed = migrate_dir(p)
            print(f"{target}: migrated {len(changed)} file(s): {', '.join(changed) or '(none)'}")
        elif p.is_file():
            print(f"{target}: {'migrated' if migrate_profile_file(p) else 'unchanged'}")
        else:
            print(f"{target}: not found")
