"""Field schema registry — user-defined extraction contracts."""

from stencil.fields.loader import (
    DEFAULT_FIELD_SCHEMA_ID,
    default_field_schema,
    get_field_schema,
    load_all_field_schemas,
    merge_field_schema,
    resolve_field_schema,
    resolve_merged_field_schema,
)
from stencil.fields.schema import (
    FieldDef,
    FieldRole,
    FieldSchema,
    FieldScope,
    FieldType,
)

__all__ = [
    "DEFAULT_FIELD_SCHEMA_ID",
    "FieldDef",
    "FieldRole",
    "FieldSchema",
    "FieldScope",
    "FieldType",
    "default_field_schema",
    "get_field_schema",
    "load_all_field_schemas",
    "merge_field_schema",
    "resolve_field_schema",
    "resolve_merged_field_schema",
]
