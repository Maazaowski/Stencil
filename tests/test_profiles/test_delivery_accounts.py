"""Multi-account delivery: one profile serves many accounts."""

from stencil.profiles.schema import SupplierProfile


def _profile(delivery: dict) -> SupplierProfile:
    return SupplierProfile.model_validate({
        "profile_id": "x.v1",
        "identity": {"canonical_name": "Acme", "aliases": []},
        "classification": {"output_type": "standard"},
        "delivery": delivery,
    })


def test_effective_accounts_falls_back_to_legacy_single_paths():
    p = _profile({"inbound_path": "/in", "output_path": "/out"})
    accts = p.effective_accounts
    assert len(accts) == 1
    assert accts[0].inbound_path == "/in" and accts[0].output_path == "/out"
    assert accts[0].label == "Acme"
    # No account label -> legacy output_path.
    assert p.output_path_for_account(None) == "/out"


def test_effective_accounts_uses_explicit_list():
    p = _profile({"accounts": [
        {"label": "82824706", "inbound_path": "/in/a", "output_path": "/out/a"},
        {"label": "59958650", "inbound_path": "/in/b", "output_path": "/out/b"},
    ]})
    accts = p.effective_accounts
    assert [a.label for a in accts] == ["82824706", "59958650"]
    assert p.output_path_for_account("59958650") == "/out/b"
    assert p.output_path_for_account("82824706") == "/out/a"
    # Unknown label -> legacy fallback (None here).
    assert p.output_path_for_account("nope") is None


def test_no_delivery_means_no_accounts():
    assert _profile({}).effective_accounts == []
