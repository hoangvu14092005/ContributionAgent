"""ExecutionSupervisor lifecycle contracts."""

from __future__ import annotations

import pytest

from contribai.control.command_service import CommandService
from contribai.control.mode import ExecutionMode
from contribai.control.pipeline_executor import PipelineWorkItemExecutor
from contribai.control.supervisor import ExecutionSupervisor
from contribai.domain.state import WorkState


class _ClosingExecutor:
    def __init__(self) -> None:
        self.seen_state = None

    async def execute(self, item, commands):
        self.seen_state = item.state
        return await commands.cancel(item.id, reason="test executor completed")


class _FailingExecutor:
    async def execute(self, item, commands):
        raise RuntimeError("engine crashed")


@pytest.mark.asyncio
async def test_supervisor_advances_live_work_to_executor_and_persists_close(memory) -> None:
    item = await CommandService(memory).submit(
        "owner/repo",
        mode=ExecutionMode.LIVE,
        idempotency_key="supervisor-close",
    )
    executor = _ClosingExecutor()

    result = await ExecutionSupervisor(memory, executor=executor).run_once(item.id)

    assert executor.seen_state is WorkState.SOLVING
    assert result.state is WorkState.CLOSED
    events = await memory.work_items.list_events(item.id)
    assert [event.to_state for event in events if event.to_state] == [
        WorkState.DISCOVERED,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.SOLVING,
        WorkState.CLOSED,
    ]


@pytest.mark.asyncio
async def test_supervisor_fail_closes_an_executor_crash(memory) -> None:
    item = await CommandService(memory).submit(
        "owner/repo",
        mode=ExecutionMode.LIVE,
        idempotency_key="supervisor-failure",
    )

    result = await ExecutionSupervisor(memory, executor=_FailingExecutor()).run_once(item.id)

    assert result.state is WorkState.CLOSED
    events = await memory.work_items.list_events(item.id)
    assert any(
        event.event_type == "transition"
        and event.to_state is WorkState.CLOSED
        and "engine crashed" in str(event.payload)
        for event in events
    )


@pytest.mark.asyncio
async def test_pipeline_executor_fails_closed_until_sandbox_is_enabled(
    memory, sample_config
) -> None:
    item = await CommandService(memory).submit(
        "owner/repo",
        mode=ExecutionMode.LIVE,
        idempotency_key="supervisor-sandbox-required",
    )

    result = await ExecutionSupervisor(
        memory,
        executor=PipelineWorkItemExecutor(sample_config),
    ).run_once(item.id)

    assert result.state is WorkState.CLOSED
    events = await memory.work_items.list_events(item.id)
    assert any(
        event.to_state is WorkState.CLOSED
        and "sandbox.enabled=true" in str(event.payload)
        for event in events
    )
