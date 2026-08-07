"""Persistence, migration, concurrency, and recovery tests for WorkItem."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from contribai.control.mode import ExecutionMode
from contribai.domain.state import InvalidWorkTransitionError, WorkState
from contribai.domain.work_item import BudgetSnapshot, WorkItem
from contribai.orchestrator.memory import Memory
from contribai.storage.work_items import (
    IllegalSideEffectStateError,
    SideEffectConflictError,
    StaleWorkItemError,
    WorkItemRepository,
    connection_transaction_lock,
    migrate_work_item_schema,
)


@pytest.fixture
async def memory(tmp_path: Path):
    value = Memory(tmp_path / "control-plane.db")
    await value.init()
    yield value
    await value.close()


def _new_item(work_id: str = "work-1") -> WorkItem:
    return WorkItem.new(
        work_id=work_id,
        repo="owner/repo",
        issue_number=42,
        mode=ExecutionMode.REVIEW_ONLY,
        budget=BudgetSnapshot.from_mapping(
            {"max_steps": 8, "max_cost_usd": 1.25, "labels": ["task-5"]}
        ),
    )


async def _advance(
    repository: WorkItemRepository,
    item: WorkItem,
    *states: WorkState,
) -> WorkItem:
    current = item
    for state in states:
        current = await repository.transition(
            current.id,
            state,
            expected_version=current.version,
            reason="test advance",
        )
    return current


@pytest.mark.asyncio
async def test_memory_owns_one_repository_and_create_get_round_trips_exact_snapshot(memory) -> None:
    repository = memory.work_items
    created = await repository.create(_new_item())
    loaded = await repository.get(created.id)

    assert repository.connection is memory.connection
    assert loaded == created
    assert loaded is not None
    assert loaded.budget.to_mapping() == {
        "labels": ["task-5"],
        "max_cost_usd": 1.25,
        "max_steps": 8,
    }
    assert loaded.attempt == 1
    assert loaded.version == 0


@pytest.mark.asyncio
async def test_every_transition_persists_an_immutable_event(memory) -> None:
    repository = memory.work_items
    item = await repository.create(_new_item())
    item = await repository.transition(
        item.id,
        WorkState.QUALIFIED,
        expected_version=item.version,
        reason="qualified by policy",
        payload={"score": 0.91},
    )

    events = await repository.list_events(item.id)
    assert [(event.event_type, event.from_state, event.to_state) for event in events] == [
        ("created", None, WorkState.DISCOVERED),
        ("transition", WorkState.DISCOVERED, WorkState.QUALIFIED),
    ]
    assert events[-1].work_item_version == item.version
    assert events[-1].payload == {"reason": "qualified by policy", "score": 0.91}


@pytest.mark.asyncio
async def test_illegal_transition_leaves_item_and_events_unchanged(memory) -> None:
    repository = memory.work_items
    item = await repository.create(_new_item())

    with pytest.raises(InvalidWorkTransitionError):
        await repository.transition(
            item.id,
            WorkState.PUBLISHED,
            expected_version=item.version,
        )

    assert await repository.get(item.id) == item
    assert len(await repository.list_events(item.id)) == 1


@pytest.mark.asyncio
async def test_wait_resume_and_cancel_are_persisted(memory) -> None:
    repository = memory.work_items
    first = await repository.create(_new_item("resume"))
    first = await _advance(
        repository,
        first,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.SOLVING,
        WorkState.ENGINE_WAITING_APPROVAL,
    )
    resumed = await repository.transition(
        first.id,
        WorkState.SOLVING,
        expected_version=first.version,
        reason="approval granted",
    )
    assert resumed.state is WorkState.SOLVING

    second = await repository.create(_new_item("cancel"))
    second = await _advance(
        repository,
        second,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.SOLVING,
        WorkState.ENGINE_WAITING_APPROVAL,
    )
    cancelled = await repository.transition(
        second.id,
        WorkState.CLOSED,
        expected_version=second.version,
        reason="approval denied",
    )
    assert cancelled.state is WorkState.CLOSED


@pytest.mark.asyncio
async def test_retry_is_explicit_and_increments_attempt(memory) -> None:
    repository = memory.work_items
    item = await repository.create(_new_item())
    item = await _advance(
        repository,
        item,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.NEEDS_FIX,
    )

    with pytest.raises(InvalidWorkTransitionError):
        await repository.transition(
            item.id,
            WorkState.PREPARING,
            expected_version=item.version,
        )

    retried = await repository.retry(item.id, expected_version=item.version, reason="repair")
    assert retried.state is WorkState.PREPARING
    assert retried.attempt == 2
    assert retried.version == item.version + 1
    assert (await repository.list_events(item.id))[-1].event_type == "retry"


@pytest.mark.asyncio
async def test_legacy_post_publish_needs_fix_record_cannot_be_retried(memory) -> None:
    repository = memory.work_items
    item = await repository.create(_new_item())
    item = await _advance(
        repository,
        item,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.SOLVING,
        WorkState.PATCH_COLLECTING,
        WorkState.PATCHED,
        WorkState.VERIFYING,
        WorkState.VERIFIED,
        WorkState.REVIEW_PENDING,
        WorkState.APPROVED,
        WorkState.PUBLISH_RESERVED,
        WorkState.PUBLISHED,
    )
    legacy_version = item.version + 1
    await memory.connection.execute(
        "UPDATE work_items SET state = ?, version = ? WHERE id = ?",
        (WorkState.NEEDS_FIX.value, legacy_version, item.id),
    )
    await memory.connection.execute(
        """
        INSERT INTO work_events
            (work_item_id, event_type, from_state, to_state, attempt,
             work_item_version, payload_json, created_at)
        VALUES (?, 'legacy_transition', ?, ?, ?, ?, '{}', ?)
        """,
        (
            item.id,
            WorkState.PUBLISHED.value,
            WorkState.NEEDS_FIX.value,
            item.attempt,
            legacy_version,
            item.updated_at,
        ),
    )
    await memory.connection.commit()

    with pytest.raises(InvalidWorkTransitionError, match="published"):
        await repository.retry(item.id, expected_version=legacy_version, reason="must be new work")


@pytest.mark.asyncio
async def test_repository_rejects_needs_fix_after_publication(memory) -> None:
    repository = memory.work_items
    item = await repository.create(_new_item())
    item = await _advance(
        repository,
        item,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.SOLVING,
        WorkState.PATCH_COLLECTING,
        WorkState.PATCHED,
        WorkState.VERIFYING,
        WorkState.VERIFIED,
        WorkState.REVIEW_PENDING,
        WorkState.APPROVED,
        WorkState.PUBLISH_RESERVED,
        WorkState.PUBLISHED,
    )

    with pytest.raises(InvalidWorkTransitionError):
        await repository.transition(
            item.id,
            WorkState.NEEDS_FIX,
            expected_version=item.version,
        )
    assert await repository.get(item.id) == item

    item = await repository.transition(
        item.id,
        WorkState.CI_RUNNING,
        expected_version=item.version,
    )
    with pytest.raises(InvalidWorkTransitionError):
        await repository.transition(
            item.id,
            WorkState.NEEDS_FIX,
            expected_version=item.version,
        )
    assert await repository.get(item.id) == item


@pytest.mark.asyncio
async def test_concurrent_transitions_reject_the_stale_snapshot(memory) -> None:
    repository = memory.work_items
    item = await repository.create(_new_item())

    results = await asyncio.gather(
        repository.transition(
            item.id,
            WorkState.QUALIFIED,
            expected_version=item.version,
        ),
        repository.transition(
            item.id,
            WorkState.CLOSED,
            expected_version=item.version,
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, WorkItem) for result in results) == 1
    assert sum(isinstance(result, StaleWorkItemError) for result in results) == 1
    loaded = await repository.get(item.id)
    assert loaded is not None
    assert loaded.version == 1
    assert len(await repository.list_events(item.id)) == 2


@pytest.mark.asyncio
async def test_append_event_is_version_checked_and_does_not_mutate_item(memory) -> None:
    repository = memory.work_items
    item = await repository.create(_new_item())
    event = await repository.append_event(
        item.id,
        "engine_output",
        payload={"line_count": 4},
        expected_version=item.version,
    )

    assert event.event_type == "engine_output"
    assert event.payload == {"line_count": 4}
    assert event.work_item_version == item.version
    assert await repository.get(item.id) == item

    with pytest.raises(StaleWorkItemError):
        await repository.append_event(
            item.id,
            "stale_output",
            expected_version=item.version + 1,
        )
    assert [value.event_type for value in await repository.list_events(item.id)] == [
        "created",
        "engine_output",
    ]


@pytest.mark.asyncio
async def test_transition_rolls_back_item_update_when_event_insert_fails(memory) -> None:
    repository = memory.work_items
    item = await repository.create(_new_item())
    await memory.connection.execute(
        """
        CREATE TRIGGER reject_qualified_event
        BEFORE INSERT ON work_events
        WHEN NEW.to_state = 'qualified'
        BEGIN
            SELECT RAISE(ABORT, 'event rejected');
        END
        """
    )
    await memory.connection.commit()

    with pytest.raises(sqlite3.IntegrityError):
        await repository.transition(
            item.id,
            WorkState.QUALIFIED,
            expected_version=item.version,
        )

    assert await repository.get(item.id) == item
    assert len(await repository.list_events(item.id)) == 1


@pytest.mark.asyncio
async def test_legacy_memory_write_cannot_commit_a_partial_work_item_transaction(
    memory,
    monkeypatch,
) -> None:
    repository = memory.work_items
    item = await repository.create(_new_item())
    event_insert_started = asyncio.Event()
    allow_event_failure = asyncio.Event()

    async def fail_event_insert(*args, **kwargs):
        event_insert_started.set()
        await allow_event_failure.wait()
        raise sqlite3.IntegrityError("forced event failure")

    monkeypatch.setattr(repository, "_insert_event", fail_event_insert)
    transition_task = asyncio.create_task(
        repository.transition(
            item.id,
            WorkState.QUALIFIED,
            expected_version=item.version,
        )
    )
    await event_insert_started.wait()

    legacy_write = asyncio.create_task(memory.record_analysis("other/repo", "python", 1, 0))
    done, _ = await asyncio.wait({legacy_write}, timeout=0.05)
    legacy_write_was_blocked = legacy_write not in done

    allow_event_failure.set()
    with pytest.raises(sqlite3.IntegrityError):
        await transition_task
    await legacy_write

    assert legacy_write_was_blocked, "Memory writers must share the repository transaction lock"
    assert await repository.get(item.id) == item


@pytest.mark.asyncio
async def test_side_effect_is_state_gated_and_idempotent(memory) -> None:
    repository = memory.work_items
    item = await repository.create(_new_item())

    with pytest.raises(IllegalSideEffectStateError):
        await repository.record_side_effect(
            item.id,
            expected_version=item.version,
            effect_type="create_pr",
            target="owner/repo:patch-sha",
            external_id="123",
            external_url="https://github.com/owner/repo/pull/123",
            created_by_contribai=True,
            auto_close=False,
        )

    item = await _advance(
        repository,
        item,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.SOLVING,
        WorkState.PATCH_COLLECTING,
        WorkState.PATCHED,
        WorkState.VERIFYING,
        WorkState.VERIFIED,
        WorkState.REVIEW_PENDING,
        WorkState.APPROVED,
        WorkState.PUBLISH_RESERVED,
    )
    first = await repository.record_side_effect(
        item.id,
        expected_version=item.version,
        effect_type="create_pr",
        target="owner/repo:patch-sha",
        external_id="123",
        external_url="https://github.com/owner/repo/pull/123",
        created_by_contribai=True,
        auto_close=False,
    )
    assert first.work_item_version == item.version

    published = await repository.transition(
        item.id,
        WorkState.PUBLISHED,
        expected_version=item.version,
    )
    duplicate = await repository.record_side_effect(
        item.id,
        expected_version=item.version,
        effect_type="create_pr",
        target="owner/repo:patch-sha",
        external_id="123",
        external_url="https://github.com/owner/repo/pull/123",
        created_by_contribai=True,
        auto_close=False,
    )

    assert duplicate == first
    assert published.version == item.version + 1
    assert first.created_by_contribai is True
    assert first.auto_close is False
    assert await repository.count_side_effects(item.id) == 1


@pytest.mark.asyncio
async def test_side_effect_rejects_stale_writer_before_insert(memory) -> None:
    repository = memory.work_items
    item = await repository.create(_new_item())
    item = await _advance(
        repository,
        item,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.SOLVING,
        WorkState.PATCH_COLLECTING,
        WorkState.PATCHED,
        WorkState.VERIFYING,
        WorkState.VERIFIED,
        WorkState.REVIEW_PENDING,
        WorkState.APPROVED,
        WorkState.PUBLISH_RESERVED,
    )
    await repository.transition(
        item.id,
        WorkState.PUBLISHED,
        expected_version=item.version,
    )

    with pytest.raises(StaleWorkItemError):
        await repository.record_side_effect(
            item.id,
            expected_version=item.version,
            effect_type="create_issue",
            target="owner/repo:issue-for-patch",
            external_id="44",
            external_url="https://github.com/owner/repo/issues/44",
            created_by_contribai=True,
            auto_close=True,
        )
    assert await repository.count_side_effects(item.id) == 0


@pytest.mark.asyncio
async def test_side_effect_same_key_with_different_result_raises_typed_conflict(memory) -> None:
    repository = memory.work_items
    item = await repository.create(_new_item())
    item = await _advance(
        repository,
        item,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.SOLVING,
        WorkState.PATCH_COLLECTING,
        WorkState.PATCHED,
        WorkState.VERIFYING,
        WorkState.VERIFIED,
        WorkState.REVIEW_PENDING,
        WorkState.APPROVED,
        WorkState.PUBLISH_RESERVED,
    )
    await repository.record_side_effect(
        item.id,
        expected_version=item.version,
        effect_type="create_pr",
        target="owner/repo:patch-sha",
        external_id="123",
        external_url="https://github.com/owner/repo/pull/123",
        created_by_contribai=True,
        auto_close=False,
    )

    with pytest.raises(SideEffectConflictError):
        await repository.record_side_effect(
            item.id,
            expected_version=item.version,
            effect_type="create_pr",
            target="owner/repo:patch-sha",
            external_id="999",
            external_url="https://github.com/owner/repo/pull/999",
            created_by_contribai=False,
            auto_close=True,
        )
    assert await repository.count_side_effects(item.id) == 1


@pytest.mark.asyncio
async def test_restart_preserves_exact_item_version_attempt_budget_and_events(
    tmp_path: Path,
) -> None:
    path = tmp_path / "restart.db"
    first_memory = Memory(path)
    await first_memory.init()
    item = await first_memory.work_items.create(_new_item())
    item = await _advance(
        first_memory.work_items,
        item,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.NEEDS_FIX,
    )
    item = await first_memory.work_items.retry(
        item.id,
        expected_version=item.version,
        reason="second attempt",
    )
    expected_events = await first_memory.work_items.list_events(item.id)
    await first_memory.close()

    second_memory = Memory(path)
    await second_memory.init()
    try:
        assert await second_memory.work_items.get(item.id) == item
        assert await second_memory.work_items.list_events(item.id) == expected_events
    finally:
        await second_memory.close()


@pytest.mark.asyncio
async def test_migration_is_versioned_idempotent_and_preserves_existing_memory_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    async with aiosqlite.connect(path) as connection:
        await connection.execute(
            "CREATE TABLE analyzed_repos (full_name TEXT PRIMARY KEY, language TEXT)"
        )
        await connection.execute(
            "INSERT INTO analyzed_repos (full_name, language) VALUES ('legacy/repo', 'python')"
        )
        await connection.commit()

    first = Memory(path)
    await first.init()
    versions_before = await first.applied_schema_versions()
    await first.close()

    second = Memory(path)
    await second.init()
    try:
        versions_after = await second.applied_schema_versions()
        cursor = await second.connection.execute(
            "SELECT language FROM analyzed_repos WHERE full_name = 'legacy/repo'"
        )
        assert await cursor.fetchone() == ("python",)
        assert versions_before == versions_after
        assert versions_after

        cursor = await second.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        tables = {row[0] for row in await cursor.fetchall()}
        assert {
            "work_items",
            "work_events",
            "review_requests",
            "verification_reports",
            "quota_reservations",
            "publish_permits",
            "side_effects",
            "schema_migrations",
        } <= tables
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_concurrent_migrations_on_same_connection_are_serialized(tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "concurrent-migration.db") as connection:
        await asyncio.gather(*(migrate_work_item_schema(connection) for _ in range(5)))

        cursor = await connection.execute(
            "SELECT version, COUNT(*) FROM schema_migrations GROUP BY version"
        )
        assert await cursor.fetchall() == [(1, 1), (2, 1)]


@pytest.mark.asyncio
async def test_memory_reuses_connection_migration_lock_without_deadlock(tmp_path: Path) -> None:
    memory = Memory(tmp_path / "shared-lock.db")
    await asyncio.wait_for(memory.init(), timeout=1)
    try:
        assert memory._transaction_lock is connection_transaction_lock(memory.connection)
        item = await asyncio.wait_for(memory.work_items.create(_new_item()), timeout=1)
        assert item.state is WorkState.DISCOVERED
    finally:
        await memory.close()


@pytest.mark.asyncio
async def test_explicit_recovery_marks_active_work_needs_fix_but_preserves_waiting(memory) -> None:
    repository = memory.work_items
    active = await repository.create(_new_item("active"))
    active = await _advance(
        repository,
        active,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.SOLVING,
    )
    waiting = await repository.create(_new_item("waiting"))
    waiting = await _advance(
        repository,
        waiting,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.SOLVING,
        WorkState.ENGINE_WAITING_APPROVAL,
    )

    recovered = await repository.recover_interrupted(reason="process restarted")

    assert [item.id for item in recovered] == [active.id]
    loaded_active = await repository.get(active.id)
    loaded_waiting = await repository.get(waiting.id)
    assert loaded_active is not None and loaded_active.state is WorkState.NEEDS_FIX
    assert loaded_active.version == active.version + 1
    assert loaded_waiting == waiting
    assert (await repository.list_events(active.id))[-1].event_type == "recovery"


@pytest.mark.asyncio
async def test_restart_recovery_preserves_publish_reserved_for_reconciliation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "publish-reconciliation.db"
    first_memory = Memory(path)
    await first_memory.init()
    item = await first_memory.work_items.create(_new_item("publish-uncertain"))
    item = await _advance(
        first_memory.work_items,
        item,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.SOLVING,
        WorkState.PATCH_COLLECTING,
        WorkState.PATCHED,
        WorkState.VERIFYING,
        WorkState.VERIFIED,
        WorkState.REVIEW_PENDING,
        WorkState.APPROVED,
        WorkState.PUBLISH_RESERVED,
    )
    await first_memory.close()

    second_memory = Memory(path)
    await second_memory.init()
    try:
        with pytest.raises(InvalidWorkTransitionError):
            await second_memory.work_items.transition(
                item.id,
                WorkState.CLOSED,
                expected_version=item.version,
                reason="generic close is not reconciliation",
            )
        assert await second_memory.work_items.get(item.id) == item

        assert await second_memory.work_items.recover_interrupted(reason="restart") == []
        assert await second_memory.work_items.recover_interrupted(reason="restart again") == []
        loaded = await second_memory.work_items.get(item.id)
        assert loaded == item
        events = await second_memory.work_items.list_events(item.id)
        reconciliation_events = [
            event for event in events if event.event_type == "reconciliation_required"
        ]
        assert len(reconciliation_events) == 1
        assert reconciliation_events[0].work_item_version == item.version

        with pytest.raises(InvalidWorkTransitionError):
            await second_memory.work_items.retry(
                item.id,
                expected_version=item.version,
                reason="unsafe retry",
            )
    finally:
        await second_memory.close()


@pytest.mark.asyncio
async def test_v1_database_migrates_publish_permit_proof_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "v1.db"
    connection = await aiosqlite.connect(db_path)
    try:
        await connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        await connection.execute(
            """
            CREATE TABLE publish_permits (
                id TEXT PRIMARY KEY, work_item_id TEXT NOT NULL,
                review_request_id TEXT, patch_hash TEXT NOT NULL,
                approved_side_effects_json TEXT NOT NULL DEFAULT '[]',
                expires_at TEXT NOT NULL, consumed_at TEXT, created_at TEXT NOT NULL
            )
            """
        )
        await connection.execute(
            "INSERT INTO schema_migrations VALUES (1, 'contribution_control_plane', 'now')"
        )
        await connection.commit()
        await migrate_work_item_schema(connection)
        cursor = await connection.execute("PRAGMA table_info(publish_permits)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert {"base_sha", "verification_id", "quota_reservation_id"} <= columns
        cursor = await connection.execute("SELECT name FROM schema_migrations WHERE version = 2")
        assert await cursor.fetchone() == ("publish_permit_proof_bindings",)
    finally:
        await connection.close()
