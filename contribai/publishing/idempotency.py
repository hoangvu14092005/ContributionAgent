"""Idempotency stores for publishing operations."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, Protocol, TypeVar

import aiosqlite

from contribai.storage.work_items import connection_transaction_lock

T = TypeVar("T")


@dataclass(frozen=True)
class IdempotencyKey:
    """Deterministic identity of one publish attempt."""

    work_id: str
    repo: str
    base_sha: str
    patch_sha256: str

    @property
    def storage_key(self) -> str:
        payload = json.dumps(
            {
                "base_sha": self.base_sha,
                "patch_sha256": self.patch_sha256,
                "repo": self.repo,
                "work_id": self.work_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"publish:v1:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


class PublishReconciliationRequiredError(RuntimeError):
    """Raised when a previous process may have performed a partial publish."""


class IdempotencyStore(Protocol, Generic[T]):
    """Storage seam for publishing idempotency."""

    async def execute(self, key: IdempotencyKey, operation: Callable[[], Awaitable[T]]) -> T:
        """Run an operation once per key and return its shared outcome."""
        ...


class InMemoryIdempotencyStore(Generic[T]):
    """Coalesce concurrent and later duplicate successful calls onto one task.

    This store is suitable only for tests and process-local tooling. Production
    publishing must use a durable store so a restart cannot silently replay a
    GitHub mutation.
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


class SQLiteIdempotencyStore(Generic[T]):
    """Durable fail-closed idempotency for external publishing side effects.

    A durable ``in_progress`` claim is committed before the external operation.
    If the process disappears before a completed result is persisted, a later
    process refuses to replay the operation automatically because GitHub may
    already have accepted some or all side effects. An operator/reconciler must
    inspect the remote state and resolve the claim explicitly.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS publish_idempotency (
        idempotency_key TEXT PRIMARY KEY,
        binding_json    TEXT NOT NULL,
        state           TEXT NOT NULL CHECK (state IN ('in_progress', 'completed')),
        result_json     TEXT,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """

    def __init__(
        self,
        connection: aiosqlite.Connection,
        *,
        serializer: Callable[[T], str],
        deserializer: Callable[[str], T],
    ) -> None:
        self._connection = connection
        self._serializer = serializer
        self._deserializer = deserializer
        self._transaction_lock = connection_transaction_lock(connection)
        self._tasks_lock = asyncio.Lock()
        self._tasks: dict[IdempotencyKey, asyncio.Task[object]] = {}
        self._schema_ready = False

    async def execute(self, key: IdempotencyKey, operation: Callable[[], Awaitable[T]]) -> T:
        async with self._tasks_lock:
            task = self._tasks.get(key)
            if task is None:
                task = asyncio.create_task(self._execute_owned(key, operation))
                self._tasks[key] = task
        try:
            return await asyncio.shield(task)  # type: ignore[return-value]
        finally:
            async with self._tasks_lock:
                if self._tasks.get(key) is task and task.done():
                    self._tasks.pop(key, None)

    async def _execute_owned(
        self,
        key: IdempotencyKey,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        await self._ensure_schema()
        binding_json = self._binding_json(key)
        now = datetime.now(UTC).isoformat()

        async with self._transaction_lock:
            cursor = await self._connection.execute(
                """
                SELECT binding_json, state, result_json
                FROM publish_idempotency WHERE idempotency_key = ?
                """,
                (key.storage_key,),
            )
            row = await cursor.fetchone()
            if row is not None:
                if str(row[0]) != binding_json:
                    raise PublishReconciliationRequiredError(
                        "publish idempotency key conflicts with persisted bindings"
                    )
                if str(row[1]) == "completed" and row[2] is not None:
                    return self._deserializer(str(row[2]))
                raise PublishReconciliationRequiredError(
                    "previous publish attempt is incomplete; reconcile GitHub state before retry"
                )

            await self._connection.execute(
                """
                INSERT INTO publish_idempotency
                    (idempotency_key, binding_json, state, result_json, created_at, updated_at)
                VALUES (?, ?, 'in_progress', NULL, ?, ?)
                """,
                (key.storage_key, binding_json, now, now),
            )
            await self._connection.commit()

        # Never delete the durable claim on an exception. Once external work has
        # started, the control plane cannot prove that zero side effects occurred.
        result = await operation()
        result_json = self._serializer(result)

        async with self._transaction_lock:
            cursor = await self._connection.execute(
                """
                UPDATE publish_idempotency
                SET state = 'completed', result_json = ?, updated_at = ?
                WHERE idempotency_key = ? AND binding_json = ? AND state = 'in_progress'
                """,
                (result_json, datetime.now(UTC).isoformat(), key.storage_key, binding_json),
            )
            if cursor.rowcount != 1:
                await self._connection.rollback()
                raise PublishReconciliationRequiredError(
                    "publish claim changed while persisting the completed result"
                )
            await self._connection.commit()
        return result

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._transaction_lock:
            await self._connection.execute(self._SCHEMA)
            await self._connection.commit()
            self._schema_ready = True

    @staticmethod
    def _binding_json(key: IdempotencyKey) -> str:
        return json.dumps(
            {
                "base_sha": key.base_sha,
                "patch_sha256": key.patch_sha256,
                "repo": key.repo,
                "work_id": key.work_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


__all__ = [
    "IdempotencyKey",
    "IdempotencyStore",
    "InMemoryIdempotencyStore",
    "PublishReconciliationRequiredError",
    "SQLiteIdempotencyStore",
]
