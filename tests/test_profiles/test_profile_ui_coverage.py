from stencil.profiles.ui_coverage import (
    PROFILE_UI_COVERAGE,
    unclassified_profile_ui_paths,
)


def test_every_persisted_profile_setting_has_a_ui_owner():
    assert unclassified_profile_ui_paths() == set()


def test_ui_coverage_manifest_uses_known_categories():
    assert set(PROFILE_UI_COVERAGE.values()) == {
        "profile_editor",
        "other_ui_workflow",
        "generated_read_only",
    }
