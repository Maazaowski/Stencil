"""One account folder may belong to only one active/training profile."""

from stencil.api.profiles import _profile_readiness_issues
from stencil.profiles.loader import (
    account_owner_index,
    normalize_account_path,
    save_profile,
)
from stencil.profiles.schema import AuthoringEvidence, SupplierProfile


def _profile(profile_id: str, *, status: str, accounts: list[dict]) -> SupplierProfile:
    return SupplierProfile.model_validate({
        "profile_id": profile_id,
        "status": status,
        "identity": {"canonical_name": profile_id, "aliases": []},
        "classification": {"output_type": "standard"},
        "output_spec_id": "temforce.standard",
        "field_schema_id": "invoice.standard",
        "delivery": {"accounts": accounts},
    })


def _acct(inbound: str) -> dict:
    return {"label": inbound, "inbound_path": inbound, "output_path": inbound.replace("pdf", "xls")}


class TestNormalizeAccountPath:
    def test_trailing_slash_and_separators_compare_equal(self):
        a = normalize_account_path("/data/Invoices/X/A/pdf")
        b = normalize_account_path("/data/Invoices/X/A/pdf/")
        assert a == b

    def test_template_and_blank_are_ignored(self):
        assert normalize_account_path("/data/{customer}/pdf") is None
        assert normalize_account_path("") is None
        assert normalize_account_path(None) is None


class TestOwnerIndex:
    def test_indexes_claims_and_flags_multi_owner(self, isolated_db):
        save_profile(_profile("p1", status="active", accounts=[_acct("/data/X/A/pdf")]))
        save_profile(_profile("p2", status="active", accounts=[_acct("/data/X/A/pdf")]))

        index = account_owner_index(statuses={"active", "training"})
        key = normalize_account_path("/data/X/A/pdf")
        assert sorted(index[key]) == ["p1", "p2"]  # conflict visible

    def test_draft_profiles_do_not_claim(self, isolated_db):
        save_profile(_profile("d1", status="draft", accounts=[_acct("/data/X/A/pdf")]))
        index = account_owner_index(statuses={"active", "training"})
        assert normalize_account_path("/data/X/A/pdf") not in index


class TestReadinessConflict:
    def test_second_profile_on_same_folder_is_rejected(self, isolated_db):
        save_profile(_profile("owner", status="active", accounts=[_acct("/data/X/A/pdf")]))
        newcomer = _profile("newcomer", status="active", accounts=[_acct("/data/X/A/pdf")])

        issues = _profile_readiness_issues(newcomer)

        assert any("already mapped to profile 'owner'" in i for i in issues)

    def test_resaving_the_same_profile_does_not_self_conflict(self, isolated_db):
        p = _profile("solo", status="active", accounts=[_acct("/data/X/A/pdf")])
        save_profile(p)

        assert not any("already mapped" in i for i in _profile_readiness_issues(p))

    def test_draft_profile_is_not_checked(self, isolated_db):
        save_profile(_profile("owner", status="active", accounts=[_acct("/data/X/A/pdf")]))
        draft = _profile("draft", status="draft", accounts=[_acct("/data/X/A/pdf")])

        # Drafts aren't watched, so no conflict is raised (the active gate below).
        assert not any("already mapped" in i for i in _profile_readiness_issues(draft))

    def test_authoring_findings_do_not_gate_active_profile(self, isolated_db):
        profile = _profile("evidence", status="active", accounts=[_acct("/data/X/E/pdf")])
        profile.authoring_evidence = AuthoringEvidence(**{
            "evidence_level": "invoice_only",
            "status": "failed",
            "hard_blockers": ["Missing billing dates"],
            "review_warnings": ["Confirm unusual charge grouping"],
        })

        issues = _profile_readiness_issues(profile)

        assert not any("AI authoring" in issue for issue in issues)
        assert not any("Missing billing dates" in issue for issue in issues)
        assert not any("requires acknowledgement" in issue for issue in issues)


def test_authoring_findings_do_not_pause_production_delivery():
    from stencil.pipeline.processor import _profile_delivery_blockers

    profile = _profile("evidence", status="active", accounts=[_acct("/data/X/E/pdf")])
    profile.authoring_evidence = AuthoringEvidence(**{
        "evidence_level": "paired_blueprint",
        "status": "failed",
        "metrics": {
            "latest_identifier_coverage": 0.2,
            "exact_output_diff": False,
        },
        "hard_blockers": ["Profile configuration changed"],
    })

    assert _profile_delivery_blockers(profile) == []
