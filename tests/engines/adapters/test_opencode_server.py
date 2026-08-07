"""OpenCode HTTP/server adapter tests."""

from __future__ import annotations

import asyncio

import pytest

from contribai.engines.adapters.opencode_server import (
    OpenCodeServerDriver,
    translate_opencode_permissions,
)
from contribai.engines.models import EngineStatus
from contribai.publishing.capability import Capability
from contribai.publishing.policy import CapabilityPolicy, PolicyDecision, PolicyRule


class FakeOpenCodeClient:
    def __init__(self, events):
        self.events_to_emit = events
        self.calls = []
        self.aborted = asyncio.Event()

    async def create_session(self, **kwargs):
        self.calls.append(("create_session", kwargs))
        return {"id": "session-1"}

    async def prompt(self, **kwargs):
        self.calls.append(("prompt", kwargs))
        return {"status": "started"}

    async def events(self, **kwargs):
        self.calls.append(("events", kwargs))
        for event in self.events_to_emit:
            yield event

    async def abort(self, **kwargs):
        self.calls.append(("abort", kwargs))
        self.aborted.set()


@pytest.mark.asyncio
async def test_opencode_maps_session_prompt_events_and_policy(
    engine_request,
    execution_lease,
):
    client = FakeOpenCodeClient(
        [
            {"type": "step", "output": "editing"},
            {"type": "session.completed", "status": "completed"},
        ]
    )
    policy = CapabilityPolicy(
        rules=[
            PolicyRule(
                actor="opencode",
                capability=Capability.WORKSPACE_READ,
                resource="workspace",
                decision=PolicyDecision.ALLOW,
            ),
            PolicyRule(
                actor="opencode",
                capability=Capability.WORKSPACE_WRITE,
                resource="workspace",
                decision=PolicyDecision.ALLOW,
            ),
        ]
    )
    request = engine_request.__class__(
        **{
            field: getattr(engine_request, field)
            for field in engine_request.__dataclass_fields__
            if field != "capability_policy"
        },
        capability_policy=policy,
    )

    outcome = await OpenCodeServerDriver(client=client).run(request, execution_lease)

    assert outcome.status is EngineStatus.COMPLETED
    create_kwargs = client.calls[0][1]
    assert create_kwargs["workspace_path"] == execution_lease.workspace.path
    assert create_kwargs["permissions"]["read"] == "allow"
    assert create_kwargs["permissions"]["edit"] == "allow"
    assert create_kwargs["permissions"]["bash"] == "allow"
    prompt_kwargs = client.calls[1][1]
    assert prompt_kwargs["session_id"] == "session-1"
    assert "GITHUB_TOKEN" not in str(create_kwargs)


@pytest.mark.asyncio
async def test_opencode_permission_ask_returns_without_scheduler_deadlock(
    engine_request,
    execution_lease,
):
    client = FakeOpenCodeClient([{"type": "permission.asked", "permission": "bash"}])

    outcome = await OpenCodeServerDriver(client=client).run(engine_request, execution_lease)

    assert outcome.status is EngineStatus.WAITING_FOR_APPROVAL
    assert not client.aborted.is_set()


@pytest.mark.asyncio
async def test_opencode_cancel_aborts_session(
    engine_request,
    execution_lease,
):
    started = asyncio.Event()

    class HangingClient(FakeOpenCodeClient):
        async def events(self, **kwargs):
            started.set()
            await asyncio.sleep(10)
            yield {"type": "session.completed"}

    client = HangingClient([])
    task = asyncio.create_task(
        OpenCodeServerDriver(client=client).run(engine_request, execution_lease)
    )
    await started.wait()
    execution_lease.cancel_event.set()
    outcome = await asyncio.wait_for(task, timeout=1)

    assert outcome.status is EngineStatus.CANCELLED
    assert client.aborted.is_set()


def test_opencode_permission_translation_is_fail_closed():
    permissions = translate_opencode_permissions(CapabilityPolicy())

    assert permissions == {
        "read": "deny",
        "edit": "deny",
        "bash": "deny",
        "webfetch": "deny",
        "websearch": "deny",
        "external_directory": "deny",
    }
