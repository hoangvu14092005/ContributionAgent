"""Tests for the LLM provider registry (Layer B).

Covers:

- :func:`register_provider` decorator form
- :func:`register_provider` imperative form
- :func:`make_provider` instantiates by name
- :func:`make_provider` raises on unknown names
- :func:`available_providers` returns sorted names
- ``LLM_PROVIDERS`` contains every built-in provider
- Built-in providers can be instantiated via the factory

These tests intentionally do NOT cover the provider classes themselves —
the existing ``test_llm_provider.py`` does that. We focus on the registry
mechanics.
"""

from __future__ import annotations

import pytest

from contribai.core.config import LLMConfig
from contribai.core.exceptions import LLMError
from contribai.llm import (
    AnthropicProvider,
    CopilotProvider,
    CustomProvider,
    GeminiProvider,
    LLM_PROVIDERS,
    OllamaProvider,
    OpenAIProvider,
    available_providers,
    make_provider,
    register_provider,
)
from contribai.llm.provider import LLMProvider


# ── Built-in providers ───────────────────────────────────────────────────────


class TestBuiltinRegistry:
    """Sanity checks for the providers registered at import time."""

    def test_known_providers_present(self):
        for name in (
            "gemini",
            "openai",
            "anthropic",
            "ollama",
            "custom",
            "copilot",
        ):
            assert name in LLM_PROVIDERS, f"Missing built-in provider: {name}"
            assert issubclass(LLM_PROVIDERS[name], LLMProvider)

    def test_known_provider_classes(self):
        assert LLM_PROVIDERS["gemini"] is GeminiProvider
        assert LLM_PROVIDERS["openai"] is OpenAIProvider
        assert LLM_PROVIDERS["anthropic"] is AnthropicProvider
        assert LLM_PROVIDERS["ollama"] is OllamaProvider
        assert LLM_PROVIDERS["custom"] is CustomProvider
        assert LLM_PROVIDERS["copilot"] is CopilotProvider

    def test_available_providers_sorted(self):
        names = available_providers()
        assert names == sorted(names)
        assert "gemini" in names
        assert "copilot" in names


# ── register_provider decorator form ──────────────────────────────────────────


class TestRegisterProviderDecorator:
    def test_decorator_returns_class_unchanged(self):
        """The decorator must return the same class object so type hints work."""

        @register_provider("__test_decorator_one__")
        class _StubOne(LLMProvider):
            def __init__(self, config: LLMConfig):
                super().__init__(config)

            async def complete(self, prompt, *, system=None, temperature=None, max_tokens=None):
                return "ok"

            async def chat(self, messages, *, system=None, temperature=None, max_tokens=None):
                return "ok"

        assert _StubOne.__name__ == "_StubOne"
        assert LLM_PROVIDERS["__test_decorator_one__"] is _StubOne

    def test_decorator_does_not_break_subclassing(self):
        """The wrapped class must remain an LLMProvider subclass."""

        @register_provider("__test_decorator_two__")
        class _StubTwo(LLMProvider):
            def __init__(self, config: LLMConfig):
                super().__init__(config)

            async def complete(self, prompt, *, system=None, temperature=None, max_tokens=None):
                return "ok"

            async def chat(self, messages, *, system=None, temperature=None, max_tokens=None):
                return "ok"

        assert issubclass(_StubTwo, LLMProvider)


# ── register_provider imperative form ────────────────────────────────────────


class TestRegisterProviderImperative:
    def test_register_existing_class_under_new_name(self):
        class _StubThree(LLMProvider):
            def __init__(self, config: LLMConfig):
                super().__init__(config)

            async def complete(self, prompt, *, system=None, temperature=None, max_tokens=None):
                return "ok"

            async def chat(self, messages, *, system=None, temperature=None, max_tokens=None):
                return "ok"

        result = register_provider("__test_imperative__", _StubThree)
        # Imperative form returns the class (not None).
        assert result is _StubThree
        assert LLM_PROVIDERS["__test_imperative__"] is _StubThree

    def test_re_registering_same_class_is_idempotent(self):
        class _StubFour(LLMProvider):
            def __init__(self, config: LLMConfig):
                super().__init__(config)

            async def complete(self, prompt, *, system=None, temperature=None, max_tokens=None):
                return "ok"

            async def chat(self, messages, *, system=None, temperature=None, max_tokens=None):
                return "ok"

        register_provider("__test_idempotent__", _StubFour)
        register_provider("__test_idempotent__", _StubFour)
        assert LLM_PROVIDERS["__test_idempotent__"] is _StubFour


# ── make_provider ────────────────────────────────────────────────────────────


class TestMakeProvider:
    def test_raises_for_unknown_provider(self):
        # Bypass Pydantic validation by constructing an LLMConfig then
        # mutating ``provider`` — the registry should still reject it.
        config = LLMConfig(provider="gemini")
        config.provider = "no-such-provider"
        with pytest.raises(LLMError, match="Unknown LLM provider"):
            make_provider("no-such-provider", config)

    def test_make_provider_uses_config(self):
        """The instantiated provider must see the config we passed in."""
        config = LLMConfig(provider="openai", api_key="test-key")
        # Stub __init__ to bypass the network imports.
        with patch_provider_init(OpenAIProvider):
            provider = make_provider("openai", config)
        assert provider.config is config
        assert provider.config.api_key == "test-key"
        assert provider.model == config.model

    def test_make_provider_for_each_builtin(self):
        """Every built-in name must round-trip through make_provider.

        Note: ``copilot`` is not in the ``LLMConfig.provider`` Literal because
        it is only ever used via the fallback chain. We mutate the attribute
        after construction to simulate that flow.
        """
        from contribai.llm import LLM_PROVIDERS

        for name in ("gemini", "openai", "anthropic", "ollama", "custom"):
            cls = LLM_PROVIDERS[name]
            config = LLMConfig(provider=name)
            with patch_provider_init(cls):
                provider = make_provider(name, config)
            assert isinstance(provider, LLMProvider), name

        # Copilot — bypass the Literal validation.
        cls = LLM_PROVIDERS["copilot"]
        config = LLMConfig(provider="custom")
        config.provider = "copilot"  # type: ignore[assignment]
        with patch_provider_init(cls):
            provider = make_provider("copilot", config)
        assert isinstance(provider, CopilotProvider)


# ── Helpers ──────────────────────────────────────────────────────────────────


def patch_provider_init(cls):
    """Return a ``patch`` context that skips the real provider's ``__init__``.

    Most providers open a network client in ``__init__`` (httpx, openai SDK,
    anthropic SDK...). For registry tests we only care that the right class
    comes back from :func:`make_provider`, not that the SDK works.
    """
    from unittest.mock import patch

    init = getattr(cls, "__init__")

    def _passthrough(self, config):
        self.config = config
        self.model = config.model
        self.temperature = config.temperature
        self.max_tokens = config.max_tokens

    return patch.object(cls, "__init__", _passthrough)