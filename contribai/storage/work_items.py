"""Transactional SQLite persistence for WorkItem lifecycle state."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final, cast
from weakref import WeakKeyDictionary

import aiosqlite

from contribai.control.mode import ExecutionMode
from contribai.domain.state import InvalidWorkTransitionError, WorkState, validate_transition
from contribai.domain.work_item import BudgetSnapshot, JsonValue, WorkItem


class WorkItemNotFoundError(LookupError):
    """Raised when an operation targets an unknown WorkItem."""


class StaleWorkItemError(RuntimeError):
    """Raised when an optimistic WorkItem version no longer matches."""


class IllegalSideEffectStateError(RuntimeError):
    """Raised when a side effect is recorded outside the publish lifecycle."""


class SideEffectConflictError(RuntimeError):
    """Raised when an idempotency key is reused with a different persisted result."""


class DuplicateWorkItemError(RuntimeError):
    """Raised when a WorkItem ID has already been persisted."""


@dataclass(frozen=True, slots=True)
class WorkEvent:
    """One immutable persisted lifecycle event."""

    id: int
    work_item_id: str
    event_type: str
    from_state: WorkState | None
    to_state: WorkState | None
    attempt: int
    work_item_version: int
    _payload_json: str
    created_at: str

    @property
    def payload(self) -> dict[str, JsonValue]:
        """Return a detached copy of the event payload."""
        return cast(dict[str, JsonValue], json.loads(self._payload_json))


@dataclass(frozen=True, slots=True)
class SideEffectRecord:
    """Durable idempotency and provenance record for an external mutation."""

    idempotency_key: str
    work_item_id: str
    effect_type: str
    target: str
    external_id: str | None
    external_url: str | None
    created_by_contribai: bool
    auto_close: bool
    work_item_version: int
    created_at: str


SCHEMA_MIGRATIONS_TABLE: Final[str] = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  TEXT NOT NULL
)
"""

_CONTROL_PLANE_V1: Final[tuple[str, ...]] = (
    """
    CREATE TABLE IF NOT EXISTS work_items (
        id              TEXT PRIMARY KEY,
        repo            TEXT NOT NULL,
        issue_number    INTEGER,
        mode            TEXT NOT NULL,
        state           TEXT NOT NULL,
        attempt         INTEGER NOT NULL CHECK (attempt >= 1),
        budget_json     TEXT NOT NULL,
        version         INTEGER NOT NULL CHECK (version >= 0),
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS work_events (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        work_item_id        TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
        event_type          TEXT NOT NULL,
        from_state          TEXT,
        to_state            TEXT,
        attempt             INTEGER NOT NULL,
        work_item_version   INTEGER NOT NULL,
        payload_json        TEXT NOT NULL DEFAULT '{}',
        created_at          TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_work_events_item
    ON work_events(work_item_id, id)
    """,
    """
    CREATE TABLE IF NOT EXISTS review_requests (
        id                      TEXT PRIMARY KEY,
        work_item_id            TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
        attempt                 INTEGER NOT NULL,
        status                  TEXT NOT NULL,
        required_side_effects_json TEXT NOT NULL DEFAULT '[]',
        request_json            TEXT NOT NULL DEFAULT '{}',
        decision_json           TEXT,
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS verification_reports (
        id              TEXT PRIMARY KEY,
        work_item_id    TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
        attempt         INTEGER NOT NULL,
        status          TEXT NOT NULL,
        report_json     TEXT NOT NULL,
        created_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quota_reservations (
        id              TEXT PRIMARY KEY,
        work_item_id    TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
        provider        TEXT NOT NULL,
        amount_json     TEXT NOT NULL,
        status          TEXT NOT NULL,
        expires_at      TEXT,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS publish_permits (
        id                      TEXT PRIMARY KEY,
        work_item_id            TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
        review_request_id       TEXT REFERENCES review_requests(id),
        patch_hash              TEXT NOT NULL,
        approved_side_effects_json TEXT NOT NULL DEFAULT '[]',
        expires_at              TEXT NOT NULL,
        consumed_at             TEXT,
        created_at              TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS side_effects (
        idempotency_key         TEXT PRIMARY KEY,
        work_item_id            TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
        effect_type             TEXT NOT NULL,
        target                  TEXT NOT NULL,
        external_id             TEXT,
        external_url            TEXT,
        created_by_contribai    INTEGER NOT NULL CHECK (created_by_contribai IN (0, 1)),
        auto_close              INTEGER NOT NULL CHECK (auto_close IN (0, 1)),
        work_item_version       INTEGER NOT NULL,
        created_at              TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_side_effects_item
    ON side_effects(work_item_id, created_at)
    """,
)

MIGRATIONS: Final[tuple[tuple[int, str, tuple[str, ...]], ...]] = (
    (1, "contribution_control_plane", _CONTROL_PLANE_V1),
)

_CONNECTION_LOCKS: WeakKeyDictionary[aiosqlite.Connection, asyncio.Lock] = WeakKeyDictionary()

_SIDE_EFFECT_STATES: Final[frozenset[WorkState]] = frozenset(
    {
        WorkState.PUBLISH_RESERVED,
        WorkState.PUBLISHED,
        WorkState.CI_RUNNING,
        WorkState.MERGED,
    }
)

_INTERRUPTED_ACTIVE_STATES: Final[frozenset[WorkState]] = frozenset(
    {
        WorkState.PREPARING,
        WorkState.SOLVING,
        WorkState.PATCH_COLLECTING,
        WorkState.VERIFYING,
    }
)

_PUBLISH_BOUNDARY_STATES: Final[frozenset[WorkState]] = frozenset(
    {
        WorkState.PUBLISH_RESERVED,
        WorkState.PUBLISHED,
        WorkState.CI_RUNNING,
        WorkState.MERGED,
    }
)


def connection_transaction_lock(connection: aiosqlite.Connection) -> asyncio.Lock:
    """Return the process-local transaction lock owned by one SQLite connection."""
    lock = _CONNECTION_LOCKS.get(connection)
    if lock is None:
        lock = asyncio.Lock()
        _CONNECTION_LOCKS[connection] = lock
    return lock


async def migrate_work_item_schema(connection: aiosqlite.Connection) -> None:
    """Apply migrations once, serialized with all work on the same connection."""
    async with connection_transaction_lock(connection):
        await _migrate_work_item_schema_unlocked(connection)


async def _migrate_work_item_schema_unlocked(connection: aiosqlite.Connection) -> None:
    await connection.execute(SCHEMA_MIGRATIONS_TABLE)
    await connection.commit()

    for version, name, statements in MIGRATIONS:
        cursor = await connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
        )
        if await cursor.fetchone() is not None:
            continue

        await connection.execute("BEGIN IMMEDIATE")
        try:
            # Recheck under the write lock for concurrent startup safety.
            cursor = await connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
            )
            if await cursor.fetchone() is not None:
                await connection.rollback()
                continue
            for statement in statements:
                await connection.execute(statement)
            await connection.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, _utc_now()),
            )
        except BaseException:
            await connection.rollback()
            raise
        else:
            await connection.commit()


class WorkItemRepository:
    """Single-connection transactional authority for WorkItem persistence."""

    def __init__(
        self,
        connection: aiosqlite.Connection,
        transaction_lock: asyncio.Lock | None = None,
    ):
        self._connection = connection
        self._lock = transaction_lock or asyncio.Lock()

    @property
    def connection(self) -> aiosqlite.Connection:
        """Return the Memory-owned connection used by this repository."""
        return self._connection

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[None]:
        async with self._lock:
            await self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                await self._connection.rollback()
                raise
            else:
                await self._connection.commit()

    async def create(self, item: WorkItem) -> WorkItem:
        """Persist a new item and its creation event atomically."""
        if item.state is not WorkState.DISCOVERED or item.version != 0 or item.attempt != 1:
            raise ValueError("A new WorkItem must start at discovered/version 0/attempt 1")
        async with self._transaction():
            try:
                await self._connection.execute(
                    """
                    INSERT INTO work_items
                        (id, repo, issue_number, mode, state, attempt, budget_json,
                         version, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _work_item_values(item),
                )
            except aiosqlite.IntegrityError as exc:
                raise DuplicateWorkItemError(f"WorkItem already exists: {item.id}") from exc
            await self._insert_event(
                item,
                event_type="created",
                from_state=None,
                to_state=item.state,
                payload={},
            )
        return item

    async def get(self, work_item_id: str) -> WorkItem | None:
        """Load the current immutable item snapshot."""
        async with self._lock:
            return await self._get_unlocked(work_item_id)

    async def _get_unlocked(self, work_item_id: str) -> WorkItem | None:
        cursor = await self._connection.execute(
            """
            SELECT id, repo, issue_number, mode, state, attempt, budget_json,
                   version, created_at, updated_at
            FROM work_items WHERE id = ?
            """,
            (work_item_id,),
        )
        row = await cursor.fetchone()
        return _work_item_from_row(row) if row is not None else None

    async def transition(
        self,
        work_item_id: str,
        target: WorkState,
        *,
        expected_version: int,
        reason: str = "",
        payload: dict[str, JsonValue] | None = None,
    ) -> WorkItem:
        """Apply one allowlisted transition using optimistic concurrency."""
        target = WorkState(target)
        async with self._transaction():
            current = await self._require(work_item_id)
            _require_version(current, expected_version)
            validate_transition(current.state, target)
            updated = replace(
                current,
                state=target,
                version=current.version + 1,
                updated_at=_utc_now(),
            )
            await self._update_snapshot(current, updated)
            event_payload = dict(payload or {})
            if reason:
                event_payload["reason"] = reason
            await self._insert_event(
                updated,
                event_type="transition",
                from_state=current.state,
                to_state=target,
                payload=event_payload,
            )
        return updated

    async def retry(
        self,
        work_item_id: str,
        *,
        expected_version: int,
        reason: str,
    ) -> WorkItem:
        """Explicitly retry a failed item and increment its attempt counter."""
        async with self._transaction():
            current = await self._require(work_item_id)
            _require_version(current, expected_version)
            if current.state is not WorkState.NEEDS_FIX:
                raise InvalidWorkTransitionError(
                    f"Retry requires needs_fix, found {current.state.value}"
                )
            if await self._has_reached_publish_boundary(current.id):
                raise InvalidWorkTransitionError(
                    "Retry is forbidden after the WorkItem reached the published boundary; "
                    "create a new WorkItem instead"
                )
            updated = replace(
                current,
                state=WorkState.PREPARING,
                attempt=current.attempt + 1,
                version=current.version + 1,
                updated_at=_utc_now(),
            )
            await self._update_snapshot(current, updated)
            await self._insert_event(
                updated,
                event_type="retry",
                from_state=current.state,
                to_state=updated.state,
                payload={"reason": reason},
            )
        return updated

    async def append_event(
        self,
        work_item_id: str,
        event_type: str,
        *,
        payload: dict[str, JsonValue] | None = None,
        expected_version: int | None = None,
    ) -> WorkEvent:
        """Append a non-transition event atomically against an optional version."""
        if not event_type.strip():
            raise ValueError("event_type must not be empty")
        async with self._transaction():
            current = await self._require(work_item_id)
            if expected_version is not None:
                _require_version(current, expected_version)
            event_id = await self._insert_event(
                current,
                event_type=event_type,
                from_state=None,
                to_state=None,
                payload=payload or {},
            )
            event = await self._get_event(event_id)
        return event

    async def list_events(self, work_item_id: str) -> list[WorkEvent]:
        """Return the immutable event stream in insertion order."""
        async with self._lock:
            cursor = await self._connection.execute(
                """
                SELECT id, work_item_id, event_type, from_state, to_state, attempt,
                       work_item_version, payload_json, created_at
                FROM work_events WHERE work_item_id = ? ORDER BY id
                """,
                (work_item_id,),
            )
            return [_event_from_row(row) for row in await cursor.fetchall()]

    async def record_side_effect(
        self,
        work_item_id: str,
        *,
        expected_version: int,
        effect_type: str,
        target: str,
        external_id: str | None,
        external_url: str | None,
        created_by_contribai: bool,
        auto_close: bool,
    ) -> SideEffectRecord:
        """Persist one state-gated external mutation with deterministic idempotency."""
        if not effect_type.strip() or not target.strip():
            raise ValueError("effect_type and target must not be empty")
        idempotency_key = _side_effect_key(work_item_id, effect_type, target)
        async with self._transaction():
            existing = await self._get_side_effect(idempotency_key)
            if existing is not None:
                if not _same_side_effect_binding(
                    existing,
                    expected_version=expected_version,
                    external_id=external_id,
                    external_url=external_url,
                    created_by_contribai=created_by_contribai,
                    auto_close=auto_close,
                ):
                    raise SideEffectConflictError(
                        f"Side-effect idempotency key conflicts with persisted result: "
                        f"{idempotency_key}"
                    )
                return existing

            item = await self._require(work_item_id)
            _require_version(item, expected_version)
            if item.state not in _SIDE_EFFECT_STATES:
                raise IllegalSideEffectStateError(
                    f"Cannot record {effect_type} while WorkItem is {item.state.value}"
                )

            created_at = _utc_now()
            await self._connection.execute(
                """
                INSERT INTO side_effects
                    (idempotency_key, work_item_id, effect_type, target,
                     external_id, external_url, created_by_contribai, auto_close,
                     work_item_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    idempotency_key,
                    item.id,
                    effect_type,
                    target,
                    external_id,
                    external_url,
                    int(created_by_contribai),
                    int(auto_close),
                    expected_version,
                    created_at,
                ),
            )
            record = SideEffectRecord(
                idempotency_key=idempotency_key,
                work_item_id=item.id,
                effect_type=effect_type,
                target=target,
                external_id=external_id,
                external_url=external_url,
                created_by_contribai=created_by_contribai,
                auto_close=auto_close,
                work_item_version=expected_version,
                created_at=created_at,
            )
        return record

    async def count_side_effects(self, work_item_id: str) -> int:
        """Count persisted side effects for test and audit use."""
        async with self._lock:
            cursor = await self._connection.execute(
                "SELECT COUNT(*) FROM side_effects WHERE work_item_id = ?", (work_item_id,)
            )
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def recover_interrupted(self, *, reason: str) -> list[WorkItem]:
        """Fail active work and flag uncertain publication for explicit reconciliation."""
        placeholders = ",".join("?" for _ in _INTERRUPTED_ACTIVE_STATES)
        state_values = tuple(sorted(state.value for state in _INTERRUPTED_ACTIVE_STATES))
        recovered: list[WorkItem] = []
        async with self._transaction():
            cursor = await self._connection.execute(
                f"""
                SELECT id, repo, issue_number, mode, state, attempt, budget_json,
                       version, created_at, updated_at
                FROM work_items
                WHERE state IN ({placeholders})
                ORDER BY id
                """,
                state_values,
            )
            for row in await cursor.fetchall():
                current = _work_item_from_row(row)
                validate_transition(current.state, WorkState.NEEDS_FIX)
                updated = replace(
                    current,
                    state=WorkState.NEEDS_FIX,
                    version=current.version + 1,
                    updated_at=_utc_now(),
                )
                await self._update_snapshot(current, updated)
                await self._insert_event(
                    updated,
                    event_type="recovery",
                    from_state=current.state,
                    to_state=updated.state,
                    payload={"reason": reason},
                )
                recovered.append(updated)

            cursor = await self._connection.execute(
                """
                SELECT id, repo, issue_number, mode, state, attempt, budget_json,
                       version, created_at, updated_at
                FROM work_items
                WHERE state = ?
                ORDER BY id
                """,
                (WorkState.PUBLISH_RESERVED.value,),
            )
            for row in await cursor.fetchall():
                uncertain = _work_item_from_row(row)
                if await self._has_event(
                    uncertain.id,
                    event_type="reconciliation_required",
                    work_item_version=uncertain.version,
                ):
                    continue
                await self._insert_event(
                    uncertain,
                    event_type="reconciliation_required",
                    from_state=None,
                    to_state=None,
                    payload={
                        "reason": reason[:500],
                        "state": WorkState.PUBLISH_RESERVED.value,
                    },
                )
        return recovered

    async def _require(self, work_item_id: str) -> WorkItem:
        item = await self._get_unlocked(work_item_id)
        if item is None:
            raise WorkItemNotFoundError(work_item_id)
        return item

    async def _update_snapshot(self, previous: WorkItem, updated: WorkItem) -> None:
        cursor = await self._connection.execute(
            """
            UPDATE work_items
            SET state = ?, attempt = ?, version = ?, updated_at = ?
            WHERE id = ? AND version = ?
            """,
            (
                updated.state.value,
                updated.attempt,
                updated.version,
                updated.updated_at,
                previous.id,
                previous.version,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleWorkItemError(f"WorkItem {previous.id} version {previous.version} is stale")

    async def _insert_event(
        self,
        item: WorkItem,
        *,
        event_type: str,
        from_state: WorkState | None,
        to_state: WorkState | None,
        payload: dict[str, JsonValue],
    ) -> int:
        payload_json = _canonical_object(payload)
        cursor = await self._connection.execute(
            """
            INSERT INTO work_events
                (work_item_id, event_type, from_state, to_state, attempt,
                 work_item_version, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                event_type,
                from_state.value if from_state else None,
                to_state.value if to_state else None,
                item.attempt,
                item.version,
                payload_json,
                _utc_now(),
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a work event ID")
        return int(cursor.lastrowid)

    async def _get_event(self, event_id: int) -> WorkEvent:
        cursor = await self._connection.execute(
            """
            SELECT id, work_item_id, event_type, from_state, to_state, attempt,
                   work_item_version, payload_json, created_at
            FROM work_events WHERE id = ?
            """,
            (event_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError(f"Work event disappeared: {event_id}")
        return _event_from_row(row)

    async def _get_side_effect(self, idempotency_key: str) -> SideEffectRecord | None:
        cursor = await self._connection.execute(
            """
            SELECT idempotency_key, work_item_id, effect_type, target,
                   external_id, external_url, created_by_contribai, auto_close,
                   work_item_version, created_at
            FROM side_effects WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        )
        row = await cursor.fetchone()
        return _side_effect_from_row(row) if row is not None else None

    async def _has_event(
        self,
        work_item_id: str,
        *,
        event_type: str,
        work_item_version: int,
    ) -> bool:
        cursor = await self._connection.execute(
            """
            SELECT 1 FROM work_events
            WHERE work_item_id = ? AND event_type = ? AND work_item_version = ?
            LIMIT 1
            """,
            (work_item_id, event_type, work_item_version),
        )
        return await cursor.fetchone() is not None

    async def _has_reached_publish_boundary(self, work_item_id: str) -> bool:
        placeholders = ",".join("?" for _ in _PUBLISH_BOUNDARY_STATES)
        states = tuple(sorted(state.value for state in _PUBLISH_BOUNDARY_STATES))
        cursor = await self._connection.execute(
            f"""
            SELECT 1 FROM work_events
            WHERE work_item_id = ? AND to_state IN ({placeholders})
            LIMIT 1
            """,
            (work_item_id, *states),
        )
        return await cursor.fetchone() is not None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _require_version(item: WorkItem, expected_version: int) -> None:
    if item.version != expected_version:
        raise StaleWorkItemError(
            f"WorkItem {item.id} expected version {expected_version}, found {item.version}"
        )


def _canonical_object(payload: dict[str, JsonValue]) -> str:
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Event payload must be a JSON-compatible object") from exc


def _side_effect_key(work_item_id: str, effect_type: str, target: str) -> str:
    identity = _canonical_object(
        {"effect_type": effect_type, "target": target, "work_item_id": work_item_id}
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"side-effect:v1:{digest}"


def _same_side_effect_binding(
    persisted: SideEffectRecord,
    *,
    expected_version: int,
    external_id: str | None,
    external_url: str | None,
    created_by_contribai: bool,
    auto_close: bool,
) -> bool:
    return (
        persisted.work_item_version == expected_version
        and persisted.external_id == external_id
        and persisted.external_url == external_url
        and persisted.created_by_contribai is created_by_contribai
        and persisted.auto_close is auto_close
    )


def _work_item_values(item: WorkItem) -> tuple[object, ...]:
    return (
        item.id,
        item.repo,
        item.issue_number,
        item.mode.value,
        item.state.value,
        item.attempt,
        item.budget.to_json(),
        item.version,
        item.created_at,
        item.updated_at,
    )


def _work_item_from_row(row: tuple[object, ...]) -> WorkItem:
    return WorkItem(
        id=str(row[0]),
        repo=str(row[1]),
        issue_number=int(row[2]) if row[2] is not None else None,
        mode=ExecutionMode(str(row[3])),
        state=WorkState(str(row[4])),
        attempt=int(row[5]),
        budget=BudgetSnapshot.from_json(str(row[6])),
        version=int(row[7]),
        created_at=str(row[8]),
        updated_at=str(row[9]),
    )


def _event_from_row(row: tuple[object, ...]) -> WorkEvent:
    return WorkEvent(
        id=int(row[0]),
        work_item_id=str(row[1]),
        event_type=str(row[2]),
        from_state=WorkState(str(row[3])) if row[3] is not None else None,
        to_state=WorkState(str(row[4])) if row[4] is not None else None,
        attempt=int(row[5]),
        work_item_version=int(row[6]),
        _payload_json=str(row[7]),
        created_at=str(row[8]),
    )


def _side_effect_from_row(row: tuple[object, ...]) -> SideEffectRecord:
    return SideEffectRecord(
        idempotency_key=str(row[0]),
        work_item_id=str(row[1]),
        effect_type=str(row[2]),
        target=str(row[3]),
        external_id=str(row[4]) if row[4] is not None else None,
        external_url=str(row[5]) if row[5] is not None else None,
        created_by_contribai=bool(row[6]),
        auto_close=bool(row[7]),
        work_item_version=int(row[8]),
        created_at=str(row[9]),
    )
