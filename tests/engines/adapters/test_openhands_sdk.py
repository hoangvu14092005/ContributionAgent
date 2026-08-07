"""OpenHands SDK adapter tests."""

from __future__ import annotations

import asyncio

import pytest

from contribai.engines.adapters.base import AdapterResult
from contribai.engines.adapters.openhands_sdk import OpenHandsSDKDriver
from contribai.engines.models import EngineStatus


@pytest.mark.asyncio
async def test_openhands_uses_outer_local_workspace_without_nested_docker(
    engine_request,
    execution_lease,
):
    captured = {}

    async def agent_factory(
        *,
        workspace_path,
        workspace_backend,
        sandbox_mode,
        use_docker,
        prompt,
    ):
        captured.update(
            workspace_path=workspace_path,
            workspace_backend=workspace_backend,
            sandbox_mode=sandbox_mode,
            use_docker=use_docker,
            prompt=prompt,
        )
        return AdapterResult(metadata={"workspace_authority": "contribai"})

    outcome = await OpenHandsSDKDriver(agent_factory=agent_factory).run(
        engine_request,
        execution_lease,
    )

    assert outcome.status is EngineStatus.COMPLETED
    assert captured["workspace_path"] == execution_lease.workspace.path
    assert captured["workspace_backend"] == "local"
    assert captured["sandbox_mode"] == "outer"
    assert captured["use_docker"] is False
    assert "docker" not in str(captured["workspace_path"]).lower()


@pytest.mark.asyncio
async def test_openhands_cancellation_calls_agent_interrupt(
    engine_request,
    execution_lease,
):
    started = asyncio.Event()
    interrupted = asyncio.Event()

    class Agent:
        async def run(self, **_kwargs):
            started.set()
            await asyncio.sleep(10)

        async def interrupt(self):
            interrupted.set()

    def agent_factory(**_kwargs):
        return Agent()

    task = asyncio.create_task(
        OpenHandsSDKDriver(agent_factory=agent_factory).run(
            engine_request,
            execution_lease,
        )
    )
    await started.wait()
    execution_lease.cancel_event.set()
    outcome = await asyncio.wait_for(task, timeout=1)

    assert outcome.status is EngineStatus.CANCELLED
    assert interrupted.is_set()


@pytest.mark.asyncio
async def test_openhands_without_sdk_or_factory_is_unsupported(
    engine_request,
    execution_lease,
):
    outcome = await OpenHandsSDKDriver().run(engine_request, execution_lease)

    assert outcome.status is EngineStatus.UNSUPPORTED
