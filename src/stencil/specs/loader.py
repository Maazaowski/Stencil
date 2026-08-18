"""Load and cache output specification JSONs (the customer deliverable contracts).

Mirrors ``stencil.profiles.loader``. The default ``temforce.standard`` spec
is always guaranteed to exist (built in code) so the system works on a fresh
install or in tests where the ``output_specs/`` directory may be absent.
"""

import re
from collections import Counter
from pathlib import Path

import structlog

from stencil.output.spec import OutputColumn, OutputSpec

logger = structlog.get_logger()

DEFAULT_OUTPUT_SPEC_ID = "temforce.standard"

_VALID_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_specs_cache: dict[str, OutputSpec] = {}


def _builtin_temforce_spec() -> OutputSpec:
    """In-code fallback equal to the shipped temforce.standard spec."""
    from stencil.output.mapper import TEMFORCE_OUTPUT_COLUMNS

    return OutputSpec(
        spec_id=DEFAULT_OUTPUT_SPEC_ID,
        name="TemForce Standard (8-column)",
        columns=[
            OutputColumn(
                header=c.xlsx_header,
                source=c.canonical_path,
                fallback=c.fallback_path,
                width=c.width,
                number_format=c.number_format,
            )
            for c in TEMFORCE_OUTPUT_COLUMNS
        ],
    )


def load_all_output_specs(specs_dir: Path | None = None) -> dict[str, OutputSpec]:
    """Load every output spec from the DB. Always includes the default spec.
    ``specs_dir`` is accepted for signature compatibility and ignored."""
    global _specs_cache
    from stencil.db import registry
    from stencil.db.session import SessionLocal

    specs: dict[str, OutputSpec] = {}
    db = SessionLocal()
    try:
        rows = registry.list_output_specs(db)
    finally:
        db.close()
    for row in rows:
        try:
            spec = OutputSpec.model_validate(row.data)
            specs[spec.spec_id] = spec
        except Exception as e:  # pragma: no cover - defensive
            logger.error("output_specs.parse_failed", spec_id=row.spec_id, error=str(e))

    if DEFAULT_OUTPUT_SPEC_ID not in specs:
        specs[DEFAULT_OUTPUT_SPEC_ID] = _builtin_temforce_spec()

    _specs_cache = specs
    logger.info("output_specs.loaded_all", count=len(specs))
    return specs


def _read_spec_row(spec_id: str) -> OutputSpec | None:
    from stencil.db import registry
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        row = registry.get_output_spec(db, spec_id)
    finally:
        db.close()
    if row is None:
        return None
    try:
        return OutputSpec.model_validate(row.data)
    except Exception as e:  # pragma: no cover - defensive
        logger.error("output_specs.parse_failed", spec_id=spec_id, error=str(e))
        return None


def get_output_spec(spec_id: str | None) -> OutputSpec:
    """Resolve a spec id to an ``OutputSpec``, falling back to the default spec.

    Reads the DB row fresh so cross-process updates are visible, mirroring
    ``profiles.loader.get_profile``.
    """
    if spec_id:
        fresh = _read_spec_row(spec_id)
        if fresh is not None:
            _specs_cache[fresh.spec_id] = fresh
            return fresh
    if not _specs_cache:
        load_all_output_specs()
    if spec_id and spec_id in _specs_cache:
        return _specs_cache[spec_id]
    return _specs_cache.get(DEFAULT_OUTPUT_SPEC_ID) or _builtin_temforce_spec()


def default_output_spec() -> OutputSpec:
    return get_output_spec(DEFAULT_OUTPUT_SPEC_ID)


def resolve_output_spec(profile) -> OutputSpec:
    """Resolve the OutputSpec a profile delivers with."""
    spec = get_output_spec(getattr(profile, "output_spec_id", None))
    hints = getattr(profile, "line_item_hints", None)
    billing_reference_preference = getattr(hints, "billing_reference_preference", None)
    billing_reference_column_label = getattr(hints, "billing_reference_column_label", None)
    explicit_distinct_billing_reference = (
        billing_reference_preference in {"none", "separate_column", "parent_identifier"}
        or (
            bool(billing_reference_column_label)
            and billing_reference_preference != "same_as_service_id"
        )
    )
    from stencil.output.mapper import normalize_source_path

    compatible_columns = [
        OutputColumn(
            header=column.header,
            source=column.source,
            fallback=(
                None
                if (
                    explicit_distinct_billing_reference
                    and normalize_source_path(column.source) == "line_item.billing_reference"
                    and column.fallback
                    and normalize_source_path(column.fallback) == "line_item.service_id"
                )
                else column.fallback
            ),
            width=column.width,
            number_format=column.number_format,
            transforms=list(getattr(column, "transforms", None) or []),
        )
        for column in spec.columns
    ]

    overrides = getattr(profile, "output_mapping_overrides", None) or []
    if not overrides:
        if not explicit_distinct_billing_reference:
            return spec
        return OutputSpec(spec_id=spec.spec_id, name=spec.name, columns=compatible_columns)

    # Invalid/ambiguous overrides are rejected by profile readiness validation.
    # Resolution remains defensive for legacy data that predates that validation:
    # only a single override targeting a unique output header is applied.
    header_counts = Counter(column.header for column in compatible_columns)
    override_counts = Counter(override.output_header for override in overrides)
    override_by_header = {
        override.output_header: override
        for override in overrides
        if override_counts[override.output_header] == 1
        and header_counts[override.output_header] == 1
    }
    resolved_columns = [
        OutputColumn(
            header=column.header,
            source=(override_by_header[column.header].source if column.header in override_by_header else column.source),
            fallback=(
                override_by_header[column.header].fallback
                if column.header in override_by_header
                else column.fallback
            ),
            width=column.width,
            number_format=column.number_format,
            transforms=(
                list(override_by_header[column.header].transforms)
                if column.header in override_by_header
                else list(getattr(column, "transforms", None) or [])
            ),
        )
        for column in compatible_columns
    ]
    return OutputSpec(spec_id=spec.spec_id, name=spec.name, columns=resolved_columns)


def validate_profile_output_mapping(profile, schema) -> list[str]:
    """Validate profile overrides and the resulting effective output spec."""
    spec = get_output_spec(getattr(profile, "output_spec_id", None))
    overrides = getattr(profile, "output_mapping_overrides", None) or []
    if not overrides:
        return validate_spec_against_schema(resolve_output_spec(profile), schema)

    issues: list[str] = []
    header_counts = Counter(column.header for column in spec.columns)
    override_counts = Counter(override.output_header for override in overrides)
    for header, count in override_counts.items():
        if count > 1:
            issues.append(f"output mapping has duplicate overrides for column '{header}'")
        if header_counts[header] == 0:
            issues.append(f"output mapping references unknown column '{header}'")
        elif header_counts[header] > 1:
            issues.append(f"output mapping column '{header}' is ambiguous because the output header is duplicated")
    for override in overrides:
        if not override.source.strip():
            issues.append(f"output mapping column '{override.output_header}' requires a source path")
        if override.transforms and override.source.startswith("computed."):
            issues.append(
                f"output mapping column '{override.output_header}' cannot transform a computed value"
            )
    issues.extend(validate_spec_against_schema(resolve_output_spec(profile), schema))
    return list(dict.fromkeys(issues))


PROTECTED_OUTPUT_SPEC_IDS = frozenset({DEFAULT_OUTPUT_SPEC_ID})


def output_spec_exists(spec_id: str) -> bool:
    if spec_id in PROTECTED_OUTPUT_SPEC_IDS:
        return True
    from stencil.db import registry
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        return registry.output_spec_exists(db, spec_id)
    finally:
        db.close()


def list_profiles_using_spec(spec_id: str) -> list[str]:
    from stencil.profiles.loader import load_all_profiles

    load_all_profiles()
    from stencil.profiles.loader import _profiles_cache

    return sorted(
        profile.profile_id
        for profile in _profiles_cache.values()
        if profile.output_spec_id == spec_id
    )


def validate_output_spec(spec: OutputSpec) -> list[str]:
    issues: list[str] = []
    if not spec.spec_id.strip():
        issues.append("spec_id is required")
    if not spec.columns:
        issues.append("at least one column is required")
    headers = [column.header.strip() for column in spec.columns]
    if any(not header for header in headers):
        issues.append("every column requires a non-empty header")
    duplicates = sorted(header for header, count in Counter(headers).items() if header and count > 1)
    if duplicates:
        issues.append(f"column headers must be unique: {', '.join(duplicates)}")
    for column in spec.columns:
        if not column.source.strip():
            issues.append(f"column '{column.header}' requires a source path")
    return issues


def validate_spec_against_schema(spec: OutputSpec, schema) -> list[str]:
    doc_names = {f.name for f in schema.document_fields()}
    row_names = {f.name for f in schema.row_fields()}
    issues: list[str] = []
    for column in spec.columns:
        for path in (column.source, column.fallback):
            if not path:
                continue
            if path.startswith("field."):
                name = path.removeprefix("field.")
                if name not in doc_names:
                    issues.append(f"column '{column.header}' references unknown document field '{name}'")
            elif path.startswith("row."):
                name = path.removeprefix("row.")
                if name not in row_names:
                    issues.append(f"column '{column.header}' references unknown row field '{name}'")
            elif path.startswith("computed."):
                continue
            elif path.startswith("header."):
                name = path.removeprefix("header.")
                if name not in doc_names:
                    issues.append(f"column '{column.header}' references unknown document field '{name}'")
            elif path.startswith("line_item."):
                name = path.removeprefix("line_item.")
                if name not in row_names:
                    issues.append(f"column '{column.header}' references unknown row field '{name}'")
    return issues


def save_output_spec(spec: OutputSpec) -> OutputSpec:
    issues = validate_output_spec(spec)
    if issues:
        raise ValueError("; ".join(issues))
    if not _VALID_ID.match(spec.spec_id):
        raise ValueError("Invalid spec ID")
    from stencil.db import registry
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        registry.upsert_output_spec(
            db, spec_id=spec.spec_id, name=spec.name, data=spec.model_dump(mode="json"),
        )
    finally:
        db.close()
    _specs_cache[spec.spec_id] = spec
    logger.info("output_specs.saved", spec_id=spec.spec_id)
    return spec


def delete_output_spec(spec_id: str) -> None:
    if spec_id in PROTECTED_OUTPUT_SPEC_IDS:
        raise ValueError(f"Cannot delete protected spec '{spec_id}'")
    users = list_profiles_using_spec(spec_id)
    if users:
        raise ValueError(f"Spec in use by profiles: {', '.join(users)}")
    from stencil.db import registry
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        registry.delete_output_spec(db, spec_id)
    finally:
        db.close()
    _specs_cache.pop(spec_id, None)
    logger.info("output_specs.deleted", spec_id=spec_id)


def clone_output_spec(source_id: str, new_spec_id: str, *, name: str | None = None) -> OutputSpec:
    source = get_output_spec(source_id)
    cloned = OutputSpec(
        spec_id=new_spec_id,
        name=name or f"{source.name} (copy)",
        columns=[OutputColumn.model_validate(c.model_dump()) for c in source.columns],
    )
    save_output_spec(cloned)
    return cloned

