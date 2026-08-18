"""Tests for supplier profile selection (DB-backed loader)."""

from stencil.profiles.loader import (
    find_profile_by_supplier,
    get_active_profiles,
    save_profile,
)
from stencil.profiles.schema import SupplierProfile


def _profile(profile_id: str, *, version: int, status: str) -> SupplierProfile:
    return SupplierProfile.model_validate({
        "profile_id": profile_id,
        "version": version,
        "status": status,
        "owner": "test",
        "created_date": "2026-01-01",
        "last_updated_date": "2026-01-01",
        "identity": {"canonical_name": "euNetworks", "aliases": ["euNetworks Ireland"]},
        "classification": {"output_type": "standard"},
        "output_spec_id": "temforce.standard",
        "field_schema_id": "invoice.standard",
        "delivery": {"inbound_path": None, "output_path": None},
    })


def test_supplier_lookup_ignores_deprecated_and_selects_highest_active(isolated_db):
    save_profile(_profile("eunetworks.standard.v1", version=1, status="deprecated"))
    save_profile(_profile("eunetworks.standard.v2", version=2, status="active"))
    save_profile(_profile("eunetworks.standard.v3", version=3, status="active"))

    assert [profile.profile_id for profile in get_active_profiles()] == [
        "eunetworks.standard.v3",
        "eunetworks.standard.v2",
    ]
    assert find_profile_by_supplier("euNetworks").profile_id == "eunetworks.standard.v3"
    assert find_profile_by_supplier("euNetworks Ireland").profile_id == "eunetworks.standard.v3"
