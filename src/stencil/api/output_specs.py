"""Output spec registry API — full CRUD."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from stencil.output.spec import OutputSpec
from stencil.specs.loader import (
    DEFAULT_OUTPUT_SPEC_ID,
    clone_output_spec,
    delete_output_spec,
    get_output_spec,
    load_all_output_specs,
    output_spec_exists,
    save_output_spec,
    validate_output_spec,
    validate_spec_against_schema,
)

router = APIRouter(prefix="/output-specs", tags=["output-specs"])

SPEC_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class OutputSpecSummary(BaseModel):
    spec_id: str
    name: str
    column_count: int


class CloneOutputSpecRequest(BaseModel):
    new_spec_id: str
    name: str | None = None


class ValidateOutputSpecRequest(BaseModel):
    spec: OutputSpec
    field_schema_id: str | None = None


def _validate_spec_id(spec_id: str) -> None:
    if not SPEC_ID_PATTERN.fullmatch(spec_id):
        raise HTTPException(status_code=400, detail="Invalid spec ID")


@router.get("")
def list_output_specs():
    specs = load_all_output_specs()
    return {
        "specs": [
            OutputSpecSummary(
                spec_id=s.spec_id,
                name=s.name,
                column_count=len(s.columns),
            ).model_dump()
            for s in specs.values()
        ]
    }


@router.get("/{spec_id}")
def get_output_spec_detail(spec_id: str):
    _validate_spec_id(spec_id)
    if not output_spec_exists(spec_id):
        raise HTTPException(status_code=404, detail="Output spec not found")
    spec = get_output_spec(spec_id)
    return spec.model_dump(mode="json")


@router.post("/validate")
def validate_spec(body: ValidateOutputSpecRequest):
    issues = validate_output_spec(body.spec)
    if body.field_schema_id:
        from stencil.fields.loader import get_field_schema

        schema = get_field_schema(body.field_schema_id)
        issues.extend(validate_spec_against_schema(body.spec, schema))
    return {"valid": not issues, "issues": issues}


@router.post("", status_code=201)
def create_output_spec(spec: OutputSpec):
    _validate_spec_id(spec.spec_id)
    if output_spec_exists(spec.spec_id):
        raise HTTPException(status_code=409, detail="Output spec already exists")
    issues = validate_output_spec(spec)
    if issues:
        raise HTTPException(status_code=422, detail={"issues": issues})
    try:
        save_output_spec(spec)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Output spec storage is not writable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    load_all_output_specs()
    return OutputSpecSummary(
        spec_id=spec.spec_id,
        name=spec.name,
        column_count=len(spec.columns),
    )


@router.put("/{spec_id}")
def update_output_spec(spec_id: str, spec: OutputSpec):
    _validate_spec_id(spec_id)
    if spec.spec_id != spec_id:
        raise HTTPException(status_code=400, detail="spec_id in body must match URL")
    if not output_spec_exists(spec_id) and spec_id != DEFAULT_OUTPUT_SPEC_ID:
        raise HTTPException(status_code=404, detail="Output spec not found")
    issues = validate_output_spec(spec)
    if issues:
        raise HTTPException(status_code=422, detail={"issues": issues})
    try:
        save_output_spec(spec)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Output spec storage is not writable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    load_all_output_specs()
    return spec.model_dump(mode="json")


@router.delete("/{spec_id}", status_code=204)
def remove_output_spec(spec_id: str):
    _validate_spec_id(spec_id)
    try:
        delete_output_spec(spec_id)
    except ValueError as exc:
        message = str(exc)
        if "in use" in message:
            raise HTTPException(status_code=422, detail={"issues": [message]}) from exc
        raise HTTPException(status_code=400, detail=message) from exc
    load_all_output_specs()


@router.post("/{spec_id}/clone", status_code=201)
def clone_spec(spec_id: str, body: CloneOutputSpecRequest):
    _validate_spec_id(spec_id)
    _validate_spec_id(body.new_spec_id)
    if not output_spec_exists(spec_id):
        raise HTTPException(status_code=404, detail="Source output spec not found")
    if output_spec_exists(body.new_spec_id):
        raise HTTPException(status_code=409, detail="Target output spec already exists")
    try:
        cloned = clone_output_spec(spec_id, body.new_spec_id, name=body.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Output spec storage is not writable") from exc
    load_all_output_specs()
    return cloned.model_dump(mode="json")
