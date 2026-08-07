"""Mini-SWE in-process adapter tests."""

from __future__ import annotations

import asyncio

import pytest

from contribai.engines.adapters.base import AdapterResult
from contribai.engines.adapters.mini_swe import MiniSWEInProcessDriver
from contribai.engines.models import EngineStatus, EngineUsage


@pytest.mark.asyncio
async def test_mini_swe_receives_outer_workspace_and_returns_no_patch(
    engine_request,
    execution_lease,
):
    captured = {}

    async def runner(*, workspace_path, prompt, model_gateway, credential_lease):
        captured.update(
            workspace_path=workspace_path,
            prompt=prompt,
            model_gateway=model_gateway,
            credential_lease=credential_lease,
        )
        return AdapterResult(
            events=({"type": "step", "output": "changed"},),
            usage=EngineUsage(tool_calls=2),
            cost_usd=0.25,
            metadata={"changed_files": ["src/service.py"]},
        )

    outcome = await MiniSWEInProcessDriver(runner=runner).run(
        engine_request,
        execution_lease,
    )

    assert outcome.status is EngineStatus.COMPLETED
    assert captured["workspace_path"] == execution_lease.workspace.path
    assert "Fix handle" in captured["prompt"]
    assert captured["model_gateway"] is execution_lease.model_gateway
    assert captured["credential_lease"] is execution_lease.credential_lease
    assert "patch" not in outcome.metadata
    assert execution_lease.budget.cost_usd == 0.25


@pytest.mark.asyncio
async def test_mini_swe_is_unsupported_without_binding_or_runner(
    engine_request,
    execution_lease,
):
    outcome = await MiniSWEInProcessDriver().run(engine_request, execution_lease)

    assert outcome.status is EngineStatus.UNSUPPORTED
    assert "binding" in outcome.exit_reason.lower()


@pytest.mark.asyncio
async def test_mini_swe_cancellation_does_not_deadlock_scheduler(
    engine_request,
    execution_lease,
):
    started = asyncio.Event()

    async def runner(**_kwargs):
        started.set()
        await asyncio.sleep(10)

    task = asyncio.create_task(
        MiniSWEInProcessDriver(runner=runner).run(engine_request, execution_lease)
    )
    await started.wait()
    execution_lease.cancel_event.set()
    outcome = await asyncio.wait_for(task, timeout=1)

    assert outcome.status is EngineStatus.CANCELLED
