"""Field schema registry API — full CRUD."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from stencil.fields.loader import (
    DEFAULT_FIELD_SCHEMA_ID,
    blank_document_schema,
    blank_tabular_schema,
    clone_field_schema,
    delete_field_schema,
    field_schema_exists,
    get_field_schema,
    load_all_field_schemas,
    save_field_schema,
    validate_field_schema,
)
from stencil.fields.schema import FieldDef, FieldSchema

router = APIRouter(prefix="/field-schemas", tags=["field-schemas"])

SCHEMA_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class FieldSchemaSummary(BaseModel):
    schema_id: str
    name: str
    field_count: int


class CloneFieldSchemaRequest(BaseModel):
    new_schema_id: str
    name: str | None = None


class CreateFieldSchemaRequest(BaseModel):
    schema_id: str
    name: str = ""
    template: str | None = Field(
        default=None,
        description="Optional starter: blank_document, blank_tabular.",
    )
    fields: list[FieldDef] = Field(default_factory=list)


def _validate_schema_id(schema_id: str) -> None:
    if not SCHEMA_ID_PATTERN.fullmatch(schema_id):
        raise HTTPException(status_code=400, detail="Invalid schema ID")


@router.get("", response_model=dict)
def list_field_schemas():
    schemas = load_all_field_schemas()
    return {
        "schemas": [
            FieldSchemaSummary(
                schema_id=s.schema_id,
                name=s.name,
                field_count=len(s.fields),
            ).model_dump()
            for s in schemas.values()
        ]
    }


@router.get("/{schema_id}")
def get_field_schema_detail(schema_id: str):
    _validate_schema_id(schema_id)
    if not field_schema_exists(schema_id):
        raise HTTPException(status_code=404, detail="Field schema not found")
    schema = get_field_schema(schema_id)
    return schema.model_dump(mode="json")


@router.post("", status_code=201)
def create_field_schema(body: CreateFieldSchemaRequest):
    _validate_schema_id(body.schema_id)
    if field_schema_exists(body.schema_id):
        raise HTTPException(status_code=409, detail="Field schema already exists")

    if body.fields:
        schema = FieldSchema(schema_id=body.schema_id, name=body.name, fields=body.fields)
    elif body.template == "blank_document":
        schema = blank_document_schema(body.schema_id, body.name)
    elif body.template == "blank_tabular":
        schema = blank_tabular_schema(body.schema_id, body.name)
    elif body.template:
        raise HTTPException(status_code=400, detail=f"Unknown template '{body.template}'")
    else:
        raise HTTPException(status_code=422, detail={"issues": ["fields or template is required"]})

    issues = validate_field_schema(schema)
    if issues:
        raise HTTPException(status_code=422, detail={"issues": issues})

    try:
        save_field_schema(schema)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Field schema storage is not writable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    load_all_field_schemas()
    return FieldSchemaSummary(
        schema_id=schema.schema_id,
        name=schema.name,
        field_count=len(schema.fields),
    )


@router.put("/{schema_id}")
def update_field_schema(schema_id: str, schema: FieldSchema):
    _validate_schema_id(schema_id)
    if schema.schema_id != schema_id:
        raise HTTPException(status_code=400, detail="schema_id in body must match URL")
    if not field_schema_exists(schema_id) and schema_id != DEFAULT_FIELD_SCHEMA_ID:
        raise HTTPException(status_code=404, detail="Field schema not found")

    issues = validate_field_schema(schema)
    if issues:
        raise HTTPException(status_code=422, detail={"issues": issues})

    try:
        save_field_schema(schema)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Field schema storage is not writable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    load_all_field_schemas()
    return schema.model_dump(mode="json")


@router.delete("/{schema_id}", status_code=204)
def remove_field_schema(schema_id: str):
    _validate_schema_id(schema_id)
    try:
        delete_field_schema(schema_id)
    except ValueError as exc:
        message = str(exc)
        if "in use" in message:
            raise HTTPException(status_code=422, detail={"issues": [message]}) from exc
        raise HTTPException(status_code=400, detail=message) from exc
    load_all_field_schemas()


@router.post("/{schema_id}/clone", status_code=201)
def clone_schema(schema_id: str, body: CloneFieldSchemaRequest):
    _validate_schema_id(schema_id)
    _validate_schema_id(body.new_schema_id)
    if not field_schema_exists(schema_id):
        raise HTTPException(status_code=404, detail="Source field schema not found")
    if field_schema_exists(body.new_schema_id):
        raise HTTPException(status_code=409, detail="Target field schema already exists")
    try:
        cloned = clone_field_schema(schema_id, body.new_schema_id, name=body.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Field schema storage is not writable") from exc
    load_all_field_schemas()
    return cloned.model_dump(mode="json")
