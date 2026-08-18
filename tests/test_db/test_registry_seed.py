"""Registry CRUD round-trip + disk seeder idempotency (DB-backed config)."""

from stencil.profiles.loader import delete_profile, get_profile, save_profile
from stencil.profiles.schema import SupplierProfile


def _profile(profile_id: str, *, status: str = "active") -> SupplierProfile:
    return SupplierProfile.model_validate({
        "profile_id": profile_id,
        "status": status,
        "identity": {"canonical_name": "Acme", "aliases": []},
        "classification": {"output_type": "standard"},
        "output_spec_id": "temforce.standard",
        "field_schema_id": "invoice.standard",
        "delivery": {"inbound_path": "/in", "output_path": "/out"},
    })


def test_profile_crud_round_trip(isolated_db):
    assert get_profile("acme.v1") is None
    save_profile(_profile("acme.v1"))
    loaded = get_profile("acme.v1")
    assert loaded is not None
    assert loaded.identity.canonical_name == "Acme"
    # update
    p = _profile("acme.v1", status="retired")
    save_profile(p)
    assert get_profile("acme.v1").status == "retired"
    # delete
    assert delete_profile("acme.v1") is True
    assert get_profile("acme.v1") is None
    assert delete_profile("acme.v1") is False


def test_seed_is_idempotent(isolated_db):
    from stencil.db.seed import seed_registries_from_disk

    first = seed_registries_from_disk()
    # Builtin field schema + output spec are committed and seed into the empty DB.
    # Supplier profiles are client data (not committed), so 0 profiles is expected.
    assert first["field_schemas"] >= 1
    assert first["output_specs"] >= 1
    assert first["profiles"] >= 0
    # Running again imports nothing (ids already present) — the real contract.
    second = seed_registries_from_disk()
    assert second == {"profiles": 0, "field_schemas": 0, "output_specs": 0}
