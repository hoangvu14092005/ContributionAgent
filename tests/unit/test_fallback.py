"""Tests for contribai.llm.fallback.

Layer A — closes the test gap for `fallback.py` (was 0% coverage, 504 lines).

The fallback chain is the most behavior-rich module in ContribAI's LLM layer.
We focus on:

- `ProviderSlot` dataclass (including `__post_init__` auto-naming)
- `FallbackAttempt` dataclass (record fields)
- `build_fallback_chains(config)` — YAML dict → slot lists
- `FallbackChainProvider`:
    - chain selection priority (task → "default" → `_default_chain`)
    - `set_task` updates the active task
    - first-slot success path returns immediately
    - fallback chain continues when transient errors are raised
    - all slots fail → final `LLMError` aggregated
    - unknown exception → re-raised (no fallback on real bugs)
    - `stats` and `recent_attempts`
    - `close()` flushes inner providers
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from contribai.core.config import LLMConfig
from contribai.core.exceptions import LLMError, LLMRateLimitError
from contribai.llm.fallback import (
    FallbackAttempt,
    FallbackChainProvider,
    ProviderSlot,
    build_fallback_chains,
)

# ── ProviderSlot dataclass ──────────────────────────────────────────────────


class TestProviderSlot:
    def test_auto_name_in_post_init_when_empty(self):
        slot = ProviderSlot(
            provider="rocket-free-2",
            base_url="http://localhost:20128/v1",
            model="glm-5-free",
        )
        assert slot.name == "rocket-free-2:glm-5-free"

    def test_explicit_name_preserved(self):
        slot = ProviderSlot(
            provider="copilot",
            base_url="https://api.githubcopilot.com",
            model="claude-sonnet-4.6",
            name="my-custom-label",
        )
        assert slot.name == "my-custom-label"

    def test_defaults(self):
        slot = ProviderSlot(provider="p", base_url="http://x")
        assert slot.api_key == ""
        assert slot.model == ""
        assert slot.timeout == 60.0


# ── FallbackAttempt dataclass ───────────────────────────────────────────────


class TestFallbackAttempt:
    def test_defaults(self):
        a = FallbackAttempt(slot_name="slot-a", started_at=0.0)
        assert a.duration == 0.0
        assert a.success is False
        assert a.error == ""
        assert a.error_type == ""


# ── build_fallback_chains ───────────────────────────────────────────────────


@pytest.fixture
def raw_chains() -> dict:
    return {
        "analysis": [
            {"provider": "rocket-free-2", "base_url": "http://a", "model": "glm-5"},
            {"provider": "copilot", "base_url": "http://b", "model": "claude"},
        ],
        "code_gen": [
            {"provider": "kilo", "base_url": "http://c", "model": "kilo-auto"},
        ],
        "default": [
            {"provider": "rocket-free-1", "base_url": "http://d", "model": "free"},
        ],
    }


class TestBuildFallbackChains:
    def test_returns_tuple_of_chains_and_default(self, raw_chains):
        config = LLMConfig(fallback_chains=raw_chains)
        chains, default_chain = build_fallback_chains(config)
        assert isinstance(chains, dict)
        assert isinstance(default_chain, list)
        assert len(chains) == 3
        assert len(default_chain) == 1

    def test_each_slot_auto_named(self, raw_chains):
        config = LLMConfig(fallback_chains=raw_chains)
        chains, _ = build_fallback_chains(config)
        first = chains["analysis"][0]
        assert first.name == "rocket-free-2:glm-5"
        assert first.api_key == ""

    def test_default_chain_returned_separately(self, raw_chains):
        config = LLMConfig(fallback_chains=raw_chains)
        _, default_chain = build_fallback_chains(config)
        assert default_chain[0].provider == "rocket-free-1"

    def test_missing_default_means_empty_default_chain(self):
        config = LLMConfig(
            fallback_chains={
                "analysis": [{"provider": "x", "base_url": "http://a", "model": "m"}],
            }
        )
        chains, default_chain = build_fallback_chains(config)
        assert "analysis" in chains
        assert default_chain == []

    def test_empty_fallback_chains(self):
        config = LLMConfig(fallback_chains={})
        chains, default_chain = build_fallback_chains(config)
        assert chains == {}
        assert default_chain == []


# ── FallbackChainProvider ───────────────────────────────────────────────────


@pytest.fixture
def llm_config() -> LLMConfig:
    """A minimal LLMConfig; tests inject their own providers via mocks."""
    return LLMConfig(provider="custom", model="m", api_key="k")


@pytest.fixture
def provider(llm_config) -> FallbackChainProvider:
    """A FallbackChainProvider with no chains configured."""
    return FallbackChainProvider(
        config=llm_config,
        chains={},
        default_chain=[],
    )


class TestFallbackChainProviderInit:
    def test_starts_with_default_task(self, llm_config):
        p = FallbackChainProvider(llm_config, chains={}, default_chain=[])
        assert p._current_task == "default"

    def test_empty_stats(self, llm_config):
        p = FallbackChainProvider(llm_config, chains={}, default_chain=[])
        s = p.stats
        assert s["total_calls"] == 0
        assert s["successful_calls"] == 0
        assert s["fallback_uses"] == 0
        assert s["fallback_rate"] == 0.0


class TestTaskSelection:
    def test_set_task_changes_current(self, provider):
        provider.set_task("code_gen")
        assert provider._current_task == "code_gen"

    def test_get_chain_for_task_priority(self, provider):
        # Build a chain map: task A → 1 slot, "default" → 2 slots,
        # _default_chain → 3 slots.
        slot_a = ProviderSlot(provider="x", base_url="http://a", model="m")
        slot_d1 = ProviderSlot(provider="y", base_url="http://d1", model="m")
        slot_d2 = ProviderSlot(provider="z", base_url="http://d2", model="m")
        slot_fb1 = ProviderSlot(provider="p", base_url="http://fb1", model="m")
        slot_fb2 = ProviderSlot(provider="q", base_url="http://fb2", model="m")
        slot_fb3 = ProviderSlot(provider="r", base_url="http://fb3", model="m")
        provider._chains = {"a": [slot_a], "default": [slot_d1, slot_d2]}
        provider._default_chain = [slot_fb1, slot_fb2, slot_fb3]

        # When task == "a": 1 slot.
        provider._current_task = "a"
        assert provider._get_chain_for_task("a") == [slot_a]

        # When task has no chain but "default" exists: use default.
        provider._current_task = "missing"
        assert provider._get_chain_for_task("missing") == [slot_d1, slot_d2]

        # When task has no chain AND no "default": use _default_chain.
        provider._chains = {}
        provider._current_task = "anything"
        assert provider._get_chain_for_task("anything") == [slot_fb1, slot_fb2, slot_fb3]


class TestExecuteWithFallbackEmptyChain:
    async def test_no_chain_raises_immediately(self, provider):
        # No chains at all → LLMError before any attempt.
        with pytest.raises(LLMError, match="No fallback chain configured"):
            await provider.complete("hello")


class TestExecuteWithFallbackSuccess:
    async def test_first_slot_succeeds(self, llm_config):
        # Stub the inner provider's `complete` method to succeed.
        inner = AsyncMock()
        inner.complete.return_value = "ok"

        slot = ProviderSlot(provider="x", base_url="http://a", model="m")
        with patch("contribai.llm.fallback._create_provider_for_slot", return_value=inner):
            p = FallbackChainProvider(llm_config, chains={"default": [slot]})
            result = await p.complete("hi")
            assert result == "ok"
            assert inner.complete.await_count == 1

        # Stats reflect success without a fallback.
        s = p.stats
        assert s["total_calls"] == 1
        assert s["successful_calls"] == 1
        assert s["fallback_uses"] == 0

    async def test_fallback_path_continues_to_second_slot(self, llm_config):
        # First slot raises rate limit; second slot succeeds.
        first = AsyncMock()
        first.complete.side_effect = LLMRateLimitError("rate-limited")
        second = AsyncMock()
        second.complete.return_value = "ok-from-second"

        slot1 = ProviderSlot(provider="x", base_url="http://a", model="m", name="a")
        slot2 = ProviderSlot(provider="y", base_url="http://b", model="m", name="b")

        # Patch the factory to return our two stubs in order.
        providers = iter([first, second])
        with patch(
            "contribai.llm.fallback._create_provider_for_slot",
            side_effect=lambda *a, **kw: next(providers),
        ):
            p = FallbackChainProvider(llm_config, chains={"default": [slot1, slot2]})
            result = await p.complete("hi")

        assert result == "ok-from-second"
        assert first.complete.await_count == 1
        assert second.complete.await_count == 1

        s = p.stats
        assert s["total_calls"] == 1
        assert s["successful_calls"] == 1
        # We used a fallback (index 1).
        assert s["fallback_uses"] == 1


class TestExecuteWithFallbackAllFail:
    async def test_all_slots_fail_raises_llm_error(self, llm_config):
        # Two slots, both raise rate limit.
        first = AsyncMock()
        first.complete.side_effect = LLMRateLimitError("first failed")
        second = AsyncMock()
        second.complete.side_effect = LLMRateLimitError("second failed")

        slot1 = ProviderSlot(provider="x", base_url="http://a", model="m", name="a")
        slot2 = ProviderSlot(provider="y", base_url="http://b", model="m", name="b")
        providers = iter([first, second])

        with patch(
            "contribai.llm.fallback._create_provider_for_slot",
            side_effect=lambda *a, **kw: next(providers),
        ):
            p = FallbackChainProvider(llm_config, chains={"default": [slot1, slot2]})
            with pytest.raises(LLMError, match="All 2 providers failed"):
                await p.complete("hi")

        s = p.stats
        assert s["total_calls"] == 1
        assert s["successful_calls"] == 0
        assert s["fallback_uses"] == 0  # No successful call at all

    async def test_recent_attempts_records_both_failures(self, llm_config):
        first = AsyncMock()
        first.complete.side_effect = LLMRateLimitError("first")
        second = AsyncMock()
        second.complete.side_effect = LLMRateLimitError("second")
        slot1 = ProviderSlot(provider="x", base_url="http://a", model="m", name="a")
        slot2 = ProviderSlot(provider="y", base_url="http://b", model="m", name="b")
        providers = iter([first, second])

        with patch(
            "contribai.llm.fallback._create_provider_for_slot",
            side_effect=lambda *a, **kw: next(providers),
        ):
            p = FallbackChainProvider(llm_config, chains={"default": [slot1, slot2]})
            with pytest.raises(LLMError):
                await p.complete("hi")

        attempts = p.recent_attempts
        assert len(attempts) == 2
        assert attempts[0].slot_name == "a"
        assert attempts[0].success is False
        assert attempts[1].slot_name == "b"
        assert attempts[1].success is False
        assert "first" in attempts[0].error
        assert "second" in attempts[1].error


class TestExecuteWithFallbackUnknownException:
    async def test_unknown_exception_raises_without_fallback(self, llm_config):
        # ValueError is NOT in FALLBACK_TRIGGERS → should NOT fall back.
        first = AsyncMock()
        first.complete.side_effect = ValueError("real bug here")
        second = AsyncMock()
        second.complete.return_value = "should-not-see-this"

        slot1 = ProviderSlot(provider="x", base_url="http://a", model="m", name="a")
        slot2 = ProviderSlot(provider="y", base_url="http://b", model="m", name="b")
        providers = iter([first, second])

        with patch(
            "contribai.llm.fallback._create_provider_for_slot",
            side_effect=lambda *a, **kw: next(providers),
        ):
            p = FallbackChainProvider(llm_config, chains={"default": [slot1, slot2]})
            with pytest.raises(ValueError, match="real bug here"):
                await p.complete("hi")

        # The second provider should NEVER be tried.
        assert second.complete.await_count == 0


class TestFallbackTriggers:
    @pytest.mark.parametrize(
        "exc",
        [
            LLMRateLimitError("rate"),
            LLMError("generic LLM error"),
            ConnectionError("conn"),
            TimeoutError("timeout"),
        ],
    )
    async def test_all_listed_triggers_cause_fallback(self, llm_config, exc):
        first = AsyncMock()
        first.complete.side_effect = exc
        second = AsyncMock()
        second.complete.return_value = "ok"

        slot1 = ProviderSlot(provider="x", base_url="http://a", model="m", name="a")
        slot2 = ProviderSlot(provider="y", base_url="http://b", model="m", name="b")
        providers = iter([first, second])

        with patch(
            "contribai.llm.fallback._create_provider_for_slot",
            side_effect=lambda *a, **kw: next(providers),
        ):
            p = FallbackChainProvider(llm_config, chains={"default": [slot1, slot2]})
            result = await p.complete("hi")

        assert result == "ok"
        assert first.complete.await_count == 1
        assert second.complete.await_count == 1


class TestChatFallback:
    async def test_chat_routes_through_fallback(self, llm_config):
        inner = AsyncMock()
        inner.chat.return_value = "chat-ok"
        slot = ProviderSlot(provider="x", base_url="http://a", model="m")

        with patch("contribai.llm.fallback._create_provider_for_slot", return_value=inner):
            p = FallbackChainProvider(llm_config, chains={"default": [slot]})
            result = await p.chat([{"role": "user", "content": "hi"}])
        assert result == "chat-ok"
        assert inner.chat.await_count == 1


class TestClose:
    async def test_close_closes_each_inner_provider(self, llm_config):
        # First slot succeeds — second never instantiated.
        # We force the second to instantiate by having the first raise a
        # transient error so the chain rolls over to slot2.
        p1 = AsyncMock()
        p1.complete.side_effect = LLMRateLimitError("rate-limited")
        p2 = AsyncMock()
        p2.complete.return_value = "ok"

        slot1 = ProviderSlot(provider="x", base_url="http://a", model="m", name="a")
        slot2 = ProviderSlot(provider="y", base_url="http://b", model="m", name="b")

        with patch(
            "contribai.llm.fallback._create_provider_for_slot",
            side_effect=[p1, p2],
        ):
            p = FallbackChainProvider(llm_config, chains={"default": [slot1, slot2]})
            await p.complete("hi")  # Forces both p1 and p2 to be instantiated.
            await p.close()

        p1.close.assert_awaited()
        p2.close.assert_awaited()
