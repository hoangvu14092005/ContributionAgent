"""Codex exec CLI adapter tests."""

from __future__ import annotations

import asyncio
import json

import pytest

from contribai.engines.adapters.codex_exec import CodexExecDriver
from contribai.engines.capabilities import EngineCapabilities
from contribai.engines.models import EngineStatus
from contribai.engines.probes import StaticEngineProbe


class FakeStream:
    def __init__(self, lines):
        self._lines = [line.encode() for line in lines]

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""


class FakeProcess:
    def __init__(self, lines, *, returncode=0):
        self.stdout = FakeStream(lines)
        self.stderr = FakeStream([])
        self.returncode = returncode
        self.terminated = False

    async def wait(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.terminated = True
        self.returncode = -9


def _probe() -> StaticEngineProbe:
    return StaticEngineProbe(
        EngineCapabilities(
            engine="codex",
            version="1.2.3",
            interface_version="exec-jsonl-1",
            streaming=True,
            cancellation=True,
            model_gateway=True,
            sandbox=True,
        )
    )


@pytest.mark.asyncio
async def test_codex_exec_runs_jsonl_in_outer_workspace_and_bounds_output(
    engine_request,
    execution_lease,
):
    process = FakeProcess(
        [
            json.dumps({"type": "turn.started"}),
            json.dumps({"type": "file_change", "path": "src/service.py"}),
            json.dumps({"type": "turn.completed", "usage": {"tool_calls": 2}}),
        ]
    )
    captured = {}

    async def process_factory(*, argv, cwd, env):
        captured.update(argv=argv, cwd=cwd, env=env)
        return process

    outcome = await CodexExecDriver(
        process_factory=process_factory,
        probe=_probe(),
    ).run(engine_request, execution_lease)

    assert outcome.status is EngineStatus.COMPLETED
    assert captured["cwd"] == execution_lease.workspace.path
    assert captured["argv"][:3] == ("codex", "exec", "--json")
    assert "GITHUB_TOKEN" not in captured["env"]
    assert "SSH_AUTH_SOCK" not in captured["env"]
    assert "CONTRIBAI_MODEL_GATEWAY_TOKEN" in captured["env"]
    assert "patch" not in outcome.metadata
    assert outcome.engine_version == "codex@1.2.3"


@pytest.mark.asyncio
async def test_codex_exec_nonzero_process_is_failed_and_never_publishes(
    engine_request,
    execution_lease,
):
    process = FakeProcess([json.dumps({"type": "error", "message": "failed"})], returncode=2)

    async def process_factory(**_kwargs):
        return process

    outcome = await CodexExecDriver(process_factory=process_factory, probe=_probe()).run(
        engine_request,
        execution_lease,
    )

    assert outcome.status is EngineStatus.FAILED
    assert "publish" not in str(outcome.metadata).lower()


@pytest.mark.asyncio
async def test_codex_exec_cancel_terminates_process(
    engine_request,
    execution_lease,
):
    class HangingProcess(FakeProcess):
        def __init__(self):
            super().__init__([])
            self.returncode = None

        async def wait(self):
            await asyncio.sleep(10)
            return self.returncode

        async def communicate(self):
            await asyncio.sleep(10)
            return b"", b""

    process = HangingProcess()

    async def process_factory(**_kwargs):
        return process

    task = asyncio.create_task(
        CodexExecDriver(process_factory=process_factory, probe=_probe()).run(
            engine_request,
            execution_lease,
        )
    )
    await asyncio.sleep(0.01)
    execution_lease.cancel_event.set()
    outcome = await asyncio.wait_for(task, timeout=1)

    assert outcome.status is EngineStatus.CANCELLED
    assert process.terminated is True


def test_codex_exec_command_rejects_dangerous_bypass_flags():
    with pytest.raises(ValueError, match="bypass"):
        CodexExecDriver(extra_args=("--dangerously-bypass-approvals-and-sandbox",))
