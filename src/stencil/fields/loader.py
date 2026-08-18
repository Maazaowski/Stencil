"""Load and cache field schema JSONs (the extraction contracts).

Mirrors ``stencil.specs.loader``. The default ``invoice.standard`` schema
is always guaranteed to exist (built in code) so the system works on a fresh
install or in tests where the ``field_schemas/`` directory may be absent.
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from stencil.fields.schema import FieldDef, FieldRole, FieldSchema, FieldScope, FieldType

logger = structlog.get_logger()

DEFAULT_FIELD_SCHEMA_ID = "invoice.standard"

_VALID_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_schemas_cache: dict[str, FieldSchema] = {}

_CHARGE_TYPE_VALUES = [
    "recurring", "one_time", "tax", "fee", "credit",
    "adjustment", "surcharge", "usage", "unknown",
]

# Parity with models/authoring.py static enums
AUTHORING_HEADER_FIELD_NAMES = [
    "invoice_number", "invoice_date", "due_date", "account_number", "ban",
    "billing_period_start", "billing_period_end", "currency", "po_number",
]
AUTHORING_ITEM_FIELD_NAMES = [
    "service_id", "billing_reference", "description", "charge_type",
    "amount", "tax_amount", "currency", "billing_period_start",
    "billing_period_end", "quantity", "unit_rate",
]
AUTHORING_TOTAL_FIELD_NAMES = ["subtotal", "tax", "fees", "total_due", "tax_rate", "current_charges"]


def _builtin_invoice_standard_schema() -> FieldSchema:
    """In-code fallback equal to field_schemas/invoice.standard.json."""
    fields: list[FieldDef] = [
        FieldDef(name="output_type", scope=FieldScope.DOCUMENT, type=FieldType.STRING, role=FieldRole.NONE),
        FieldDef(name="supplier_name", scope=FieldScope.DOCUMENT, type=FieldType.STRING, required=True),
        FieldDef(name="supplier_aliases", scope=FieldScope.DOCUMENT, type=FieldType.STRING, role=FieldRole.NONE),
        FieldDef(name="invoice_number", scope=FieldScope.DOCUMENT, type=FieldType.STRING, required=True,
                 label_hint="Invoice Number", role=FieldRole.IDENTIFIER),
        FieldDef(name="invoice_date", scope=FieldScope.DOCUMENT, type=FieldType.DATE, required=True,
                 label_hint="Invoice Date"),
        FieldDef(name="due_date", scope=FieldScope.DOCUMENT, type=FieldType.DATE, label_hint="Due Date"),
        FieldDef(name="account_number", scope=FieldScope.DOCUMENT, type=FieldType.STRING,
                 label_hint="Account Number",
                 description="Customer/account number identifying the billed account."),
        FieldDef(name="ban", scope=FieldScope.DOCUMENT, type=FieldType.STRING,
                 description="Secondary billing account number, when the document shows one."),
        FieldDef(name="billing_period_start", scope=FieldScope.DOCUMENT, type=FieldType.DATE,
                 description="Start of the billing period the invoice covers."),
        FieldDef(name="billing_period_end", scope=FieldScope.DOCUMENT, type=FieldType.DATE,
                 description="End of the billing period the invoice covers."),
        FieldDef(name="currency", scope=FieldScope.DOCUMENT, type=FieldType.STRING),
        FieldDef(name="po_number", scope=FieldScope.DOCUMENT, type=FieldType.STRING),
        FieldDef(name="payment_terms", scope=FieldScope.DOCUMENT, type=FieldType.STRING),
        FieldDef(name="source_page", scope=FieldScope.DOCUMENT, type=FieldType.INTEGER),
        FieldDef(name="confidence", scope=FieldScope.DOCUMENT, type=FieldType.NUMBER),
        FieldDef(name="subtotal", scope=FieldScope.DOCUMENT, type=FieldType.CURRENCY, role=FieldRole.SUBTOTAL),
        FieldDef(name="tax", scope=FieldScope.DOCUMENT, type=FieldType.CURRENCY, role=FieldRole.TAX),
        FieldDef(name="fees", scope=FieldScope.DOCUMENT, type=FieldType.CURRENCY),
        FieldDef(name="current_charges", scope=FieldScope.DOCUMENT, type=FieldType.CURRENCY,
                 role=FieldRole.SUBTOTAL),
        FieldDef(name="total_due", scope=FieldScope.DOCUMENT, type=FieldType.CURRENCY, role=FieldRole.TOTAL,
                 label_hint="Total Due"),
        FieldDef(name="tax_rate", scope=FieldScope.DOCUMENT, type=FieldType.NUMBER, role=FieldRole.TAX_RATE),
        FieldDef(name="line_number", scope=FieldScope.ROW, type=FieldType.INTEGER, required=True),
        FieldDef(name="service_id", scope=FieldScope.ROW, type=FieldType.STRING, role=FieldRole.IDENTIFIER,
                 description="Per-line service/item identifier (e.g. circuit or service number, SKU, line ID)."),
        FieldDef(name="billing_reference", scope=FieldScope.ROW, type=FieldType.STRING, role=FieldRole.IDENTIFIER,
                 description="Per-line contract/order/billing reference, when shown separately from the service id."),
        FieldDef(name="description", scope=FieldScope.ROW, type=FieldType.STRING, required=True,
                 description="Human-readable description of the charge."),
        FieldDef(name="charge_type", scope=FieldScope.ROW, type=FieldType.ENUM, role=FieldRole.NONE,
                 enum_values=_CHARGE_TYPE_VALUES,
                 description="Kind of charge; each fee/tax/credit/adjustment is its own line item."),
        FieldDef(name="amount", scope=FieldScope.ROW, type=FieldType.CURRENCY, required=True, role=FieldRole.AMOUNT,
                 description="Net (pre-tax) amount for this line; numeric, no currency symbol."),
        FieldDef(name="tax_amount", scope=FieldScope.ROW, type=FieldType.CURRENCY, role=FieldRole.TAX,
                 description="Per-line tax amount, only when tax is itemized per line (else null)."),
        FieldDef(name="currency", scope=FieldScope.ROW, type=FieldType.STRING),
        FieldDef(name="billing_period_start", scope=FieldScope.ROW, type=FieldType.DATE),
        FieldDef(name="billing_period_end", scope=FieldScope.ROW, type=FieldType.DATE),
        FieldDef(name="quantity", scope=FieldScope.ROW, type=FieldType.NUMBER),
        FieldDef(name="unit_rate", scope=FieldScope.ROW, type=FieldType.CURRENCY),
        FieldDef(name="source_page", scope=FieldScope.ROW, type=FieldType.INTEGER),
        FieldDef(name="confidence", scope=FieldScope.ROW, type=FieldType.NUMBER),
    ]
    return FieldSchema(
        schema_id=DEFAULT_FIELD_SCHEMA_ID,
        name="Standard Invoice",
        fields=fields,
    )


def merge_field_schema(base: FieldSchema, overrides: list[FieldDef]) -> FieldSchema:
    """Apply profile overrides by scope + field name.

    Per-supplier overrides are *label-only*: for a field that already exists in
    the base schema they may set authoring hints (``label_hint``, ``description``,
    ``required``, ``enum_values``) but MUST NOT change the structural contract
    (``scope``/``type``/``role``) — those belong to the schema, not to a supplier.
    This guards against legacy/migrated overrides (and an over-eager UI) that
    carried ``type="string"/role="none"`` and silently broke coercion and
    reconciliation. An override whose name is NOT in the base appends as a full
    new field (not exposed in the supplier UI today).
    """
    by_key = {(f.scope, f.name): f for f in base.fields}
    for override in overrides:
        key = (override.scope, override.name)
        existing = by_key.get(key)
        existing_key = key
        if existing is None:
            # Preserve compatibility with legacy overrides that carried the wrong
            # scope when the base field name is otherwise unambiguous. Names such
            # as currency/billing_period_start exist at both scopes and must never
            # be resolved by name alone.
            same_name = [candidate for candidate in by_key if candidate[1] == override.name]
            if len(same_name) == 1:
                existing_key = same_name[0]
                existing = by_key[existing_key]
        if existing is None:
            by_key[key] = override
            continue
        # Label-only merge: keep the base scope/type/role; apply hint fields.
        by_key[existing_key] = existing.model_copy(
            update={
                "label_hint": override.label_hint or existing.label_hint,
                "date_format": override.date_format or existing.date_format,
                "description": override.description or existing.description,
                "required": existing.required or override.required,
                "enum_values": override.enum_values or existing.enum_values,
            }
        )
    return FieldSchema(
        schema_id=base.schema_id,
        name=base.name,
        fields=list(by_key.values()),
    )


def load_all_field_schemas(schemas_dir: Path | None = None) -> dict[str, FieldSchema]:
    """Load all field schemas from the DB into the cache. ``schemas_dir`` is
    accepted for signature compatibility and ignored (storage is the database)."""
    global _schemas_cache
    from stencil.db import registry
    from stencil.db.session import SessionLocal

    schemas: dict[str, FieldSchema] = {}
    db = SessionLocal()
    try:
        rows = registry.list_field_schemas(db)
    finally:
        db.close()
    for row in rows:
        try:
            schema = FieldSchema.model_validate(row.data)
            schemas[schema.schema_id] = schema
        except Exception as e:  # pragma: no cover - defensive
            logger.error("field_schemas.parse_failed", schema_id=row.schema_id, error=str(e))

    if DEFAULT_FIELD_SCHEMA_ID not in schemas:
        schemas[DEFAULT_FIELD_SCHEMA_ID] = _builtin_invoice_standard_schema()

    _schemas_cache = schemas
    logger.info("field_schemas.loaded_all", count=len(schemas))
    return schemas


def _read_schema_row(schema_id: str) -> FieldSchema | None:
    from stencil.db import registry
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        row = registry.get_field_schema(db, schema_id)
    finally:
        db.close()
    if row is None:
        return None
    try:
        return FieldSchema.model_validate(row.data)
    except Exception as e:  # pragma: no cover - defensive
        logger.error("field_schemas.parse_failed", schema_id=schema_id, error=str(e))
        return None


def get_field_schema(schema_id: str | None) -> FieldSchema:
    if schema_id:
        fresh = _read_schema_row(schema_id)
        if fresh is not None:
            _schemas_cache[fresh.schema_id] = fresh
            return fresh
    if not _schemas_cache:
        load_all_field_schemas()
    if schema_id and schema_id in _schemas_cache:
        return _schemas_cache[schema_id]
    return _schemas_cache.get(DEFAULT_FIELD_SCHEMA_ID) or _builtin_invoice_standard_schema()


def default_field_schema() -> FieldSchema:
    return get_field_schema(DEFAULT_FIELD_SCHEMA_ID)


def resolve_field_schema(profile) -> FieldSchema:
    return get_field_schema(getattr(profile, "field_schema_id", None))


def resolve_merged_field_schema(profile) -> FieldSchema:
    base = resolve_field_schema(profile)
    overrides = getattr(profile, "field_overrides", None) or []
    if not overrides:
        return base
    parsed = [FieldDef.model_validate(o) if isinstance(o, dict) else o for o in overrides]
    return merge_field_schema(base, parsed)


PROTECTED_FIELD_SCHEMA_IDS = frozenset({DEFAULT_FIELD_SCHEMA_ID})


def blank_document_schema(schema_id: str, name: str = "") -> FieldSchema:
    return FieldSchema(
        schema_id=schema_id,
        name=name or schema_id,
        fields=[
            FieldDef(
                name="document_id",
                scope=FieldScope.DOCUMENT,
                type=FieldType.STRING,
                role=FieldRole.IDENTIFIER,
                required=True,
            ),
        ],
    )


def blank_tabular_schema(schema_id: str, name: str = "") -> FieldSchema:
    return FieldSchema(
        schema_id=schema_id,
        name=name or schema_id,
        fields=[
            FieldDef(
                name="document_id",
                scope=FieldScope.DOCUMENT,
                type=FieldType.STRING,
                role=FieldRole.IDENTIFIER,
                required=True,
            ),
            FieldDef(
                name="line_amount",
                scope=FieldScope.ROW,
                type=FieldType.CURRENCY,
                role=FieldRole.AMOUNT,
                required=True,
            ),
        ],
    )


def validate_field_schema(schema: FieldSchema) -> list[str]:
    issues: list[str] = []
    if not schema.schema_id.strip():
        issues.append("schema_id is required")
    keys = [(f.scope, f.name) for f in schema.fields]
    if len(keys) != len(set(keys)):
        issues.append("field names must be unique within each scope")
    for field in schema.fields:
        if field.type == FieldType.ENUM and not field.enum_values:
            issues.append(f"enum field '{field.name}' requires enum_values")
    return issues


def field_schema_exists(schema_id: str) -> bool:
    if schema_id in PROTECTED_FIELD_SCHEMA_IDS:
        return True
    from stencil.db import registry
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        return registry.field_schema_exists(db, schema_id)
    finally:
        db.close()


def list_profiles_using_schema(schema_id: str) -> list[str]:
    from stencil.profiles.loader import load_all_profiles

    load_all_profiles()
    from stencil.profiles.loader import _profiles_cache

    return sorted(
        profile.profile_id
        for profile in _profiles_cache.values()
        if profile.field_schema_id == schema_id
    )


def save_field_schema(schema: FieldSchema) -> FieldSchema:
    issues = validate_field_schema(schema)
    if issues:
        raise ValueError("; ".join(issues))
    if not _VALID_ID.match(schema.schema_id):
        raise ValueError("Invalid schema ID")
    from stencil.db import registry
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        registry.upsert_field_schema(
            db, schema_id=schema.schema_id, name=schema.name,
            data=schema.model_dump(mode="json"),
        )
    finally:
        db.close()
    _schemas_cache[schema.schema_id] = schema
    logger.info("field_schemas.saved", schema_id=schema.schema_id)
    return schema


def delete_field_schema(schema_id: str) -> None:
    if schema_id in PROTECTED_FIELD_SCHEMA_IDS:
        raise ValueError(f"Cannot delete protected schema '{schema_id}'")
    users = list_profiles_using_schema(schema_id)
    if users:
        raise ValueError(f"Schema in use by profiles: {', '.join(users)}")
    from stencil.db import registry
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        registry.delete_field_schema(db, schema_id)
    finally:
        db.close()
    _schemas_cache.pop(schema_id, None)
    logger.info("field_schemas.deleted", schema_id=schema_id)


def clone_field_schema(source_id: str, new_schema_id: str, *, name: str | None = None) -> FieldSchema:
    source = get_field_schema(source_id)
    cloned = FieldSchema(
        schema_id=new_schema_id,
        name=name or f"{source.name} (copy)",
        fields=[FieldDef.model_validate(f.model_dump()) for f in source.fields],
    )
    save_field_schema(cloned)
    return cloned

