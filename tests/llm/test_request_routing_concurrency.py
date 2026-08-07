"""Concurrency and request-scoped routing contracts for LLM providers."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import httpx
import pytest

from contribai.core.config import LLMConfig
from contribai.core.exceptions import LLMError
from contribai.llm.fallback import FallbackChainProvider, ProviderSlot
from contribai.llm.models import LLMRequest, TaskType
from contribai.llm.provider import MultiModelProvider


def test_llm_request_deep_freezes_nested_payloads() -> None:
    messages = [{"role": "user", "content": "hello"}]
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    request = LLMRequest(
        task=TaskType.ANALYSIS,
        provider="gemini",
        model="analysis-model",
        messages=messages,
        timeout_sec=3,
        max_tokens=100,
        response_schema=schema,
    )

    messages[0]["content"] = "mutated"
    schema["properties"]["ok"]["type"] = "string"

    assert request.messages[0]["content"] == "hello"
    assert request.response_schema["properties"]["ok"]["type"] == "boolean"
    with pytest.raises(TypeError):
        request.messages[0]["content"] = "nope"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        request.model = "other"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_multimodel_request_tasks_do_not_cross_contaminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = object.__new__(MultiModelProvider)
    provider._router = type(
        "Router",
        (),
        {
            "route": lambda self, task, complexity: type(
                "Decision",
                (),
                {
                    "model": type(
                        "Model",
                        (),
                        {"name": task.value, "display_name": task.value},
                    )(),
                    "reason": "test",
                },
            )(),
        },
    )()
    provider._call_log = []

    calls: list[tuple[str, str]] = []

    async def inner_complete(prompt: str, **kwargs) -> str:
        await asyncio.sleep(0)
        calls.append((f"{kwargs['model']}:{kwargs['task']}", prompt))
        return kwargs["model"]

    async def inner_chat(messages, **kwargs) -> str:
        return await inner_complete(messages[-1]["content"], **kwargs)

    provider._inner = type(
        "Inner",
        (),
        {"complete": staticmethod(inner_complete), "chat": staticmethod(inner_chat)},
    )()

    analysis = LLMRequest(
        task=TaskType.ANALYSIS,
        provider="gemini",
        model="analysis-model",
        messages=({"role": "user", "content": "analysis"},),
        timeout_sec=1,
        max_tokens=10,
    )
    code_gen = LLMRequest(
        task=TaskType.CODE_GEN,
        provider="gemini",
        model="code-model",
        messages=({"role": "user", "content": "code"},),
        timeout_sec=1,
        max_tokens=10,
    )

    results = await asyncio.gather(
        provider.complete_request(analysis),
        provider.complete_request(code_gen),
    )

    assert results == ["analysis-model", "code-model"]
    assert set(calls) == {
        (f"analysis-model:{TaskType.ANALYSIS}", "analysis"),
        (f"code-model:{TaskType.CODE_GEN}", "code"),
    }


@pytest.mark.asyncio
async def test_fallback_applies_each_slot_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    config = LLMConfig(provider="custom", model="base", api_key="test")
    slots = [
        ProviderSlot(provider="custom", base_url="http://one", model="one", timeout=0.01),
        ProviderSlot(provider="custom", base_url="http://two", model="two", timeout=1),
    ]
    provider = FallbackChainProvider(config, {"analysis": slots})

    class Slow:
        async def chat(self, *args, **kwargs):
            await asyncio.sleep(1)

    class Fast:
        async def chat(self, *args, **kwargs):
            return "fallback"

    providers = iter([Slow(), Fast()])
    monkeypatch.setattr(provider, "_get_provider_for_slot", lambda slot: next(providers))

    request = LLMRequest(
        task=TaskType.ANALYSIS,
        provider="custom",
        model="base",
        messages=({"role": "user", "content": "x"},),
        timeout_sec=2,
        max_tokens=10,
    )
    assert await provider.complete_request(request) == "fallback"
    assert len(provider.recent_attempts) == 2
    assert provider.recent_attempts[0].error_type == "TimeoutError"


def test_auth_http_error_is_not_a_fallback_trigger() -> None:
    response = httpx.Response(401, request=httpx.Request("GET", "https://example.invalid"))
    error = httpx.HTTPStatusError("unauthorized", request=response.request, response=response)
    assert FallbackChainProvider._is_fallback_trigger(error) is False


@pytest.mark.asyncio
async def test_fallback_attempt_history_is_bounded() -> None:
    config = LLMConfig(provider="custom", model="base", api_key="test")
    slot = ProviderSlot(provider="custom", base_url="http://one", model="one")
    provider = FallbackChainProvider(config, {"analysis": [slot]})

    class Broken:
        async def chat(self, *args, **kwargs):
            raise LLMError("down")

    provider._get_provider_for_slot = lambda _: Broken()
    request = LLMRequest(
        task=TaskType.ANALYSIS,
        provider="custom",
        model="base",
        messages=({"role": "user", "content": "x"},),
        timeout_sec=1,
        max_tokens=10,
    )
    for _ in range(30):
        with pytest.raises(LLMError):
            await provider.complete_request(request)
    assert len(provider.recent_attempts) <= 20
