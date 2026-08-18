"""Compile a supplier profile + field schema + free-text notes into ONE instruction set.

Historically each prompt builder stitched together structured profile hints and the
free-text ``notes`` blob ad hoc, with precedence expressed only as prose and no way
to tell that a note contradicted an executable field. This module is the single
place that:

1. Renders structured profile fields as executable directives.
2. Applies an explicit precedence rule (structured fields win, unless a note line
   is marked ``override:``).
3. Detects notes-vs-structured contradictions deterministically (no AI).

Both the extraction prompt and the model-authoring prompt render from here, so the
two paths can never drift apart again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# A note fragment starting with this token is authoritative and supersedes the
# structured field it contradicts. Documented, deterministic, greppable.
OVERRIDE_PREFIX = "override:"

STRUCTURED_WINS = "structured_wins"
NOTE_OVERRIDE = "note_override"

# Ordered so the rendered hint block matches the historical prompt wording.
_LAYOUT_HINT_KEYS = (
    "detail_table_anchors",
    "line_item_granularity",
    "service_id_preference",
    "billing_reference_preference",
    "service_id_column_label",
    "billing_reference_column_label",
    "amount_column_label",
    "tax_amount_column_label",
    "amount_source",
    "tax_source",
    "tax_output_mode",
    "tax_rate_source",
    "service_id_value_pattern",
    "billing_reference_value_pattern",
    "skip_row_keywords",
    "table_column_labels",
)

# --- conflict-detection vocabularies (deterministic, high precision) ---------

_TAX_PRESCRIPTION = re.compile(
    r"(ext_tax|computed\.line_tax|per-?line tax|line tax|tax rate|tax multiplier|"
    r"compute\s+(the\s+)?tax|×\s*0?\.\d+|\bx\s*0\.\d+)",
    re.IGNORECASE,
)
_PER_CHARGE_PHRASE = re.compile(r"(one|1)\s+(delivered\s+)?row\s+per\s+charge|per[-\s]charge\s+row", re.IGNORECASE)
_PER_SERVICE_PHRASE = re.compile(r"(one|1)\s+row\s+per\s+service|per[-\s]service\s+group", re.IGNORECASE)
_ISO_CODE = re.compile(
    r"\b(USD|EUR|GBP|CAD|AUD|INR|CHF|JPY|SEK|NOK|DKK|PLN|MXN|BRL|ZAR|SGD|HKD|NZD)\b"
)

_TAX_DISABLED_VALUES = {"none"}
_TAX_UNENCODED_VALUES = {None, "", "auto"}


@dataclass(frozen=True)
class Conflict:
    """One deterministic contradiction between a note and a structured field."""

    field: str
    structured_value: str
    note_fragment: str
    resolution: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "field": self.field,
            "structured_value": self.structured_value,
            "note_fragment": _truncate(self.note_fragment),
            "resolution": self.resolution,
            "message": self.message,
        }


@dataclass
class CompiledInstructions:
    """The compiled, precedence-resolved instruction set for one supplier."""

    supplier_name: str | None = None
    output_type: str = "standard"
    field_schema: Any = None
    hints: dict[str, Any] = field(default_factory=dict)
    structure: dict[str, Any] = field(default_factory=dict)
    currency: dict[str, Any] = field(default_factory=dict)
    notes_effective: list[str] = field(default_factory=list)
    overrides: list[str] = field(default_factory=list)
    ignored_notes: list[str] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)

    # -- rendering ---------------------------------------------------------

    def render(self, purpose: str = "extraction") -> str:
        """Render the profile/schema instruction block for ``extraction`` or ``authoring``.

        Callers keep their own intro/ground-truth/layout sections; this returns only
        the portion derived from the profile, field schema, and notes.
        """
        parts: list[str] = [_precedence_block()]

        if purpose == "extraction":
            parts.extend(self._extraction_field_blocks())
        else:
            parts.extend(self._authoring_field_blocks())

        if identifier_lines := self._identifier_lines():
            parts.append("\nLine-item identifier columns for this source:")
            parts.extend(identifier_lines)

        if hint_lines := self._layout_hint_lines():
            advisory = (
                "\nLayout/table hints for this source (advisory only):"
                if purpose == "extraction"
                else "\nLayout/table hints for this source (advisory; the rules must stand alone):"
            )
            parts.append(advisory)
            parts.extend(hint_lines)

        if currency_lines := self._currency_lines():
            parts.append("\nCurrency rules for this source:")
            parts.extend(currency_lines)

        if purpose == "extraction":
            if region := self._region_line():
                parts.append(region)
        elif start := self.structure.get("detail_start_marker"):
            parts.append(f"\nLine items typically start after: '{start}'")

        if conflict_lines := self._conflict_lines():
            parts.append("\n--- RESOLVED CONFLICTS (follow the structured field, not the note) ---")
            parts.extend(conflict_lines)

        if self.overrides:
            parts.append("\n--- NOTE OVERRIDES (these notes are authoritative) ---")
            parts.extend(f"  - {note}" for note in self.overrides)

        if self.notes_effective:
            parts.append("\nNotes: " + "\n\n".join(self.notes_effective))

        return "\n".join(part for part in parts if part)

    def as_dict(self) -> dict[str, Any]:
        return {
            "supplier_name": self.supplier_name,
            "output_type": self.output_type,
            "conflicts": [c.as_dict() for c in self.conflicts],
            "ignored_notes": [_truncate(n) for n in self.ignored_notes],
            "overrides": [_truncate(n) for n in self.overrides],
        }

    def conflict_warnings(self) -> list[str]:
        """Conflict messages, shaped for the extraction ``warnings`` channel."""
        return [f"Profile setup conflict: {c.message}" for c in self.conflicts]

    # -- block builders ----------------------------------------------------

    def _doc_fields(self) -> list:
        if self.field_schema is None:
            return []
        return [f for f in self.field_schema.fields if f.scope.value == "document"]

    def _row_fields(self) -> list:
        if self.field_schema is None:
            return []
        return [f for f in self.field_schema.fields if f.scope.value == "row"]

    def _extraction_field_blocks(self) -> list[str]:
        parts: list[str] = []
        doc_fields, row_fields = self._doc_fields(), self._row_fields()
        if doc_fields or row_fields:
            parts.append("\nActive extraction fields:")
            parts.extend(f"  - document.{_describe_field(f)}" for f in doc_fields)
            parts.extend(f"  - row.{_describe_field(f)}" for f in row_fields)

        label_lines = [
            f"  - document.{f.name}: extract from label/field '{f.label_hint}'"
            for f in doc_fields
            if getattr(f, "label_hint", None)
        ]
        if label_lines:
            parts.append(
                "\nDocument field labels for this source (must be checked before using null or fallback):"
            )
            parts.extend(label_lines)
            parts.append(
                "  - If both invoice_date and due_date are printed, extract them independently; "
                "do not copy due_date into invoice_date."
            )
        return parts

    def _authoring_field_blocks(self) -> list[str]:
        labels = [f"  - {f.name}: '{f.label_hint}'" for f in self._doc_fields() if f.label_hint]
        if not labels:
            return []
        return ["\nKnown document field labels for this source (hints):", *labels]

    def _identifier_lines(self) -> list[str]:
        service_col = self.hints.get("service_id_column_label")
        billing_col = self.hints.get("billing_reference_column_label")
        lines: list[str] = []
        if service_col:
            lines.append(f"  - service_id: extract from column/field labeled '{service_col}'")
        if billing_col:
            lines.append(f"  - billing_reference: extract from column/field labeled '{billing_col}'")
            lines.append(
                "    This explicit billing_reference hint overrides generic billing-reference "
                "semantics and any conflicting free-text notes."
            )
        return lines

    def _layout_hint_lines(self) -> list[str]:
        lines = []
        for key in _LAYOUT_HINT_KEYS:
            value = self.hints.get(key)
            if value not in (None, [], ""):
                lines.append(f"  - {key}: {value}")
        return lines

    def _currency_lines(self) -> list[str]:
        lines: list[str] = []
        if default_code := self.currency.get("default_code"):
            lines.append(f"  - default currency code: {default_code}")
        if allowed := self.currency.get("allowed_codes"):
            lines.append(f"  - allowed currency codes: {', '.join(allowed)}")
        if aliases := (self.currency.get("aliases") or {}):
            rendered = ", ".join(f"{alias} -> {code}" for alias, code in aliases.items())
            lines.append(f"  - normalize currency aliases: {rendered}")
        if lines:
            lines.append("  - Emit currency fields only as ISO 4217 three-letter codes.")
        return lines

    def _region_line(self) -> str | None:
        start = self.structure.get("detail_start_marker")
        end = self.structure.get("detail_end_marker")
        if start and end:
            return (
                f"\nIMPORTANT — line-item region: extract line items ONLY from the section "
                f"that starts at '{start}' and ends at '{end}'. IGNORE every charge, table, or "
                f"itemized breakdown outside this region (e.g. summary pages and any repeated "
                f"detail that appears after '{end}'). Do not extract the same charge twice."
            )
        if start:
            return f"\nLine items start after: '{start}'. Ignore charges before it."
        if end:
            return f"\nLine items end at: '{end}'. Ignore everything after it."
        return None

    def _conflict_lines(self) -> list[str]:
        return [
            f"  - {c.field} = {c.structured_value!r}: {c.message}"
            for c in self.conflicts
            if c.resolution == STRUCTURED_WINS
        ]


# --- public API -------------------------------------------------------------


def compile_instructions(
    supplier_profile: dict | None = None,
    field_schema: Any = None,
    output_type: str = "standard",
    supplier_name: str | None = None,
) -> CompiledInstructions:
    """Merge structured profile fields, the field schema, and free-text notes.

    Precedence: structured fields are executable and win; notes are supplemental,
    unless a note fragment starts with ``override:``. Contradictions are detected
    deterministically and reported on the result.
    """
    profile = supplier_profile or {}
    advanced = profile.get("advanced") or {}
    hints = advanced.get("line_item_hints") or {}
    structure = advanced.get("document_structure") or {}
    currency = advanced.get("currency") or {}

    fragments = _split_notes(profile.get("notes"))
    overrides: list[str] = []
    effective: list[str] = []
    conflicts: list[Conflict] = []
    ignored: list[str] = []

    for fragment in fragments:
        if _is_override(fragment):
            body = fragment.strip()[len(OVERRIDE_PREFIX):].strip()
            overrides.append(body)
            for conflict in _detect(hints, structure, currency, body):
                conflicts.append(
                    Conflict(
                        field=conflict.field,
                        structured_value=conflict.structured_value,
                        note_fragment=body,
                        resolution=NOTE_OVERRIDE,
                        message=(
                            f"note is marked '{OVERRIDE_PREFIX}' and supersedes the structured value; "
                            f"follow the note"
                        ),
                    )
                )
            continue

        found = _detect(hints, structure, currency, fragment)
        if found:
            conflicts.extend(found)
            ignored.append(fragment)
        effective.append(fragment)

    return CompiledInstructions(
        supplier_name=supplier_name,
        output_type=output_type,
        field_schema=field_schema,
        hints=hints,
        structure=structure,
        currency=currency,
        notes_effective=effective,
        overrides=overrides,
        ignored_notes=ignored,
        conflicts=conflicts,
    )


# --- conflict rules ---------------------------------------------------------


def _detect(hints: dict, structure: dict, currency: dict, fragment: str) -> list[Conflict]:
    out: list[Conflict] = []
    for rule in (_rule_tax_disabled, _rule_tax_rule_not_encoded, _rule_granularity, _rule_currency, _rule_region):
        if conflict := rule(hints, structure, currency, fragment):
            out.append(conflict)
    return out


def _rule_tax_disabled(hints: dict, _s: dict, _c: dict, fragment: str) -> Conflict | None:
    """Notes prescribe a per-line tax while the profile disables row tax."""
    if not _TAX_PRESCRIPTION.search(fragment):
        return None
    for key in ("tax_output_mode", "tax_source"):
        value = (hints.get(key) or "").strip().lower() if isinstance(hints.get(key), str) else hints.get(key)
        if value in _TAX_DISABLED_VALUES:
            return Conflict(
                field=f"line_item_hints.{key}",
                structured_value=str(hints.get(key)),
                note_fragment=fragment,
                resolution=STRUCTURED_WINS,
                message=(
                    "a note prescribes computing per-line tax, but row tax is disabled by this "
                    "field; leave delivered row tax blank"
                ),
            )
    return None


def _rule_tax_rule_not_encoded(hints: dict, _s: dict, _c: dict, fragment: str) -> Conflict | None:
    """Notes describe an executable tax rule that no structured field encodes.

    This is the euNetworks case: notes say ``EXT_TAX = Net Value x 0.23`` while
    ``tax_output_mode`` is unset, so delivery falls to the generic auto resolver
    instead of the rate the note describes.
    """
    if not _TAX_PRESCRIPTION.search(fragment):
        return None
    mode = hints.get("tax_output_mode")
    normalized = mode.strip().lower() if isinstance(mode, str) else mode
    if normalized not in _TAX_UNENCODED_VALUES:
        return None
    return Conflict(
        field="line_item_hints.tax_output_mode",
        structured_value=str(mode) if mode else "unset (auto)",
        note_fragment=fragment,
        resolution=STRUCTURED_WINS,
        message=(
            "a note prescribes a specific tax rule, but tax_output_mode is unset/auto so delivery "
            "uses the automatic resolver; encode the rule (e.g. tax_output_mode=calculate + "
            "tax_rate_source) if the note is meant to be executable"
        ),
    )


def _rule_granularity(hints: dict, _s: dict, _c: dict, fragment: str) -> Conflict | None:
    granularity = hints.get("line_item_granularity")
    if not granularity:
        return None
    if _PER_CHARGE_PHRASE.search(fragment) and granularity != "per_charge_row":
        return _granularity_conflict(granularity, fragment, "one row per charge")
    if _PER_SERVICE_PHRASE.search(fragment) and granularity != "per_service_group":
        return _granularity_conflict(granularity, fragment, "one row per service group")
    return None


def _granularity_conflict(granularity: str, fragment: str, described: str) -> Conflict:
    return Conflict(
        field="line_item_hints.line_item_granularity",
        structured_value=granularity,
        note_fragment=fragment,
        resolution=STRUCTURED_WINS,
        message=(
            f"a note describes '{described}', which contradicts the configured granularity; "
            f"emit rows at {granularity!r}"
        ),
    )


def _rule_currency(_h: dict, _s: dict, currency: dict, fragment: str) -> Conflict | None:
    allowed = currency.get("allowed_codes") or []
    if not allowed:
        return None
    allowed_upper = {code.upper() for code in allowed}
    mentioned = {code.upper() for code in _ISO_CODE.findall(fragment)}
    stray = sorted(mentioned - allowed_upper)
    if not stray:
        return None
    return Conflict(
        field="currency.allowed_codes",
        structured_value=", ".join(allowed),
        note_fragment=fragment,
        resolution=STRUCTURED_WINS,
        message=(
            f"a note mentions currency code(s) {', '.join(stray)} that are not in the allowed list; "
            f"emit only the allowed codes"
        ),
    )


def _rule_region(_h: dict, structure: dict, _c: dict, fragment: str) -> Conflict | None:
    start = structure.get("detail_start_marker")
    if not start:
        return None
    match = re.search(r"line items? (?:start|begin)s? (?:after|at)\s+['\"]([^'\"]+)['\"]", fragment, re.IGNORECASE)
    if not match or match.group(1).strip().lower() == str(start).strip().lower():
        return None
    return Conflict(
        field="document_structure.detail_start_marker",
        structured_value=str(start),
        note_fragment=fragment,
        resolution=STRUCTURED_WINS,
        message=(
            f"a note says line items start at {match.group(1)!r}, which contradicts the configured "
            f"marker; use the configured marker"
        ),
    )


# --- helpers ----------------------------------------------------------------


def _precedence_block() -> str:
    return (
        "\n--- INSTRUCTION PRECEDENCE ---\n"
        "1. Invoice-visible evidence always wins; surface ambiguity via warnings/exceptions.\n"
        "2. Structured profile fields below are executable rules and are authoritative.\n"
        "3. Free-text notes are supplemental guidance only. Where a note contradicts a\n"
        "   structured field, follow the structured field.\n"
        f"4. A note beginning {OVERRIDE_PREFIX!r} is authoritative and supersedes the field it names."
    )


def _split_notes(notes: str | None) -> list[str]:
    """Split free-text notes into paragraph fragments (blank-line separated)."""
    if not notes or not notes.strip():
        return []
    return [block.strip() for block in re.split(r"\n\s*\n", notes.strip()) if block.strip()]


def _is_override(fragment: str) -> bool:
    return fragment.strip().lower().startswith(OVERRIDE_PREFIX)


def _truncate(text: str, limit: int = 240) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _describe_field(field_def) -> str:
    """Render one ``name (label: 'X') — description [one of: ...]`` prompt line."""
    out = field_def.name
    if field_def.label_hint:
        out += f" (label: '{field_def.label_hint}')"
    if getattr(field_def, "date_format", None):
        out += f" (date format as printed: {field_def.date_format})"
    desc = (getattr(field_def, "description", "") or "").strip()
    if desc:
        out += f" — {desc}"
    if enum_values := (getattr(field_def, "enum_values", None) or []):
        out += f" [one of: {', '.join(enum_values)}]"
    return out
