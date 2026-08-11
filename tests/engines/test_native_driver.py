"""Native driver execution tests."""

from __future__ import annotations

import asyncio

import pytest

from contribai.context.builder import ContextBuilder
from contribai.context.rules import RepoRules
from contribai.core.models import Repository
from contribai.engines.models import EngineRequest, EngineStatus, EngineUsage, ExecutionLease
from contribai.engines.native import NativeEngineDriver, NativeExecutionResult
from contribai.execution.budget import ExecutionBudget
from contribai.localization import ContributionTask
from contribai.publishing.policy import CapabilityPolicy


def _request(budget: ExecutionBudget) -> EngineRequest:
    repo = Repository(owner="owner", name="repo", full_name="owner/repo", language="Python")
    context = ContextBuilder(max_context_tokens=200).build(
        repo,
        files={"src/service.py": "def handle(value):\n    return value\n"},
    )
    return EngineRequest(
        work_id="work-1",
        attempt_id="attempt-1",
        task=ContributionTask(title="Fix handle"),
        context=context,
        repo_rules=RepoRules(),
        budget=budget,
        capability_policy=CapabilityPolicy(),
        engine_config={"model": "test"},
    )


def _lease(budget: ExecutionBudget, *, cancel_event: asyncio.Event | None = None) -> ExecutionLease:
    return ExecutionLease(
        work_id="work-1",
        attempt_id="attempt-1",
        workspace_ref="snapshot-1",
        budget=budget,
        cancel_event=cancel_event,
    )


@pytest.mark.asyncio
async def test_native_driver_mutates_only_supplied_workspace_and_returns_outcome() -> None:
    calls = []

    async def operation(request, execution):
        calls.append((request.work_id, execution.workspace_ref))
        return NativeExecutionResult(
            events=(),
            usage=EngineUsage(tool_calls=2),
            cost_usd=0.2,
            metadata={"changed_files": ["src/service.py"]},
        )

    budget = ExecutionBudget(10, 1, 60, 2)
    outcome = await NativeEngineDriver(operation).run(_request(budget), _lease(budget))

    assert outcome.status is EngineStatus.COMPLETED
    assert outcome.terminal is True
    assert outcome.cost_usd == 0.2
    assert calls == [("work-1", "snapshot-1")]
    assert "changed_files" in outcome.metadata
    assert "patch" not in outcome.metadata


@pytest.mark.asyncio
async def test_native_driver_has_no_implicit_fallback_when_operation_missing() -> None:
    budget = ExecutionBudget(10, 1, 60, 2)

    outcome = await NativeEngineDriver().run(_request(budget), _lease(budget))

    assert outcome.status is EngineStatus.UNSUPPORTED
    assert outcome.terminal is True
    assert budget.steps_used == 1


@pytest.mark.asyncio
async def test_native_driver_honors_cancellation_before_operation() -> None:
    called = False

    async def operation(request, execution):
        nonlocal called
        called = True

    cancel_event = asyncio.Event()
    cancel_event.set()
    budget = ExecutionBudget(10, 1, 60, 2)

    outcome = await NativeEngineDriver(operation).run(
        _request(budget), _lease(budget, cancel_event=cancel_event)
    )

    assert outcome.status is EngineStatus.CANCELLED
    assert called is False


@pytest.mark.asyncio
async def test_native_driver_allows_exact_step_boundary_for_current_attempt() -> None:
    budget = ExecutionBudget(1, 1, 60, 2)

    async def operation(request, execution):
        return NativeExecutionResult()

    outcome = await NativeEngineDriver(operation).run(_request(budget), _lease(budget))

    assert outcome.status is EngineStatus.COMPLETED
    assert budget.exhausted is True
