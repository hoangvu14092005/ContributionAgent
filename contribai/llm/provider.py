"""LLM Provider abstraction with multi-provider support.

Gemini is the primary/default provider. All providers implement
the same async interface for easy swapping.

Layer B (registry plumbing):

- ``LLM_PROVIDERS`` is the canonical ``name → class`` registry.
- :func:`register_provider` decorates a class to register it under a name.
- :func:`make_provider` instantiates a provider by name from a config.
- :func:`create_llm_provider` (kept for back-compat) is the high-level
  factory that wires fallback / multi-model wrapping around a base provider.

The factory used by the fallback chain — formerly ``_create_provider_for_slot``
in :mod:`contribai.llm.fallback` — now goes through :func:`make_provider`,
so any provider added via ``@register_provider`` is automatically available
to every fallback slot.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import warnings
from abc import ABC, abstractmethod
from contextvars import ContextVar
from typing import TYPE_CHECKING

from contribai.core.exceptions import LLMError, LLMRateLimitError
from contribai.core.retry import rate_limit_retry
from contribai.llm.models import LLMRequest, TaskType

if TYPE_CHECKING:
    from contribai.core.config import LLMConfig

logger = logging.getLogger(__name__)

_TASK_CONTEXT: ContextVar[TaskType | str | None] = ContextVar("contribai_llm_task", default=None)


def current_task_context() -> TaskType | str | None:
    """Return the coroutine-local compatibility task, if one is set."""
    return _TASK_CONTEXT.get()


def set_task_context(task: TaskType | str) -> None:
    """Set task context for legacy callers without mutating provider state."""
    _TASK_CONTEXT.set(task)


# ── Abstract base ──────────────────────────────────────────────────────────────


class LLMProvider(ABC):
    """Abstract LLM provider interface."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.model = config.model
        self.temperature = config.temperature
        self.max_tokens = config.max_tokens

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        task: TaskType | str | None = None,
    ) -> str:
        """Single-turn completion."""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        task: TaskType | str | None = None,
    ) -> str:
        """Multi-turn chat completion."""

    async def close(self):  # noqa: B027
        """Clean up any resources."""

    async def complete_request(self, request: LLMRequest) -> str:
        """Execute an immutable request without shared provider task state."""
        messages = [dict(message) for message in request.messages]
        return await asyncio.wait_for(
            self.chat(
                messages,
                max_tokens=request.max_tokens,
                model=request.model,
                task=request.task,
            ),
            timeout=request.timeout_sec,
        )

    def set_task(self, task_type: TaskType | str) -> None:
        """Deprecated compatibility shim using coroutine-local context."""
        warnings.warn(
            "set_task is deprecated; construct an LLMRequest with task instead",
            DeprecationWarning,
            stacklevel=2,
        )
        set_task_context(task_type)


# ── Provider registry ─────────────────────────────────────────────────────────
#
# Built-in providers register themselves via ``@register_provider`` below. Third
# party packages (or adapters added in later layers) can call
# ``register_provider("name", cls)`` to extend this map without editing core.


#: Canonical ``name → provider class`` registry. Populated by the
#: :func:`register_provider` decorator at import time.
LLM_PROVIDERS: dict[str, type[LLMProvider]] = {}


def register_provider(name: str, cls: type[LLMProvider] | None = None):
    """Register an LLM provider class under ``name``.

    Usable as a decorator::

        @register_provider("gemini")
        class GeminiProvider(LLMProvider):
            ...

    Or imperatively::

        register_provider("copilot", CopilotProvider)

    Args:
        name: Public name used by ``make_provider`` and ``LLMConfig.provider``.
        cls: Provider class. When ``None``, the decorator returns a wrapping
            function (decorator form).

    Returns:
        The class (decorator form) or ``None`` (imperative form).
    """
    if cls is None:
        # Decorator form: ``@register_provider("name")`` — return a wrapper.
        def _decorator(klass: type[LLMProvider]) -> type[LLMProvider]:
            _add_to_registry(name, klass)
            return klass

        return _decorator

    # Imperative form: ``register_provider("name", cls)``.
    _add_to_registry(name, cls)
    return cls


def _add_to_registry(name: str, cls: type[LLMProvider]) -> None:
    """Insert ``cls`` under ``name``, warning on collision."""
    existing = LLM_PROVIDERS.get(name)
    if existing is not None and existing is not cls:
        logger.warning(
            "LLM provider %r already registered as %s — overwriting with %s",
            name,
            existing.__name__,
            cls.__name__,
        )
    LLM_PROVIDERS[name] = cls
    logger.debug("Registered LLM provider: %r → %s", name, cls.__name__)


def make_provider(name: str, config: LLMConfig) -> LLMProvider:
    """Instantiate a provider by registered ``name``.

    Args:
        name: Provider name as registered via :func:`register_provider`.
        config: ``LLMConfig`` scoped to this provider.

    Raises:
        LLMError: When ``name`` is not in the registry.
    """
    provider_cls = LLM_PROVIDERS.get(name)
    if provider_cls is None:
        available = ", ".join(sorted(LLM_PROVIDERS)) or "<none>"
        raise LLMError(f"Unknown LLM provider: {name!r}. Available: {available}")
    return provider_cls(config)


def available_providers() -> list[str]:
    """Return sorted list of registered provider names."""
    return sorted(LLM_PROVIDERS)


# ── Gemini (primary) ──────────────────────────────────────────────────────────


@register_provider("gemini")
class GeminiProvider(LLMProvider):
    """Google Gemini provider - primary/default.

    Supports both API key auth and Vertex AI (Google Cloud).
    Set vertex_project in config to use Vertex AI.
    """

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            from google import genai

            if config.use_vertex:
                self._client = genai.Client(
                    vertexai=True,
                    project=config.vertex_project,
                    location=config.vertex_location,
                )
                logger.info(
                    "Gemini via Vertex AI (project=%s, location=%s)",
                    config.vertex_project,
                    config.vertex_location,
                )
            else:
                self._client = genai.Client(api_key=config.api_key)
                logger.info("Gemini via API key")
        except ImportError as e:
            raise LLMError("google-genai package not installed") from e

    @rate_limit_retry
    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        task: TaskType | str | None = None,
    ) -> str:
        from google.genai import types

        temp = temperature if temperature is not None else self.temperature
        max_tok = max_tokens if max_tokens is not None else self.max_tokens
        use_model = model or self.model

        try:
            config = types.GenerateContentConfig(
                system_instruction=system,
                temperature=temp,
                max_output_tokens=max_tok,
            )
            response = self._client.models.generate_content(
                model=use_model,
                contents=prompt,
                config=config,
            )
            return response.text or ""
        except Exception as e:
            error_msg = str(e).lower()
            if "rate" in error_msg or "quota" in error_msg or "429" in error_msg:
                raise LLMRateLimitError(f"Gemini rate limit: {e}") from e
            raise LLMError(f"Gemini error: {e}") from e

    @rate_limit_retry
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        task: TaskType | str | None = None,
    ) -> str:
        from google.genai import types

        temp = temperature if temperature is not None else self.temperature
        max_tok = max_tokens if max_tokens is not None else self.max_tokens
        use_model = model or self.model

        try:
            contents = []
            for msg in messages:
                role = "model" if msg["role"] == "assistant" else "user"
                contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))

            config = types.GenerateContentConfig(
                system_instruction=system,
                temperature=temp,
                max_output_tokens=max_tok,
            )
            response = self._client.models.generate_content(
                model=use_model,
                contents=contents,
                config=config,
            )
            return response.text or ""
        except Exception as e:
            error_msg = str(e).lower()
            if "rate" in error_msg or "quota" in error_msg or "429" in error_msg:
                raise LLMRateLimitError(f"Gemini rate limit: {e}") from e
            raise LLMError(f"Gemini chat error: {e}") from e


# ── OpenAI ─────────────────────────────────────────────────────────────────────


@register_provider("openai")
class OpenAIProvider(LLMProvider):
    """OpenAI provider (GPT-4o, etc.)."""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            from openai import AsyncOpenAI

            kwargs = {"api_key": config.api_key}
            if config.base_url:
                kwargs["base_url"] = config.base_url
            self._client = AsyncOpenAI(**kwargs)
        except ImportError as e:
            raise LLMError("openai package not installed") from e

    async def complete(self, prompt: str, *, system: str | None = None, **kwargs) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return await self.chat(messages, **kwargs)

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        task: TaskType | str | None = None,
    ) -> str:
        temp = temperature if temperature is not None else self.temperature
        max_tok = max_tokens if max_tokens is not None else self.max_tokens

        all_messages = list(messages)
        if system and not any(m["role"] == "system" for m in all_messages):
            all_messages.insert(0, {"role": "system", "content": system})

        try:
            response = await self._client.chat.completions.create(
                model=model or self.model,
                messages=all_messages,
                temperature=temp,
                max_tokens=max_tok,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            error_msg = str(e).lower()
            if "rate" in error_msg or "429" in error_msg:
                raise LLMRateLimitError(f"OpenAI rate limit: {e}") from e
            raise LLMError(f"OpenAI error: {e}") from e

    async def close(self):
        await self._client.close()


# ── Anthropic ──────────────────────────────────────────────────────────────────


@register_provider("anthropic")
class AnthropicProvider(LLMProvider):
    """Anthropic provider (Claude)."""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=config.api_key)
        except ImportError as e:
            raise LLMError("anthropic package not installed") from e

    async def complete(self, prompt: str, *, system: str | None = None, **kwargs) -> str:
        messages = [{"role": "user", "content": prompt}]
        return await self.chat(messages, system=system, **kwargs)

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        task: TaskType | str | None = None,
    ) -> str:
        temp = temperature if temperature is not None else self.temperature
        max_tok = max_tokens if max_tokens is not None else self.max_tokens

        try:
            kwargs = {
                "model": model or self.model,
                "messages": messages,
                "temperature": temp,
                "max_tokens": max_tok,
            }
            if system:
                kwargs["system"] = system

            response = await self._client.messages.create(**kwargs)
            return response.content[0].text
        except Exception as e:
            error_msg = str(e).lower()
            if "rate" in error_msg or "429" in error_msg:
                raise LLMRateLimitError(f"Anthropic rate limit: {e}") from e
            raise LLMError(f"Anthropic error: {e}") from e

    async def close(self):
        await self._client.close()


# ── Ollama (local) ─────────────────────────────────────────────────────────────


@register_provider("ollama")
class OllamaProvider(LLMProvider):
    """Ollama local model provider."""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        self._base_url = config.base_url or "http://localhost:11434"
        try:
            import httpx

            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=120.0)
        except ImportError as e:
            raise LLMError("httpx package not installed") from e

    async def complete(self, prompt: str, *, system: str | None = None, **kwargs) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return await self.chat(messages, **kwargs)

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> str:
        temp = temperature if temperature is not None else self.temperature

        all_messages = list(messages)
        if system and not any(m["role"] == "system" for m in all_messages):
            all_messages.insert(0, {"role": "system", "content": system})

        try:
            payload = {
                "model": kwargs.get("model") or self.model,
                "messages": all_messages,
                "stream": False,
                "options": {"temperature": temp},
            }
            response = await self._client.post("/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("message", {}).get("content", "")
        except Exception as e:
            raise LLMError(f"Ollama error: {e}") from e

    async def close(self):
        await self._client.aclose()


# ── Custom (self-hosted) ──────────────────────────────────────────────────────


@register_provider("custom")
class CustomProvider(LLMProvider):
    """Custom self-hosted LLM provider with per-task model routing.

    Supports OpenAI-compatible API endpoints with dynamic model selection
    based on task type (analysis, code_gen, review, validation, etc.).
    """

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            from openai import AsyncOpenAI

            self._base_url = (
                config.custom_base_url or config.base_url or "http://localhost:20128/v1"
            )
            self._client = AsyncOpenAI(
                api_key=config.api_key or "dummy-key",
                base_url=self._base_url,
            )
            self._custom_models = config.custom_models or {}
            logger.info(
                "Custom provider initialized: %s (models: %s)",
                self._base_url,
                ", ".join(f"{k}={v}" for k, v in self._custom_models.items()),
            )
        except ImportError as e:
            raise LLMError("openai package not installed") from e

    def set_task(self, task_type: str) -> None:
        """Deprecated compatibility shim for model routing."""
        super().set_task(task_type)
        logger.debug("Custom provider task context set to: %s", task_type)

    def _get_model_for_task(
        self,
        override_model: str | None = None,
        task: TaskType | str | None = None,
    ) -> str:
        """Get the appropriate model for the current task."""
        if override_model:
            return override_model

        # Map task to model from config
        task_name = str(task or current_task_context() or "default")
        model = self._custom_models.get(task_name)
        if model:
            return model

        # Check if there's a "default" key in custom_models
        default_model = self._custom_models.get("default")
        if default_model:
            return default_model

        # Final fallback to config model
        return self.model

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        task: TaskType | str | None = None,
        **kwargs,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return await self.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            task=task,
        )

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        task: TaskType | str | None = None,
    ) -> str:
        temp = temperature if temperature is not None else self.temperature
        max_tok = max_tokens if max_tokens is not None else self.max_tokens

        # Get model for current task
        use_model = self._get_model_for_task(model, task)

        all_messages = list(messages)
        if system and not any(m["role"] == "system" for m in all_messages):
            all_messages.insert(0, {"role": "system", "content": system})

        try:
            logger.info(
                "🤖 Custom LLM call [task=%s, model=%s]",
                task or current_task_context() or "default",
                use_model,
            )
            response = await self._client.chat.completions.create(
                model=use_model,
                messages=all_messages,
                temperature=temp,
                max_tokens=max_tok,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            error_msg = str(e).lower()
            if "rate" in error_msg or "429" in error_msg:
                raise LLMRateLimitError(f"Custom LLM rate limit: {e}") from e
            raise LLMError(f"Custom LLM error: {e}") from e

    async def close(self):
        await self._client.close()


# ── GitHub Copilot (special headers) ──────────────────────────────────────────


def _resolve_gh_auth_token() -> str:
    """Resolve GitHub CLI OAuth token (``gh auth token``).

    Note: ``gh auth token`` respects the ``GITHUB_TOKEN`` env var and returns it
    if set. But that token is a PAT, which is rejected by the Copilot API with
    "Personal Access Tokens are not supported for this endpoint". To get the
    actual OAuth token, we temporarily unset GITHUB_TOKEN before calling
    ``gh auth token``.
    """
    env = os.environ.copy()
    saved_token = env.pop("GITHUB_TOKEN", None)
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    finally:
        if saved_token is not None:
            os.environ["GITHUB_TOKEN"] = saved_token
    return ""


@register_provider("copilot")
class CopilotProvider(LLMProvider):
    """GitHub Copilot provider — OpenAI-compatible but with specific headers.

    Registered under the name ``"copilot"``. Originally lived inside
    :mod:`contribai.llm.fallback`; moved here so it can participate in the
    global provider registry and be instantiated via :func:`make_provider`.

    The ``__init__`` accepts an ``LLMConfig`` whose ``api_key`` may already
    hold a real OAuth token — the fallback chain resolves the token via
    :func:`_resolve_gh_auth_token` when the configured key is empty.
    """

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        import httpx

        token = config.api_key or _resolve_gh_auth_token()
        self._client = httpx.AsyncClient(
            base_url=config.base_url or "",
            timeout=60.0,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Editor-Version": "vscode/1.85.0",
                "Editor-Plugin-Version": "copilot-chat/0.12.0",
                "User-Agent": "GithubCopilot/1.155.0",
                "Copilot-Integration-Id": "vscode-chat",
            },
        )

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return await self.chat(messages, temperature=temperature, max_tokens=max_tokens)

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> str:
        temp = temperature if temperature is not None else self.temperature
        max_tok = max_tokens if max_tokens is not None else self.max_tokens

        all_messages = list(messages)
        if system and not any(m["role"] == "system" for m in all_messages):
            all_messages.insert(0, {"role": "system", "content": system})

        payload = {
            "model": self.model,
            "messages": all_messages,
            "temperature": temp,
            "max_tokens": max_tok,
            "stream": False,
        }
        response = await self._client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"] or ""

    async def close(self):
        await self._client.aclose()


# ── Multi-Model Wrapper ────────────────────────────────────────────────────────


class MultiModelProvider(LLMProvider):
    """Wraps a Gemini provider with task-aware model routing.

    Automatically selects the best model for each task type
    based on the configured routing strategy.
    """

    def __init__(self, config: LLMConfig, strategy: str = "balanced"):
        super().__init__(config)
        from contribai.llm.router import TaskRouter

        self._inner = GeminiProvider(config)
        self._router = TaskRouter(strategy=strategy)
        self._call_log: list[dict] = []

    def set_task(self, task_type) -> None:
        """Deprecated compatibility shim for coroutine-local routing."""
        super().set_task(task_type)

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        task: TaskType | str | None = None,
    ) -> str:
        task_type = TaskType(task or current_task_context() or TaskType.ANALYSIS)
        if model is None:
            decision = self._router.route(
                task_type,
                complexity=min(len(prompt) // 500, 10),
            )
            model = decision.model.name
            logger.info(
                "🧠 [%s] → %s (%s)",
                task_type.value,
                decision.model.display_name,
                decision.reason,
            )
            self._call_log.append(
                {
                    "task": task_type.value,
                    "model": model,
                    "reason": decision.reason,
                }
            )
        return await self._inner.complete(
            prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
        )

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        task: TaskType | str | None = None,
    ) -> str:
        task_type = TaskType(task or current_task_context() or TaskType.ANALYSIS)
        if model is None:
            decision = self._router.route(
                task_type,
                complexity=5,
            )
            model = decision.model.name
            logger.info(
                "🧠 [%s] → %s (%s)",
                task_type.value,
                decision.model.display_name,
                decision.reason,
            )
        return await self._inner.chat(
            messages,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            task=task_type,
        )

    async def close(self):
        await self._inner.close()

    @property
    def routing_log(self) -> list[dict]:
        """Get the log of routing decisions."""
        return self._call_log

    @property
    def routing_stats(self) -> dict:
        return self._router.stats


# ── Factory ────────────────────────────────────────────────────────────────────


def create_llm_provider(
    config: LLMConfig,
    multi_model: bool = False,
    strategy: str = "balanced",
) -> LLMProvider:
    """Create an LLM provider instance from config.

    Resolution order:
      1. If ``fallback_enabled`` is True and ``fallback_chains`` is configured,
         wrap in a :class:`FallbackChainProvider` that tries each provider in
         order on failure.
      2. If ``multi_model=True`` and provider is Gemini, wrap with
         :class:`MultiModelProvider` for per-task model routing.
      3. Otherwise, instantiate the configured provider directly via
         :func:`make_provider`.

    Args:
        config: LLM configuration
        multi_model: If True and provider is Gemini, wrap with
                     MultiModelProvider for per-task model routing
        strategy: Routing strategy (performance/balanced/economy)
    """
    if config.model_gateway_required:
        raise LLMError(
            "model gateway is required for this provider; use ModelGateway "
            "instead of raw provider access"
        )

    # ── Fallback chain takes priority over multi-model ─────────────────────
    if config.fallback_enabled and config.fallback_chains:
        from contribai.llm.fallback import (
            FallbackChainProvider,
            build_fallback_chains,
        )

        chains, default_chain = build_fallback_chains(config)
        logger.info(
            "🔗 Using fallback chain provider (tasks=%d, default chain length=%d)",
            len(chains),
            len(default_chain),
        )
        return FallbackChainProvider(config, chains, default_chain)

    if multi_model and config.provider == "gemini":
        logger.info(
            "Using multi-model routing (strategy=%s, default=%s)",
            strategy,
            config.model,
        )
        return MultiModelProvider(config, strategy=strategy)

    logger.info(
        "Using LLM provider: %s (model: %s)",
        config.provider,
        config.model,
    )
    return make_provider(config.provider, config)


__all__ = [
    "LLM_PROVIDERS",
    "AnthropicProvider",
    "CopilotProvider",
    "CustomProvider",
    "GeminiProvider",
    "LLMProvider",
    "MultiModelProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "available_providers",
    "create_llm_provider",
    "make_provider",
    "register_provider",
]
