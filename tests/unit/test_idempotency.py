"""Focused tests for publishing idempotency behavior."""

from __future__ import annotations

import asyncio

import pytest

from contribai.publishing.idempotency import IdempotencyKey, InMemoryIdempotencyStore


@pytest.mark.asyncio
async def test_failed_operation_is_evicted_and_can_retry() -> None:
    store = InMemoryIdempotencyStore()
    key = IdempotencyKey("work-1", "owner/repo", "base", "patch")
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient failure")
        return "ok"

    with pytest.raises(RuntimeError, match="transient failure"):
        await store.execute(key, operation)

    assert await store.execute(key, operation) == "ok"
    assert attempts == 2


@pytest.mark.asyncio
async def test_concurrent_waiters_still_share_one_successful_operation() -> None:
    store = InMemoryIdempotencyStore()
    key = IdempotencyKey("work-2", "owner/repo", "base", "patch")
    started = asyncio.Event()
    release = asyncio.Event()
    attempts = 0

    async def operation() -> object:
        nonlocal attempts
        attempts += 1
        started.set()
        await release.wait()
        return object()

    first = asyncio.create_task(store.execute(key, operation))
    await started.wait()
    second = asyncio.create_task(store.execute(key, operation))
    await asyncio.sleep(0)
    release.set()

    first_result, second_result = await asyncio.gather(first, second)

    assert first_result is second_result
    assert attempts == 1
