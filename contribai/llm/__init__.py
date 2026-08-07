"""LLM provider abstraction and implementations.

Public API (Layer B):

- :func:`register_provider` — register a new ``LLMProvider`` subclass.
- :func:`make_provider` — instantiate a provider by name.
- :func:`available_providers` — list registered provider names.
- :data:`LLM_PROVIDERS` — the canonical ``name → class`` registry.
- :func:`create_llm_provider` — high-level factory with fallback/multi-model
  wrapping.
"""

from contribai.llm.provider import (
    LLMProvider,
    LLM_PROVIDERS,
    AnthropicProvider,
    CopilotProvider,
    CustomProvider,
    GeminiProvider,
    MultiModelProvider,
    OllamaProvider,
    OpenAIProvider,
    available_providers,
    create_llm_provider,
    make_provider,
    register_provider,
)

__all__ = [
    "LLMProvider",
    "LLM_PROVIDERS",
    "register_provider",
    "make_provider",
    "available_providers",
    "AnthropicProvider",
    "CopilotProvider",
    "CustomProvider",
    "GeminiProvider",
    "MultiModelProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "create_llm_provider",
]