"""Centralized OpenAI cost estimation.

Rates are configured per-model in settings.openai_pricing (USD per 1M tokens)
and can be overridden via the ST_OPENAI_PRICING env var without a code change.
"""

from decimal import Decimal

import structlog

from stencil.config import settings

logger = structlog.get_logger()

_PER_MILLION = Decimal("1000000")


def estimate_cost_usd(model_name: str | None, tokens_input: int, tokens_output: int) -> Decimal:
    """Estimate the USD cost of an OpenAI call for the given model and token counts.

    Falls back to settings.openai_pricing_default (and logs a warning) when the
    model is not in the pricing table, so cost is never silently zero.
    """
    pricing = {**settings.openai_pricing, **settings.anthropic_pricing}
    rates = pricing.get(model_name or "")
    if rates is None:
        logger.warning("pricing.unknown_model", model=model_name)
        rates = settings.openai_pricing_default

    input_rate = Decimal(str(rates.get("input", 0)))
    output_rate = Decimal(str(rates.get("output", 0)))

    cost = (
        Decimal(tokens_input) * input_rate + Decimal(tokens_output) * output_rate
    ) / _PER_MILLION
    return cost.quantize(Decimal("0.000001"))
