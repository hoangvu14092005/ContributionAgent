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
import os
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from contribai.core.exceptions import LLMError, LLMRateLimitError
from contribai.llm.models import TaskType
from contribai.llm.provider import LLMProvider, current_task_context, set_task_context

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


# ── Auth resolver ─────────────────────────────────────────────────────────────


def _resolve_gh_auth_token() -> str:
    """Resolve GitHub CLI OAuth token (``gh auth token``).

    Note: ``gh auth token`` respects the ``GITHUB_TOKEN`` env var and returns it
    if set. But that token is a PAT, which is rejected by the Copilot API with
    "Personal Access Tokens are not supported for this endpoint". To get the
    actual OAuth token, we temporarily unset GITHUB_TOKEN before calling
    ``gh auth token``.
    """
    # Save and unset GITHUB_TOKEN so gh returns the real OAuth token
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
        # Restore GITHUB_TOKEN (don't pollute caller state)
        if saved_token is not None:
            os.environ["GITHUB_TOKEN"] = saved_token
    return ""


# ── Provider factory ──────────────────────────────────────────────────────────


def _create_provider_for_slot(slot: ProviderSlot, base_config: LLMConfig):
    """Build a real LLMProvider instance for a slot.

    Handles provider-specific protocol differences (Copilot uses ``/chat/completions``
    with a special token, custom endpoints use OpenAI SDK, etc.).
    """
    from contribai.llm.provider import (
        CustomProvider,
        OpenAIProvider,
    )

    # Build a fresh LLMConfig scoped to this slot
    slot_config = base_config.model_copy(deep=True)
    slot_config.provider = (
        "custom" if slot.provider.startswith(("rocket", "custom", "copilot")) else slot.provider
    )
    slot_config.custom_base_url = slot.base_url
    slot_config.base_url = slot.base_url
    slot_config.api_key = slot.api_key or "dummy-key"
    slot_config.model = slot.model
    slot_config.custom_models = {"default": slot.model, slot.provider: slot.model}

    if slot.provider == "copilot":
        # GitHub Copilot requires Bearer auth via gh token
        slot_config.api_key = slot.api_key or _resolve_gh_auth_token()
        return _CopilotProvider(slot_config, slot)
    elif slot.provider in ("rocket-free-2", "rocket-free-1", "kilo", "opencode"):
        return CustomProvider(slot_config)
    elif slot.provider == "openai":
        return OpenAIProvider(slot_config)
    else:
        # Default: treat as CustomProvider (OpenAI-compatible)
        return CustomProvider(slot_config)


# ── Copilot-specific provider (header quirks) ─────────────────────────────────


class _CopilotProvider(LLMProvider):
    """GitHub Copilot provider — OpenAI-compatible but with specific headers."""

    def __init__(self, config: LLMConfig, slot: ProviderSlot):
        super().__init__(config)
        import httpx

        self._slot = slot
        self._client = httpx.AsyncClient(
            base_url=slot.base_url,
            timeout=slot.timeout,
            headers={
                "Authorization": f"Bearer {slot.api_key or _resolve_gh_auth_token()}",
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
            "model": self._slot.model,
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
