"""Durable publishing idempotency contracts."""

from __future__ import annotations

import asyncio

import aiosqlite
import pytest

from contribai.publishing.idempotency import (
    IdempotencyKey,
    PublishReconciliationRequiredError,
    SQLiteIdempotencyStore,
)


@pytest.mark.asyncio
async def test_sqlite_store_replays_completed_result_after_restart() -> None:
    connection = await aiosqlite.connect(":memory:")
    key = IdempotencyKey("work-1", "owner/repo", "base", "patch")
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "published"

    try:
        first = SQLiteIdempotencyStore[str](
            connection,
            serializer=lambda value: value,
            deserializer=lambda value: value,
        )
        assert await first.execute(key, operation) == "published"

        restarted = SQLiteIdempotencyStore[str](
            connection,
            serializer=lambda value: value,
            deserializer=lambda value: value,
        )
        assert await restarted.execute(key, operation) == "published"
        assert calls == 1
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_sqlite_store_coalesces_concurrent_callers() -> None:
    connection = await aiosqlite.connect(":memory:")
    key = IdempotencyKey("work-2", "owner/repo", "base", "patch")
    calls = 0
    gate = asyncio.Event()

    async def operation() -> str:
        nonlocal calls
        calls += 1
        await gate.wait()
        return "done"

    try:
        store = SQLiteIdempotencyStore[str](
            connection,
            serializer=lambda value: value,
            deserializer=lambda value: value,
        )
        first = asyncio.create_task(store.execute(key, operation))
        second = asyncio.create_task(store.execute(key, operation))
        await asyncio.sleep(0)
        gate.set()
        assert await asyncio.gather(first, second) == ["done", "done"]
        assert calls == 1
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_sqlite_store_requires_reconciliation_after_uncertain_failure() -> None:
    connection = await aiosqlite.connect(":memory:")
    key = IdempotencyKey("work-3", "owner/repo", "base", "patch")

    async def uncertain_operation() -> str:
        raise RuntimeError("process died after an external side effect may have started")

    try:
        first = SQLiteIdempotencyStore[str](
            connection,
            serializer=lambda value: value,
            deserializer=lambda value: value,
        )
        with pytest.raises(RuntimeError, match="process died"):
            await first.execute(key, uncertain_operation)

        restarted = SQLiteIdempotencyStore[str](
            connection,
            serializer=lambda value: value,
            deserializer=lambda value: value,
        )
        with pytest.raises(PublishReconciliationRequiredError, match="reconcile"):
            await restarted.execute(key, lambda: asyncio.sleep(0, result="duplicate"))
    finally:
        await connection.close()
