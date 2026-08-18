"""Unit tests for OpenAI cost estimation."""

from decimal import Decimal

from stencil.config import settings
from stencil.llm.config import PROVIDER_MODELS
from stencil.pricing import estimate_cost_usd
from stencil.runtime_settings import OPENAI_MODEL_OPTIONS


class TestModelCatalog:
    def test_selectable_models_stay_in_sync(self):
        # The catalog is maintained in two hand-synced lists — keep them identical.
        assert OPENAI_MODEL_OPTIONS == PROVIDER_MODELS["openai"]

    def test_gpt_5_6_family_is_priced(self):
        # A selectable model without a pricing entry silently falls back to the
        # default rate. Guard the models we ship here so their cost is never
        # quietly wrong. (Some older models predate their pricing entries and
        # still fall back — tracked separately.)
        family = [m for m in OPENAI_MODEL_OPTIONS if m.startswith("gpt-5.6")]
        assert family, "expected the gpt-5.6 family in the model options"
        missing = [m for m in family if m not in settings.openai_pricing]
        assert missing == [], f"gpt-5.6 models missing pricing: {missing}"


class TestGpt56Family:
    def test_sol_rate(self):
        assert estimate_cost_usd("gpt-5.6-sol", 1_000_000, 1_000_000) == Decimal("35.000000")

    def test_terra_rate(self):
        assert estimate_cost_usd("gpt-5.6-terra", 1_000_000, 1_000_000) == Decimal("17.500000")

    def test_luna_rate(self):
        assert estimate_cost_usd("gpt-5.6-luna", 1_000_000, 1_000_000) == Decimal("7.000000")


class TestEstimateCost:
    def test_gpt_5_5_rate(self):
        # gpt-5.5: $5/1M input, $30/1M output
        cost = estimate_cost_usd("gpt-5.5", 1_000_000, 1_000_000)
        assert cost == Decimal("35.000000")

    def test_gpt_5_5_realistic_tokens(self):
        # 7,523 input + 2,879 output (from a real extraction log)
        cost = estimate_cost_usd("gpt-5.5", 7523, 2879)
        expected = (Decimal(7523) * Decimal("5.0") + Decimal(2879) * Decimal("30.0")) / Decimal("1000000")
        assert cost == expected.quantize(Decimal("0.000001"))

    def test_gpt_4o_rate(self):
        cost = estimate_cost_usd("gpt-4o", 1_000_000, 1_000_000)
        assert cost == Decimal("12.500000")  # $2.50 + $10.00

    def test_zero_tokens(self):
        assert estimate_cost_usd("gpt-5.5", 0, 0) == Decimal("0.000000")

    def test_unknown_model_uses_default(self):
        # Falls back to openai_pricing_default ($5/$30), never silently zero
        cost = estimate_cost_usd("some-future-model", 1_000_000, 0)
        assert cost == Decimal("5.000000")

    def test_none_model_uses_default(self):
        cost = estimate_cost_usd(None, 0, 1_000_000)
        assert cost == Decimal("30.000000")

    def test_rounds_to_six_places(self):
        cost = estimate_cost_usd("gpt-5.5", 1, 1)
        # (1*5 + 1*30) / 1e6 = 0.000035
        assert cost == Decimal("0.000035")

    def test_claude_rate_uses_anthropic_pricing(self):
        cost = estimate_cost_usd("claude-opus-4-8", 1_000_000, 1_000_000)
        assert cost == Decimal("30.000000")
