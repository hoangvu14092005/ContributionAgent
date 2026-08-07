"""Codex ``exec`` CLI adapter with bounded JSONL and process controls."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import shlex
import signal
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from contribai.engines.adapters.base import (
    MAX_ADAPTER_OUTPUT_CHARS,
    AdapterResult,
    AdapterUnavailableError,
    ExternalEngineDriver,
    bounded_text,
    invoke_callback,
    normalize_events,
    scoped_environment,
    wait_with_cancel,
)
from contribai.engines.capabilities import CapabilityProbeError, EngineCapabilities, VersionPolicy
from contribai.engines.models import EngineRequest, EngineStatus, EngineUsage, ExecutionLease
from contribai.execution.workspaces.base import Workspace
from contribai.execution.workspaces.docker import DockerWorkspace

_POLICY_FLAG_FRAGMENTS = ("bypass", "danger", "sandbox", "approval", "permission")
_GATEWAY_ENV_PREFIX = "CONTRIBAI_MODEL_GATEWAY_"


class CodexExecDriver(ExternalEngineDriver):
    """Run Codex non-interactively; diff authority remains PatchCollector."""

    engine_name = "codex-exec"
    engine_version = "codex@optional"

    def __init__(
        self,
        *,
        executable: str = "codex",
        process_factory: Callable[..., object] | None = None,
        probe: Any | None = None,
        version_policy: VersionPolicy | None = None,
        extra_args: tuple[str, ...] = (),
        engine_version: str = "codex@optional",
        timeout_sec: float | None = None,
        require_gateway: bool = True,
    ) -> None:
        super().__init__(require_gateway=require_gateway)
        self.executable = executable
        self._process_factory = process_factory
        self._probe = probe
        self._version_policy = version_policy
        self.extra_args = tuple(extra_args)
        self.engine_version = engine_version
        self.timeout_sec = timeout_sec
        for arg in self.extra_args:
            normalized = arg.lower()
            if any(fragment in normalized for fragment in _POLICY_FLAG_FRAGMENTS):
                raise ValueError("Codex adapter refuses flags that can alter approval/sandbox policy")

    async def execute_runtime(
        self,
        request: EngineRequest,
        execution: ExecutionLease,
        workspace: Workspace,
        started: float,
    ) -> object:
        snapshot = await self._probe_runtime()
        argv = self.build_command(request)
        environment = scoped_environment(execution, workspace)
        process = await self._start_process(
            request,
            execution,
            workspace,
            argv=argv,
            environment=environment,
        )
        if process is None:
            raise AdapterUnavailableError("Codex executable is unavailable")
        try:
            return await wait_with_cancel(
                self._consume_process(process),
                execution,
                timeout_sec=self.timeout_sec,
                on_cancel=lambda: _terminate_process(process),
            )
        except TimeoutError:
            await _terminate_process(process)
            return AdapterResult(
                status=EngineStatus.TIMED_OUT,
                exit_reason="Codex exec exceeded its execution timeout",
                metadata={"argv": tuple(argv), "version": snapshot.version if snapshot else None},
            )

    def build_command(self, request: EngineRequest) -> tuple[str, ...]:
        """Build a conservative command without approval/sandbox bypass flags."""
        return (
            self.executable,
            "exec",
            "--json",
            *self.extra_args,
            request.task.query_text,
        )

    async def _probe_runtime(self) -> EngineCapabilities | None:
        if self._probe is None:
            return None
        try:
            snapshot = await self._probe.probe()
            if self._version_policy:
                self._version_policy.assert_accepts(snapshot)
            self.engine_version = f"codex@{snapshot.version}"
            return snapshot
        except (CapabilityProbeError, ValueError, TypeError) as exc:
            raise AdapterUnavailableError(f"Codex capability probe failed: {exc}") from exc

    async def _start_process(
        self,
        request: EngineRequest,
        execution: ExecutionLease,
        workspace: Workspace,
        *,
        argv: tuple[str, ...],
        environment: dict[str, str],
    ) -> object:
        if self._process_factory is not None:
            return await invoke_callback(
                self._process_factory,
                request=request,
                execution=execution,
                workspace=workspace,
                prompt=request.task.query_text,
                argv=argv,
                cwd=workspace.path,
                env=environment,
            )

        # Real CLI processes must stay inside the outer Docker security boundary.
        # Running with cwd=workspace.path on the host would bypass WorkspaceManager.
        if not isinstance(workspace, DockerWorkspace):
            raise AdapterUnavailableError(
                "Codex exec requires a DockerWorkspace for real process execution"
            )
        if not workspace.docker_available:
            raise AdapterUnavailableError("Docker is unavailable for Codex exec")

        docker_argv = workspace.build_docker_command(
            image=workspace.image,
            workspace_path=workspace.path,
            command=shlex.join(argv),
            policy=workspace.policy,
            container_uid=workspace.container_uid,
            container_gid=workspace.container_gid,
        )
        # Docker copies only the short-lived model-gateway lease variables into
        # the container. Provider keys and arbitrary host variables never cross.
        image_index = len(docker_argv) - 4
        gateway_flags: list[str] = []
        for key in sorted(environment):
            if key.startswith(_GATEWAY_ENV_PREFIX):
                gateway_flags.extend(("--env", key))
        docker_argv[image_index:image_index] = gateway_flags

        try:
            return await asyncio.create_subprocess_exec(
                *docker_argv,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise AdapterUnavailableError(f"unable to start sandboxed Codex executable: {exc}") from exc

    async def _consume_process(self, process: Any) -> AdapterResult:
        if hasattr(process, "stdout") and hasattr(process.stdout, "readline"):
            stdout_task = asyncio.create_task(_read_stream(process.stdout))
            stderr_task = asyncio.create_task(_read_stream(getattr(process, "stderr", None)))
            wait_task = asyncio.create_task(process.wait()) if hasattr(process, "wait") else None
            try:
                stdout_lines, stderr_text = await asyncio.gather(stdout_task, stderr_task)
                returncode = await wait_task if wait_task else getattr(process, "returncode", 0)
            finally:
                for task in (stdout_task, stderr_task, wait_task):
                    if task and not task.done():
                        task.cancel()
            return self._result_from_output(stdout_lines, stderr_text, returncode)
        communicate = getattr(process, "communicate", None)
        if communicate is None:
            raise AdapterUnavailableError("Codex process has no bounded output interface")
        stdout, stderr = await communicate()
        output = stdout.decode(errors="replace") if isinstance(stdout, bytes) else str(stdout)
        error = stderr.decode(errors="replace") if isinstance(stderr, bytes) else str(stderr)
        return self._result_from_output(
            output.splitlines()[:1000],
            error,
            getattr(process, "returncode", 0),
        )

    @staticmethod
    def _result_from_output(
        lines: Iterable[object],
        stderr: str,
        returncode: int | None,
    ) -> AdapterResult:
        events: list[object] = []
        prompt_tokens = completion_tokens = tool_calls = 0
        cost = 0.0
        status: EngineStatus | None = None
        for raw_line in lines:
            line = bounded_text(raw_line, limit=MAX_ADAPTER_OUTPUT_CHARS)
            try:
                value = json.loads(line)
            except (TypeError, ValueError):
                events.append({"type": "stdout", "line": line})
                continue
            if not isinstance(value, Mapping):
                events.append({"type": "stdout", "line": bounded_text(value)})
                continue
            events.append(value)
            raw_type = str(value.get("type") or value.get("status") or "").lower()
            if (
                "approval" in raw_type
                or "permission" in raw_type
                or raw_type == "waiting_for_approval"
            ):
                status = EngineStatus.WAITING_FOR_APPROVAL
            elif raw_type in {"turn.completed", "task.completed", "completed", "done"}:
                status = EngineStatus.COMPLETED
            elif raw_type in {"turn.cancelled", "cancelled", "canceled", "aborted"}:
                status = EngineStatus.CANCELLED
            elif raw_type in {"error", "failed", "turn.failed"}:
                status = EngineStatus.FAILED
            usage = value.get("usage")
            if isinstance(usage, Mapping):
                prompt_tokens += int(usage.get("prompt_tokens", 0))
                completion_tokens += int(usage.get("completion_tokens", 0))
                tool_calls += int(usage.get("tool_calls", 0))
                cost += float(usage.get("cost_usd", value.get("cost_usd", 0.0)) or 0.0)
            if not isinstance(usage, Mapping):
                cost += float(value.get("cost_usd", 0.0) or 0.0)
        if stderr:
            events.append(
                {
                    "type": "stderr",
                    "output": bounded_text(stderr, limit=MAX_ADAPTER_OUTPUT_CHARS),
                }
            )
        if status is None:
            status = EngineStatus.COMPLETED if returncode == 0 else EngineStatus.FAILED
        return AdapterResult(
            status=status,
            exit_reason=(
                "Codex exec completed" if status is EngineStatus.COMPLETED else status.value
            ),
            events=normalize_events(events),
            usage=EngineUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                tool_calls=tool_calls,
            ),
            cost_usd=cost,
            metadata={"returncode": returncode},
        )


async def _read_stream(stream: Any) -> list[str]:
    if stream is None:
        return []
    lines: list[str] = []
    total = 0
    while total < MAX_ADAPTER_OUTPUT_CHARS:
        line = await stream.readline()
        if not line:
            break
        decoded = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
        remaining = MAX_ADAPTER_OUTPUT_CHARS - total
        lines.append(decoded[:remaining])
        total += len(decoded)
    return lines


async def _terminate_process(process: Any) -> None:
    if getattr(process, "returncode", None) is None:
        if os.name == "posix" and getattr(process, "pid", None):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        elif hasattr(process, "terminate"):
            process.terminate()
        elif hasattr(process, "kill"):
            process.kill()
    wait = getattr(process, "wait", None)
    if wait is not None:
        result = wait()
        if inspect.isawaitable(result):
            await result


__all__ = ["CodexExecDriver"]
