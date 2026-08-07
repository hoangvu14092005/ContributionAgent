"""Async-safe in-memory idempotency for publishing operations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class IdempotencyKey:
    """Deterministic identity of one publish attempt."""

    work_id: str
    repo: str
    base_sha: str
    patch_sha256: str


class IdempotencyStore(Protocol):
    """Storage seam that Task 5 can replace with persistent state."""

    async def execute(self, key: IdempotencyKey, operation: Callable[[], Awaitable[T]]) -> T:
        """Run an operation once per key and return its shared outcome."""
        ...


class InMemoryIdempotencyStore:
    """Coalesce concurrent and later duplicate calls onto one async task."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[IdempotencyKey, asyncio.Task[object]] = {}

    async def execute(self, key: IdempotencyKey, operation: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            task = self._tasks.get(key)
            if task is None:
                task = asyncio.create_task(operation())
                self._tasks[key] = task

        return await asyncio.shield(task)  # type: ignore[return-value]
