"""Provider catalog: selectable models per LLM provider.

Pricing lives in ``config.openai_pricing`` (keyed by model name, globally unique),
so cost estimation stays provider-agnostic. This module only defines which models
a user can pick per provider for the Settings dropdowns.
"""

from __future__ import annotations

PROVIDERS: tuple[str, ...] = ("openai", "anthropic")

# Models offered in Settings, per provider. The first entry is the default when
# switching to that provider.
PROVIDER_MODELS: dict[str, list[str]] = {
    "openai": [
        "gpt-5.5",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.1",
        "gpt-5",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4o",
        "gpt-4o-mini",
    ],
    "anthropic": [
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
}


def normalize_provider(provider: str | None) -> str:
    return provider if provider in PROVIDERS else "openai"


def models_for(provider: str) -> list[str]:
    return list(PROVIDER_MODELS.get(normalize_provider(provider), []))


def default_model(provider: str) -> str:
    models = models_for(provider)
    return models[0] if models else ""
