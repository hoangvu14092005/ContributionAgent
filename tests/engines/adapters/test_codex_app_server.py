"""Codex app-server JSON-RPC adapter tests."""

from __future__ import annotations

import asyncio

import pytest

from contribai.engines.adapters.codex_app_server import CodexAppServerDriver
from contribai.engines.capabilities import EngineCapabilities
from contribai.engines.models import EngineStatus
from contribai.engines.probes import StaticEngineProbe


class FakeTransport:
    def __init__(self, events):
        self.events_to_emit = events
        self.calls = []
        self.interrupted = asyncio.Event()
        self.closed = False

    async def request(self, method, params):
        self.calls.append((method, params))
        if method == "initialize":
            return {"protocolVersion": "app-server-1"}
        if method == "thread/start":
            return {"threadId": "thread-1"}
        if method == "thread/resume":
            return {"threadId": "thread-1"}
        if method == "turn/start":
            return {"turnId": "turn-1"}
        if method == "turn/interrupt":
            self.interrupted.set()
            return {"ok": True}
        return {"ok": True}

    async def events(self):
        for event in self.events_to_emit:
            yield event

    async def close(self):
        self.closed = True


def _probe(*, resume=True):
    return StaticEngineProbe(
        EngineCapabilities(
            engine="codex-app-server",
            version="2.0.0",
            interface_version="app-server-1",
            streaming=True,
            resume=resume,
            cancellation=True,
            diff_events=True,
            approvals=True,
            model_gateway=True,
            sandbox=True,
        )
    )


@pytest.mark.asyncio
async def test_codex_app_server_maps_thread_turn_events_without_publish_authority(
    engine_request,
    execution_lease,
):
    transport = FakeTransport(
        [
            {"type": "item/command_execution", "command": "pytest"},
            {"type": "item/file_change", "path": "src/service.py"},
            {"type": "turn/completed"},
        ]
    )

    outcome = await CodexAppServerDriver(transport=transport, probe=_probe()).run(
        engine_request,
        execution_lease,
    )

    assert outcome.status is EngineStatus.COMPLETED
    assert [method for method, _ in transport.calls[:4]] == [
        "initialize",
        "thread/start",
        "turn/start",
    ]
    turn_params = transport.calls[2][1]
    assert turn_params["threadId"] == "thread-1"
    assert "github_client" not in str(turn_params).lower()
    assert "GITHUB_TOKEN" not in str(turn_params)
    assert "patch" not in outcome.metadata
    assert transport.closed is True


@pytest.mark.asyncio
async def test_codex_app_server_approval_event_is_returned_to_control_plane(
    engine_request,
    execution_lease,
):
    transport = FakeTransport([{"type": "approval/requested", "action": "command"}])

    outcome = await CodexAppServerDriver(transport=transport, probe=_probe()).run(
        engine_request,
        execution_lease,
    )

    assert outcome.status is EngineStatus.WAITING_FOR_APPROVAL
    assert all(method != "approval/accept" for method, _ in transport.calls)


@pytest.mark.asyncio
async def test_codex_app_server_interrupts_turn_on_cancel(
    engine_request,
    execution_lease,
):
    started = asyncio.Event()

    class HangingTransport(FakeTransport):
        async def events(self):
            started.set()
            await asyncio.sleep(10)
            yield {"type": "turn/completed"}

    transport = HangingTransport([])
    task = asyncio.create_task(
        CodexAppServerDriver(transport=transport, probe=_probe()).run(
            engine_request,
            execution_lease,
        )
    )
    await started.wait()
    execution_lease.cancel_event.set()
    outcome = await asyncio.wait_for(task, timeout=1)

    assert outcome.status is EngineStatus.CANCELLED
    assert transport.interrupted.is_set()


@pytest.mark.asyncio
async def test_codex_app_server_capability_drift_is_unsupported(
    engine_request,
    execution_lease,
):
    outcome = await CodexAppServerDriver(
        transport=FakeTransport([]),
        probe=_probe(resume=False),
    ).run(engine_request, execution_lease)

    assert outcome.status is EngineStatus.UNSUPPORTED
