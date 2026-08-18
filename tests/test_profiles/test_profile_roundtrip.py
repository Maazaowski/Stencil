"""Contract tests: profiles must never silently lose backend-supported fields.

Regression guard for the schema-drift bug where the frontend profile editor rebuilt
``line_item_hints`` from a hand-maintained allowlist and dropped four
backend-supported knobs (``amount_source``, ``tax_source``,
``service_id_value_pattern``, ``billing_reference_value_pattern``). The stored
profile then extracted differently from its ``eval_cases`` source - same PDF, same
code, different output - because the effective config had quietly changed.

These tests are offline (no AI, no DB): they only load committed profile JSON and
round-trip the Pydantic model.
"""

import json
from pathlib import Path

import pytest

from stencil.evals.dataset import eval_corpus_dir
from stencil.profiles.migrate import migrate_profile_dict
from stencil.profiles.schema import LineItemHints, SupplierProfile

# The advanced knobs that used to be silently dropped by the editor allowlist.
_ADVANCED_HINT_FIELDS = {
    "amount_source",
    "tax_source",
    "service_id_value_pattern",
    "billing_reference_value_pattern",
}


def _eval_profile_paths() -> list[Path]:
    root = eval_corpus_dir()
    return sorted(root.glob("*/profile.json")) if root.is_dir() else []


def _raw_line_item_hints(raw: dict) -> dict:
    """The line_item_hints block as authored, whether nested under advanced or legacy."""
    advanced = raw.get("advanced") or {}
    hints = advanced.get("line_item_hints")
    if hints is None:
        hints = raw.get("line_item_hints")  # legacy top-level layout
    return hints or {}


def test_schema_defines_the_advanced_hint_fields():
    """The backend contract must include these fields (guards against removal)."""
    assert _ADVANCED_HINT_FIELDS <= set(LineItemHints.model_fields)


def test_eval_corpus_has_profiles():
    """Sanity: the parametrized guard below must actually collect cases.

    eval_cases/ is gitignored client data, so it does not exist in CI — skip
    there. When the directory DOES exist locally, an empty discovery means the
    glob broke and the per-profile guard is silently inert: fail loudly.
    """
    if not eval_corpus_dir().is_dir():
        pytest.skip("eval corpus absent (gitignored client data; not present in CI)")
    assert _eval_profile_paths(), "eval_cases exists but no */profile.json found - discovery broken"


@pytest.mark.parametrize(
    "profile_path", _eval_profile_paths(), ids=lambda p: p.parent.name
)
def test_eval_profile_retains_all_line_item_hint_keys(profile_path: Path):
    """Every known line_item_hints key in a committed profile survives validation.

    If a future schema edit drops a field the corpus depends on, this fails - the
    exact class of bug that made Rogers extract account IDs / gross amounts through
    the UI instead of phone IDs / net amounts.
    """
    raw = json.loads(profile_path.read_text(encoding="utf-8"))
    profile = SupplierProfile.model_validate(migrate_profile_dict(raw))
    kept = profile.advanced.line_item_hints.model_dump()

    known_keys = set(_raw_line_item_hints(raw)) & set(LineItemHints.model_fields)
    missing = {key for key in known_keys if key not in kept}
    assert not missing, f"{profile_path.parent.name} lost line_item_hints keys: {missing}"


def _full_profile_dict() -> dict:
    """A profile that exercises every editor-managed config block."""
    return {
        "profile_id": "roundtrip.standard",
        "version": 1,
        "status": "active",
        "owner": "test",
        "created_date": "2026-01-01",
        "last_updated_date": "2026-01-01",
        "identity": {"canonical_name": "Roundtrip", "aliases": []},
        "classification": {"output_type": "standard"},
        "output_spec_id": "temforce.standard",
        "field_schema_id": "invoice.standard",
        "output_mapping_overrides": [
            {
                "output_header": "EXT_DATE",
                "source": "row.billing_period_start",
                "fallback": "field.invoice_date",
            }
        ],
        "delivery": {
            "inbound_path": "/data/Roundtrip/pdf",
            "output_path": "/data/Roundtrip/xls",
            "accounts": [
                {
                    "label": "acct-1",
                    "inbound_path": "/data/Roundtrip/a1/pdf",
                    "output_path": "/data/Roundtrip/a1/xls",
                }
            ],
        },
        "training_config": {
            "min_validation_successes": 5,
            "require_reconciliation": False,
        },
        "layout_fingerprint": {
            "summary_anchors": ["Payment Details"],
            "currency_codes": ["USD"],
            "ignore_label_patterns": [r"(?i)^total\s+sweden$"],
            "optional_column_patterns": [r"(?i)^dia\s+usage$"],
            "exclude_span_patterns": [r"(?i)\bbank\b"],
        },
        "advanced": {
            "document_field_defaults": {"account_number": "acct-1"},
            "currency": {
                "default_code": "INR",
                "allowed_codes": ["INR"],
                "aliases": {"Rupees": "INR", "Rs": "INR"},
            },
            "line_item_hints": {
                "amount_source": "label_amount",
                "tax_source": "group_total_minus_amount",
                "service_id_value_pattern": r"PDFSPLITSTART-[^_]+_(.+?)-PDFSPLITEND",
                "billing_reference_value_pattern": r"(5G Business Internet Premium)",
            },
        },
    }


def test_round_trip_preserves_advanced_hint_values():
    """A profile carrying all four advanced knobs survives dump -> re-validate."""
    profile = SupplierProfile.model_validate(_full_profile_dict())
    reloaded = SupplierProfile.model_validate(profile.model_dump(mode="json"))
    hints = reloaded.advanced.line_item_hints
    assert hints.amount_source == "label_amount"
    assert hints.tax_source == "group_total_minus_amount"
    assert hints.service_id_value_pattern == r"PDFSPLITSTART-[^_]+_(.+?)-PDFSPLITEND"
    assert hints.billing_reference_value_pattern == r"(5G Business Internet Premium)"


def test_round_trip_preserves_training_config():
    """training_config survives dump -> re-validate (editor must not reset it)."""
    reloaded = SupplierProfile.model_validate(
        SupplierProfile.model_validate(_full_profile_dict()).model_dump(mode="json")
    )
    assert reloaded.training_config.min_validation_successes == 5
    assert reloaded.training_config.require_reconciliation is False


def test_round_trip_preserves_output_mapping_overrides():
    reloaded = SupplierProfile.model_validate(
        SupplierProfile.model_validate(_full_profile_dict()).model_dump(mode="json")
    )
    override = reloaded.output_mapping_overrides[0]
    assert override.output_header == "EXT_DATE"
    assert override.source == "row.billing_period_start"
    assert override.fallback == "field.invoice_date"


def test_round_trip_preserves_currency_rules():
    """advanced.currency survives dump -> re-validate."""
    reloaded = SupplierProfile.model_validate(
        SupplierProfile.model_validate(_full_profile_dict()).model_dump(mode="json")
    )
    assert reloaded.advanced.currency.default_code == "INR"
    assert reloaded.advanced.currency.allowed_codes == ["INR"]
    assert reloaded.advanced.currency.aliases == {"Rupees": "INR", "Rs": "INR"}


def test_round_trip_preserves_document_field_defaults():
    reloaded = SupplierProfile.model_validate(
        SupplierProfile.model_validate(_full_profile_dict()).model_dump(mode="json")
    )
    assert reloaded.advanced.document_field_defaults == {"account_number": "acct-1"}


def test_round_trip_preserves_fingerprint_and_delivery():
    """layout_fingerprint rules and delivery accounts survive the round-trip."""
    reloaded = SupplierProfile.model_validate(
        SupplierProfile.model_validate(_full_profile_dict()).model_dump(mode="json")
    )
    assert reloaded.layout_fingerprint is not None
    assert reloaded.layout_fingerprint.summary_anchors == ["Payment Details"]
    assert reloaded.layout_fingerprint.exclude_span_patterns == [r"(?i)\bbank\b"]
    assert [a.label for a in reloaded.delivery.accounts] == ["acct-1"]
    assert reloaded.delivery.inbound_path == "/data/Roundtrip/pdf"
