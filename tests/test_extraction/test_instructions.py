"""Unit tests for the deterministic instruction compiler."""

from stencil.extraction.instructions import (
    NOTE_OVERRIDE,
    STRUCTURED_WINS,
    compile_instructions,
)


def _profile(*, notes=None, hints=None, structure=None, currency=None) -> dict:
    return {
        "identity": {"canonical_name": "TestSupplier"},
        "classification": {"output_type": "standard"},
        "notes": notes,
        "advanced": {
            "line_item_hints": hints or {},
            "document_structure": structure or {},
            "currency": currency or {},
        },
    }


def _fields(compiled, field_name):
    return [c for c in compiled.conflicts if c.field == field_name]


class TestNotesSplitting:
    def test_no_notes_yields_nothing(self):
        compiled = compile_instructions(_profile())
        assert compiled.notes_effective == []
        assert compiled.conflicts == []
        assert compiled.overrides == []

    def test_paragraphs_become_fragments(self):
        compiled = compile_instructions(_profile(notes="First para.\n\nSecond para."))
        assert compiled.notes_effective == ["First para.", "Second para."]

    def test_notes_round_trip_into_rendered_prompt(self):
        compiled = compile_instructions(_profile(notes="Alpha.\n\nBeta."))
        rendered = compiled.render("extraction")
        assert "Alpha." in rendered and "Beta." in rendered


class TestPrecedence:
    def test_structured_wins_by_default(self):
        compiled = compile_instructions(
            _profile(notes="Compute the tax for each line.", hints={"tax_output_mode": "none"})
        )
        conflict = _fields(compiled, "line_item_hints.tax_output_mode")[0]
        assert conflict.resolution == STRUCTURED_WINS
        assert compiled.overrides == []
        # The contradicting fragment is flagged but never silently dropped.
        assert compiled.ignored_notes == ["Compute the tax for each line."]
        assert "Compute the tax for each line." in compiled.render("extraction")

    def test_override_prefix_makes_note_authoritative(self):
        compiled = compile_instructions(
            _profile(
                notes="override: Compute the tax rate for each line.",
                hints={"tax_output_mode": "none"},
            )
        )
        assert compiled.overrides == ["Compute the tax rate for each line."]
        assert compiled.notes_effective == []
        assert all(c.resolution == NOTE_OVERRIDE for c in compiled.conflicts)

    def test_override_is_case_insensitive(self):
        compiled = compile_instructions(_profile(notes="OVERRIDE: use column X."))
        assert compiled.overrides == ["use column X."]

    def test_precedence_block_always_rendered(self):
        rendered = compile_instructions(_profile()).render("extraction")
        assert "INSTRUCTION PRECEDENCE" in rendered
        assert "override:" in rendered


class TestTaxConflicts:
    def test_tax_disabled_but_note_prescribes_tax(self):
        compiled = compile_instructions(
            _profile(notes="EXT_TAX = Net Value x 0.23", hints={"tax_output_mode": "none"})
        )
        conflict = _fields(compiled, "line_item_hints.tax_output_mode")[0]
        assert conflict.resolution == STRUCTURED_WINS
        assert "row tax is disabled" in conflict.message

    def test_tax_source_none_also_flags(self):
        compiled = compile_instructions(
            _profile(notes="Use the per-line tax column.", hints={"tax_source": "none"})
        )
        assert _fields(compiled, "line_item_hints.tax_source")

    def test_unencoded_tax_rule_flags_when_mode_unset(self):
        # The euNetworks case: notes describe a rate rule, tax_output_mode is unset.
        compiled = compile_instructions(_profile(notes="EXT_TAX = Net Value x 0.23"))
        conflict = _fields(compiled, "line_item_hints.tax_output_mode")[0]
        assert conflict.structured_value == "unset (auto)"
        assert "tax_output_mode=calculate" in conflict.message

    def test_unencoded_tax_rule_flags_when_mode_auto(self):
        compiled = compile_instructions(
            _profile(notes="Compute the tax rate per row.", hints={"tax_output_mode": "auto"})
        )
        assert _fields(compiled, "line_item_hints.tax_output_mode")

    def test_explicit_calculate_mode_does_not_flag(self):
        compiled = compile_instructions(
            _profile(notes="EXT_TAX = Net Value x 0.23", hints={"tax_output_mode": "calculate"})
        )
        assert compiled.conflicts == []

    def test_note_without_tax_language_does_not_flag(self):
        compiled = compile_instructions(
            _profile(notes="Dates are printed day-first.", hints={"tax_output_mode": "none"})
        )
        assert compiled.conflicts == []


class TestGranularityConflict:
    def test_per_charge_note_contradicts_service_group(self):
        compiled = compile_instructions(
            _profile(
                notes="Emit one row per charge.",
                hints={"line_item_granularity": "per_service_group"},
            )
        )
        conflict = _fields(compiled, "line_item_hints.line_item_granularity")[0]
        assert conflict.structured_value == "per_service_group"

    def test_matching_granularity_does_not_flag(self):
        compiled = compile_instructions(
            _profile(notes="Emit one row per charge.", hints={"line_item_granularity": "per_charge_row"})
        )
        assert compiled.conflicts == []


class TestCurrencyConflict:
    def test_note_mentions_disallowed_code(self):
        compiled = compile_instructions(
            _profile(notes="Amounts may be billed in USD.", currency={"allowed_codes": ["EUR"]})
        )
        conflict = _fields(compiled, "currency.allowed_codes")[0]
        assert "USD" in conflict.message

    def test_allowed_code_does_not_flag(self):
        compiled = compile_instructions(
            _profile(notes="Amounts are billed in EUR.", currency={"allowed_codes": ["EUR"]})
        )
        assert compiled.conflicts == []

    def test_no_allowed_list_means_no_rule(self):
        compiled = compile_instructions(_profile(notes="Amounts are in USD."))
        assert compiled.conflicts == []


class TestRegionConflict:
    def test_note_marker_contradicts_structured_marker(self):
        compiled = compile_instructions(
            _profile(
                notes="Line items start after 'Charges'.",
                structure={"detail_start_marker": "Service"},
            )
        )
        conflict = _fields(compiled, "document_structure.detail_start_marker")[0]
        assert conflict.structured_value == "Service"

    def test_matching_marker_does_not_flag(self):
        compiled = compile_instructions(
            _profile(
                notes="Line items start after 'Service'.",
                structure={"detail_start_marker": "Service"},
            )
        )
        assert compiled.conflicts == []


class TestRendering:
    def test_extraction_renders_region_and_hints(self):
        compiled = compile_instructions(
            _profile(
                hints={"service_id_column_label": "Service", "line_item_granularity": "per_charge_row"},
                structure={"detail_start_marker": "Service", "detail_end_marker": "Net Total"},
            )
        )
        rendered = compiled.render("extraction")
        assert "IMPORTANT — line-item region" in rendered
        assert "service_id: extract from column/field labeled 'Service'" in rendered
        assert "line_item_granularity: per_charge_row" in rendered

    def test_authoring_renders_start_marker_not_region_block(self):
        compiled = compile_instructions(
            _profile(structure={"detail_start_marker": "Service", "detail_end_marker": "Net Total"})
        )
        rendered = compiled.render("authoring")
        assert "Line items typically start after: 'Service'" in rendered
        assert "IMPORTANT — line-item region" not in rendered

    def test_currency_block_rendered(self):
        compiled = compile_instructions(
            _profile(currency={"default_code": "EUR", "allowed_codes": ["EUR", "GBP"]})
        )
        rendered = compiled.render("extraction")
        assert "default currency code: EUR" in rendered
        assert "allowed currency codes: EUR, GBP" in rendered

    def test_conflicts_block_rendered_for_structured_wins(self):
        compiled = compile_instructions(
            _profile(notes="EXT_TAX = amount x 0.2", hints={"tax_output_mode": "none"})
        )
        rendered = compiled.render("extraction")
        assert "RESOLVED CONFLICTS" in rendered

    def test_overrides_block_rendered(self):
        compiled = compile_instructions(_profile(notes="override: trust this note."))
        rendered = compiled.render("extraction")
        assert "NOTE OVERRIDES" in rendered
        assert "trust this note." in rendered

    def test_render_is_deterministic(self):
        profile = _profile(notes="EXT_TAX = x 0.23", hints={"tax_output_mode": "none"})
        a = compile_instructions(profile).render("extraction")
        b = compile_instructions(profile).render("extraction")
        assert a == b


class TestSurfacing:
    def test_as_dict_exposes_conflicts_and_notes(self):
        compiled = compile_instructions(
            _profile(notes="EXT_TAX = amount x 0.2", hints={"tax_output_mode": "none"})
        )
        payload = compiled.as_dict()
        assert payload["conflicts"] and payload["ignored_notes"]
        assert payload["conflicts"][0]["field"] == "line_item_hints.tax_output_mode"

    def test_conflict_warnings_are_prefixed(self):
        compiled = compile_instructions(
            _profile(notes="EXT_TAX = amount x 0.2", hints={"tax_output_mode": "none"})
        )
        warnings = compiled.conflict_warnings()
        assert warnings and warnings[0].startswith("Profile setup conflict:")

    def test_no_conflicts_means_no_warnings(self):
        assert compile_instructions(_profile()).conflict_warnings() == []
