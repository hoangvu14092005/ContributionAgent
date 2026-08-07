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
    """Storage seam for publishing idempotency."""

    async def execute(self, key: IdempotencyKey, operation: Callable[[], Awaitable[T]]) -> T:
        """Run an operation once per key and return its shared outcome."""
        ...


class InMemoryIdempotencyStore:
    """Coalesce concurrent and later duplicate successful calls onto one task.

    Failed or cancelled operations are evicted so a transient GitHub/API failure
    does not poison the idempotency key for the lifetime of the process.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[IdempotencyKey, asyncio.Task[object]] = {}

    async def execute(self, key: IdempotencyKey, operation: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            task = self._tasks.get(key)
            if task is None:
                task = asyncio.create_task(operation())
                self._tasks[key] = task

        try:
            return await asyncio.shield(task)  # type: ignore[return-value]
        except BaseException:
            async with self._lock:
                if self._tasks.get(key) is task and task.done():
                    self._tasks.pop(key, None)
            raise
