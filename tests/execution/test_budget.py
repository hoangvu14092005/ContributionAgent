"""Execution budget and trajectory contracts."""

from __future__ import annotations

import asyncio

import pytest

from contribai.execution.budget import BudgetExceededError, ExecutionBudget
from contribai.execution.trajectory import AgentTrajectory, ExecutionEvent


@pytest.mark.asyncio
async def test_budget_enforces_step_cost_time_and_tool_failure_limits() -> None:
    now = [100.0]
    budget = ExecutionBudget(
        max_steps=2,
        max_cost_usd=1.0,
        max_wall_time_sec=10,
        max_tool_failures=1,
        clock=lambda: now[0],
    )

    await budget.consume_step()
    await budget.consume_step()
    with pytest.raises(BudgetExceededError, match="steps"):
        await budget.consume_step()
    with pytest.raises(BudgetExceededError):
        await budget.add_cost(0.01)


@pytest.mark.asyncio
async def test_budget_exhaustion_is_sticky_and_blocks_publish_callback() -> None:
    budget = ExecutionBudget(
        max_steps=1,
        max_cost_usd=5,
        max_wall_time_sec=10,
        max_tool_failures=2,
    )
    await budget.consume_step()
    publish_called = False

    async def publish() -> None:
        nonlocal publish_called
        publish_called = True

    with pytest.raises(BudgetExceededError):
        await budget.run_if_allowed(publish)
    assert publish_called is False
    with pytest.raises(BudgetExceededError):
        await budget.ensure_publish_allowed()


@pytest.mark.asyncio
async def test_budget_consumption_is_race_safe() -> None:
    budget = ExecutionBudget(
        max_steps=3,
        max_cost_usd=10,
        max_wall_time_sec=10,
        max_tool_failures=10,
    )
    results = await asyncio.gather(
        *(budget.consume_step() for _ in range(10)),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 3
    assert sum(isinstance(result, BudgetExceededError) for result in results) == 7


def test_budget_snapshot_round_trip() -> None:
    budget = ExecutionBudget(
        max_steps=3, max_cost_usd=1.5, max_wall_time_sec=4, max_tool_failures=2
    )
    snapshot = budget.snapshot()
    restored = ExecutionBudget.from_snapshot(snapshot)
    assert restored.max_steps == 3
    assert restored.max_cost_usd == 1.5
    assert restored.steps_used == 0


@pytest.mark.asyncio
async def test_trajectory_is_ordered_bounded_and_detached() -> None:
    trajectory = AgentTrajectory(max_events=2)
    await asyncio.gather(
        trajectory.append(ExecutionEvent(kind="one", data={"value": 1})),
        trajectory.append(ExecutionEvent(kind="two", data={"value": 2})),
        trajectory.append(ExecutionEvent(kind="three", data={"value": 3})),
    )
    snapshot = trajectory.snapshot()
    assert len(snapshot) == 2
    assert [event.kind for event in snapshot] == ["two", "three"]
    assert snapshot[0].data == {"value": 2}
    with pytest.raises(TypeError):
        snapshot[0].data["value"] = 99  # type: ignore[index]
    assert trajectory.snapshot()[0].data["value"] == 2
