"""Fallback LLM provider chain implementation.

Implements multi-provider fallback: when one LLM fails (rate limit, network error,
auth error, timeout), automatically try the next provider in the chain.

This is critical for ContribAI because:
- Custom self-hosted endpoints may be down or rate-limited
- We want to maximize uptime by attempting multiple providers
- Some providers are faster/cheaper but less reliable
- Different providers have different model strengths per task

Architecture:
    ┌──────────────┐
    │ LLM Call     │
    │ (TaskRouter) │
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │ Try Tier 1   │──────────┐
    │ (primary)    │          │ fail
    └──────────────┘          ▼
                       ┌──────────────┐
                       │ Try Tier 2   │──────┐
                       │ (copilot)    │      │ fail
                       └──────────────┘      ▼
                                      ┌──────────────┐
                                      │ Try Tier 3   │──→ raise
                                      │ (kilo)       │
                                      └──────────────┘
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from contribai.core.exceptions import LLMError, LLMRateLimitError
from contribai.llm.models import TaskType
from contribai.llm.provider import (
    LLMProvider,
    current_task_context,
    make_provider,
    set_task_context,
)

if __name__ != "__main__":
    from contribai.core.config import LLMConfig

logger = logging.getLogger(__name__)


# ── Provider slot definition ──────────────────────────────────────────────────


@dataclass
class ProviderSlot:
    """A single provider/model slot in the fallback chain."""

    provider: str  # "custom", "openai", "copilot", "rocket-free-2", ...
    base_url: str
    api_key: str = ""
    model: str = ""  # model name for this task
    name: str = ""  # human-readable label e.g. "rocket-free-2:glm-5-free"
    timeout: float = 60.0

    def __post_init__(self):
        if not self.name:
            self.name = f"{self.provider}:{self.model}"
        if self.timeout <= 0:
            raise ValueError("ProviderSlot timeout must be positive")


# ── Provider factory ──────────────────────────────────────────────────────────


# Slot provider names that are OpenAI-compatible but use the "custom" base class.
_OPENAI_COMPATIBLE_SLOT_NAMES = frozenset({
    "rocket-free-2",
    "rocket-free-1",
    "kilo",
    "opencode",
})

# Native provider names known to the registry.
_KNOWN_NATIVE_PROVIDERS = frozenset({
    "openai",
    "anthropic",
    "ollama",
    "gemini",
    "custom",
})


def _create_provider_for_slot(slot: ProviderSlot, base_config: LLMConfig) -> LLMProvider:
    """Build a real LLMProvider instance for a slot via the shared registry.

    Translates the slot's free-form ``provider`` string (``"rocket-free-2"``,
    ``"copilot"``, ``"openai"``…) into the appropriate registered class and
    scopes the ``LLMConfig`` so the instantiated provider sees only its own
    base URL, key, and model.

    Routing rules (Layer B reconciliation):
      - ``copilot`` → registered as ``"copilot"`` (Copilot-specific headers
        applied inside :class:`contribai.llm.provider.CopilotProvider`).
      - OpenAI-compatible aliases (``rocket-free-*``, ``kilo``, ``opencode``,
        anything unknown) → registered as ``"custom"`` and the slot base URL
        is written into ``custom_base_url``/``base_url``.
      - Known native names (``openai``, ``anthropic``, ``ollama``, ``gemini``,
        ``custom``) → registered directly.

    Copilot-specific auth resolution (``gh auth token``) is owned by
    :class:`contribai.llm.provider.CopilotProvider`; this factory just passes
    the configured key through and lets that class fall back to ``gh`` when
    it's empty.
    """
    if base_config.model_gateway_required:
        raise LLMError("fallback providers cannot bypass the required model gateway")

    # Build a fresh LLMConfig scoped to this slot
    slot_config = base_config.model_copy(deep=True)
    slot_config.base_url = slot.base_url
    slot_config.api_key = slot.api_key
    slot_config.model = slot.model
    slot_config.custom_models = {"default": slot.model, slot.provider: slot.model}

    # ── Map slot provider name → registered provider name ───────────────────
    if slot.provider == "copilot":
        slot_config.provider = "copilot"
    elif slot.provider in _OPENAI_COMPATIBLE_SLOT_NAMES:
        slot_config.provider = "custom"
        slot_config.custom_base_url = slot.base_url
    elif slot.provider in _KNOWN_NATIVE_PROVIDERS:
        slot_config.provider = slot.provider
        if slot.provider == "custom":
            slot_config.custom_base_url = slot.base_url
    else:
        # Unknown provider name — default to "custom" (OpenAI-compatible)
        # rather than failing outright. The user will see a clear error
        # at the HTTP layer if the endpoint truly isn't compatible.
        logger.debug(
            "Unknown slot provider %r — falling back to 'custom' (OpenAI-compatible)",
            slot.provider,
        )
        slot_config.provider = "custom"
        slot_config.custom_base_url = slot.base_url

    return make_provider(slot_config.provider, slot_config)


# ── Fallback chain provider ──────────────────────────────────────────────────


@dataclass
class FallbackAttempt:
    """Record of one attempt in the chain."""

    slot_name: str
    started_at: float
    duration: float = 0.0
    success: bool = False
    error: str = ""
    error_type: str = ""


class FallbackChainProvider(LLMProvider):
    """Wraps multiple LLM providers/endpoints in a fallback chain.

    Each call tries providers in order. On failure (rate limit, network error,
    timeout, auth error, malformed response), it logs and tries the next one.
    The first successful response is returned.

    The chain is constructed per task from the config (`fallback_chains` in
    `LLMConfig`). Each task can have its own chain with different models.
    """

    # Errors that should trigger a fallback (transient/unrecoverable)
    FALLBACK_TRIGGERS = (
        LLMRateLimitError,
        LLMError,
        ConnectionError,
        TimeoutError,
        asyncio.TimeoutError,
    )

    @classmethod
    def _is_fallback_trigger(cls, exc: BaseException) -> bool:
        """Check whether an exception should trigger a fallback attempt.

        Catches ContribAI LLM errors, network/timeout errors, and HTTP errors
        from httpx (4xx/5xx under the hood). Unknown programming errors are
        surfaced immediately so we don't mask real bugs.
        """
        import httpx

        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
            return False
        if isinstance(exc, cls.FALLBACK_TRIGGERS):
            return True
        # Catch httpx HTTP errors (4xx/5xx) — they're transient from our perspective
        return isinstance(exc, (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout))

    def __init__(
        self,
        config: LLMConfig,
        chains: dict[str, list[ProviderSlot]],
        default_chain: list[ProviderSlot] | None = None,
    ):
        super().__init__(config)
        self._chains = chains
        self._default_chain = default_chain or []
        self._inner_providers: dict[str, LLMProvider] = {}
        self._attempts: deque[FallbackAttempt] = deque(maxlen=20)
        self._stats = {
            "total_calls": 0,
            "successful_calls": 0,
            "fallback_uses": 0,
            "by_slot": {},
        }

    # ── Lazy provider instantiation per slot ─────────────────────────────────

    def _get_provider_for_slot(self, slot: ProviderSlot) -> LLMProvider:
        """Get or create the LLMProvider instance for a slot."""
        if slot.name not in self._inner_providers:
            self._inner_providers[slot.name] = _create_provider_for_slot(slot, self.config)
        return self._inner_providers[slot.name]

    def _get_chain_for_task(self, task: str) -> list[ProviderSlot]:
        """Get the fallback chain for a task type."""
        if task in self._chains:
            return self._chains[task]
        if "default" in self._chains:
            return self._chains["default"]
        return self._default_chain

    # ── Task context ─────────────────────────────────────────────────────────

    def set_task(self, task_type: str) -> None:
        """Deprecated compatibility shim for coroutine-local task routing."""
        super().set_task(task_type)

    @property
    def _current_task(self) -> str:
        """Legacy read-only view backed by coroutine-local context."""
        return str(current_task_context() or "default")

    @_current_task.setter
    def _current_task(self, task_type: str) -> None:
        """Keep legacy tests/callers working without provider-wide task state."""
        set_task_context(task_type)

    # ── Core fallback execution ──────────────────────────────────────────────

    async def _execute_with_fallback(
        self,
        method_name: str,
        *args: Any,
        task: TaskType | str | None = None,
        **kwargs: Any,
    ) -> str:
        """Execute a method with provider fallback chain."""
        task_name = str(task or current_task_context() or "default")
        chain = self._get_chain_for_task(task_name)
        if not chain:
            raise LLMError(
                f"No fallback chain configured for task '{task_name}'. "
                f"Configure llm.fallback_chains in config.yaml."
            )

        self._stats["total_calls"] += 1
        last_error: Exception | None = None

        for idx, slot in enumerate(chain):
            attempt = FallbackAttempt(slot_name=slot.name, started_at=time.time())
            try:
                provider = self._get_provider_for_slot(slot)
                method = getattr(provider, method_name)
                result = await asyncio.wait_for(method(*args, **kwargs), timeout=slot.timeout)
                attempt.duration = time.time() - attempt.started_at
                attempt.success = True

                if idx > 0:
                    logger.warning(
                        "🔁 Used fallback #%d for task='%s' → %s (after %d failures)",
                        idx,
                        task_name,
                        slot.name,
                        idx,
                    )
                    self._stats["fallback_uses"] += 1
                else:
                    logger.info(
                        "✅ LLM call [task=%s] → %s",
                        task_name,
                        slot.name,
                    )

                self._stats["successful_calls"] += 1
                self._stats["by_slot"][slot.name] = self._stats["by_slot"].get(slot.name, 0) + 1
                self._attempts.append(attempt)
                return result

            except self.FALLBACK_TRIGGERS as e:
                attempt.duration = time.time() - attempt.started_at
                attempt.error = str(e)
                attempt.error_type = type(e).__name__
                self._attempts.append(attempt)
                last_error = e
                logger.warning(
                    "❌ LLM fallback triggered [task=%s, slot=%s, error=%s]: %s",
                    task_name,
                    slot.name,
                    type(e).__name__,
                    e,
                )
                continue
            except Exception as e:
                # Unknown exception: check if it's a fallback-eligible error
                if self._is_fallback_trigger(e):
                    attempt.duration = time.time() - attempt.started_at
                    attempt.error = str(e)
                    attempt.error_type = type(e).__name__
                    self._attempts.append(attempt)
                    last_error = e
                    logger.warning(
                        "❌ LLM fallback triggered [task=%s, slot=%s, error=%s]: %s",
                        task_name,
                        slot.name,
                        type(e).__name__,
                        e,
                    )
                    continue
                # Real bug — don't fall back, raise immediately
                attempt.duration = time.time() - attempt.started_at
                attempt.error = str(e)
                attempt.error_type = type(e).__name__
                self._attempts.append(attempt)
                logger.error(
                    "🚨 LLM non-fallback error [task=%s, slot=%s]: %s",
                    task_name,
                    slot.name,
                    e,
                )
                raise

        # All slots failed
        raise LLMError(
            f"All {len(chain)} providers failed for task '{task_name}'. Last error: {last_error}"
        )

    # ── Public LLMProvider interface ────────────────────────────────────────

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
        return await self._execute_with_fallback(
            "complete",
            prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            task=task,
            **kwargs,
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
        **kwargs,
    ) -> str:
        return await self._execute_with_fallback(
            "chat",
            messages,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            task=task,
            **kwargs,
        )

    async def close(self):
        for provider in self._inner_providers.values():
            try:
                await provider.close()
            except Exception as e:
                logger.debug("Error closing provider: %s", e)

    # ── Stats / introspection ───────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        """Get fallback chain statistics."""
        return {
            **self._stats,
            "fallback_rate": (
                self._stats["fallback_uses"] / self._stats["total_calls"]
                if self._stats["total_calls"] > 0
                else 0.0
            ),
        }

    @property
    def recent_attempts(self) -> list[FallbackAttempt]:
        """Return last N attempts for debugging."""
        return list(self._attempts)


# ── Helper to build chains from config ────────────────────────────────────────


def build_fallback_chains(
    config: LLMConfig,
) -> tuple[dict[str, list[ProviderSlot]], list[ProviderSlot]]:
    """Parse ``config.fallback_chains`` (dict) into ProviderSlot lists.

    Schema (YAML):
        fallback_chains:
          analysis:
            - {provider: rocket-free-2, base_url: ..., api_key: ..., model: glm-5-free}
            - {provider: copilot,      base_url: ..., api_key: ..., model: claude-sonnet-4.6}
            - {provider: rocket-free-1, base_url: ..., api_key: ..., model: kilo-auto/free}
          code_gen:
            - ...
          default:
            - ...
    """
    raw = config.fallback_chains or {}
    chains: dict[str, list[ProviderSlot]] = {}
    for task, slots in raw.items():
        chain: list[ProviderSlot] = []
        for slot_def in slots:
            slot = ProviderSlot(
                provider=slot_def.get("provider", "custom"),
                base_url=slot_def.get("base_url", ""),
                api_key=slot_def.get("api_key", ""),
                model=slot_def.get("model", ""),
                name=slot_def.get("name", ""),
                timeout=slot_def.get("timeout", 60.0),
            )
            chain.append(slot)
        chains[task] = chain

    default_chain = chains.get("default", [])
    return chains, default_chain
