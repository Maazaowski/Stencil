"""Provider-agnostic LLM layer (OpenAI + Anthropic)."""

from stencil.llm.client import active_provider, get_ai_client
from stencil.llm.config import PROVIDER_MODELS, PROVIDERS, default_model, models_for, normalize_provider

__all__ = [
    "PROVIDERS",
    "PROVIDER_MODELS",
    "active_provider",
    "default_model",
    "get_ai_client",
    "models_for",
    "normalize_provider",
]
