"""Seed the config-registry tables from committed disk JSON (import-if-not-exists).

The DB is the source of truth. On boot we import any disk profile / field schema /
output spec whose id is not already in the DB — so a fresh deployment is populated
and newly committed records are picked up on later deploys, without ever
overwriting a row that was edited at runtime through the UI.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import structlog
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from stencil.config import settings
from stencil.db import registry

logger = structlog.get_logger()


def _iter_json(directory: Path) -> Iterator[tuple[Path, dict]]:
    if not directory.exists():
        return
    for path in sorted(directory.glob("*.json")):
        try:
            yield path, json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # pragma: no cover - defensive
            logger.error("seed.read_failed", path=str(path), error=str(e))


def seed_registries_from_disk() -> dict[str, int]:
    """Import committed disk JSON into the registry tables where the id is missing.
    Idempotent and safe to call from multiple processes (duplicate inserts are
    swallowed). Returns the per-registry insert counts."""
    from stencil.db.session import SessionLocal
    from stencil.fields.schema import FieldSchema
    from stencil.output.spec import OutputSpec
    from stencil.profiles.migrate import migrate_profile_dict
    from stencil.profiles.schema import SupplierProfile

    inserted = {"profiles": 0, "field_schemas": 0, "output_specs": 0}
    db = SessionLocal()
    try:
        for path, raw in _iter_json(settings.supplier_profiles_dir):
            try:
                profile = SupplierProfile.model_validate(migrate_profile_dict(raw))
            except Exception as e:
                logger.error("seed.profile_invalid", path=str(path), error=str(e))
                continue
            if registry.profile_exists(db, profile.profile_id):
                continue
            try:
                registry.upsert_profile(
                    db, profile_id=profile.profile_id, status=profile.status,
                    canonical_name=profile.identity.canonical_name,
                    data=profile.model_dump(mode="json"),
                )
                inserted["profiles"] += 1
            except IntegrityError:  # pragma: no cover - concurrent boot
                db.rollback()

        for path, raw in _iter_json(settings.field_schemas_dir):
            try:
                schema = FieldSchema.model_validate(raw)
            except Exception as e:
                logger.error("seed.field_schema_invalid", path=str(path), error=str(e))
                continue
            if registry.field_schema_exists(db, schema.schema_id):
                continue
            try:
                registry.upsert_field_schema(
                    db, schema_id=schema.schema_id, name=schema.name,
                    data=schema.model_dump(mode="json"),
                )
                inserted["field_schemas"] += 1
            except IntegrityError:  # pragma: no cover - concurrent boot
                db.rollback()

        for path, raw in _iter_json(settings.output_specs_dir):
            try:
                spec = OutputSpec.model_validate(raw)
            except Exception as e:
                logger.error("seed.output_spec_invalid", path=str(path), error=str(e))
                continue
            if registry.output_spec_exists(db, spec.spec_id):
                continue
            try:
                registry.upsert_output_spec(
                    db, spec_id=spec.spec_id, name=spec.name,
                    data=spec.model_dump(mode="json"),
                )
                inserted["output_specs"] += 1
            except IntegrityError:  # pragma: no cover - concurrent boot
                db.rollback()
    except (OperationalError, ProgrammingError) as e:
        # Tables not migrated yet (e.g. a process booted before the backend ran
        # alembic). Defer — the authoritative seed runs in the backend lifespan.
        logger.warning("seed.tables_not_ready", error=str(e))
        db.rollback()
    finally:
        db.close()

    logger.info("seed.completed", **inserted)
    return inserted
