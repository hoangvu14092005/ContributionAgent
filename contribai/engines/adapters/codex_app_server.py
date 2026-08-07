"""Codex app-server JSON-RPC driver with explicit approval and cancel mapping."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterable, Callable, Iterable, Mapping
from typing import Any

from contribai.engines.adapters.base import (
    AdapterResult,
    AdapterUnavailableError,
    ExternalEngineDriver,
    invoke_callback,
    normalize_events,
    scoped_environment,
    wait_with_cancel,
)
from contribai.engines.capabilities import CapabilityProbeError, EngineCapabilities, VersionPolicy
from contribai.engines.models import EngineRequest, EngineStatus, EngineUsage, ExecutionLease
from contribai.execution.workspaces.base import Workspace


class CodexAppServerDriver(ExternalEngineDriver):
    """Use app-server as transport while ContribAI owns policy and publishing."""

    engine_name = "codex-app-server"
    engine_version = "codex-app-server@optional"

    def __init__(
        self,
        *,
        transport: Any | None = None,
        transport_factory: Callable[..., object] | None = None,
        probe: Any | None = None,
        version_policy: VersionPolicy | None = None,
        protocol_version: str = "app-server-1",
        engine_version: str = "codex-app-server@optional",
        timeout_sec: float | None = None,
        require_gateway: bool = True,
    ) -> None:
        super().__init__(require_gateway=require_gateway)
        self._transport = transport
        self._transport_factory = transport_factory
        self._probe = probe
        self._version_policy = version_policy
        self.protocol_version = protocol_version
        self.engine_version = engine_version
        self.timeout_sec = timeout_sec

    async def execute_runtime(
        self,
        request: EngineRequest,
        execution: ExecutionLease,
        workspace: Workspace,
        started: float,
    ) -> object:
        snapshot = await self._probe_runtime()
        transport, cleanup = await self._resolve_transport(request, execution, workspace)
        if transport is None:
            raise AdapterUnavailableError("Codex app-server transport is not configured")
        thread_id: str | None = None
        turn_id: str | None = None
        try:
            initialized = await _rpc(
                transport,
                "initialize",
                {"protocolVersion": self.protocol_version},
            )
            protocol = _protocol_version(initialized)
            if protocol and protocol != self.protocol_version:
                raise AdapterUnavailableError(
                    "Codex app-server protocol drift: "
                    f"expected {self.protocol_version}, got {protocol}"
                )
            configured_thread = request.engine_config.get("thread_id")
            if configured_thread:
                resumed = await _rpc(
                    transport,
                    "thread/resume",
                    {"threadId": str(configured_thread), "cwd": str(workspace.path)},
                )
                thread_id = _identifier(resumed, "threadId") or str(configured_thread)
            else:
                started_thread = await _rpc(
                    transport,
                    "thread/start",
                    {
                        "cwd": str(workspace.path),
                        "approvalPolicy": "on-request",
                        "sandboxPolicy": {
                            "type": "workspace-write",
                            "workspaceRoot": str(workspace.path),
                        },
                        "environment": scoped_environment(execution, workspace),
                    },
                )
                thread_id = _identifier(started_thread, "threadId")
            if not thread_id:
                raise AdapterUnavailableError("Codex app-server did not return a thread id")
            started_turn = await _rpc(
                transport,
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": self._build_prompt(request),
                    "cwd": str(workspace.path),
                    "modelGateway": {
                        "endpoint": (
                            execution.credential_lease.endpoint
                            if execution.credential_lease
                            else None
                        ),
                        "leaseId": (
                            execution.credential_lease.lease_id
                            if execution.credential_lease
                            else None
                        ),
                    },
                },
            )
            turn_id = _identifier(started_turn, "turnId")
            events = _events_from_value(started_turn)
            stream_method = getattr(transport, "events", None) or getattr(
                transport,
                "stream_events",
                None,
            )
            if stream_method is not None:
                streamed = await wait_with_cancel(
                    _collect_transport_events(stream_method),
                    execution,
                    timeout_sec=self.timeout_sec,
                    on_cancel=lambda: _rpc(
                        transport,
                        "turn/interrupt",
                        {"threadId": thread_id, "turnId": turn_id},
                    ),
                )
                events.extend(streamed)
            status = _terminal_status(events) or EngineStatus.COMPLETED
            reason = {
                EngineStatus.COMPLETED: "Codex app-server turn completed",
                EngineStatus.WAITING_FOR_APPROVAL: "Codex approval event returned to control plane",
                EngineStatus.FAILED: "Codex app-server turn failed",
                EngineStatus.CANCELLED: "Codex app-server turn cancelled",
            }.get(status, status.value)
            return AdapterResult(
                status=status,
                exit_reason=reason,
                events=normalize_events(events),
                usage=_usage_from_events(events),
                metadata={
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "protocol_version": protocol or self.protocol_version,
                    "capability_version": snapshot.version if snapshot else None,
                },
            )
        finally:
            await _cleanup_transport(transport, cleanup)

    async def _probe_runtime(self) -> EngineCapabilities | None:
        if self._probe is None:
            return None
        try:
            snapshot = await self._probe.probe()
            if not snapshot.cancellation or not snapshot.resume or not snapshot.model_gateway:
                raise AdapterUnavailableError(
                    "Codex app-server lacks pinned cancellation, resume or model gateway capability"
                )
            if self._version_policy:
                self._version_policy.assert_accepts(snapshot)
            self.engine_version = f"codex-app-server@{snapshot.version}"
            return snapshot
        except AdapterUnavailableError:
            raise
        except (CapabilityProbeError, ValueError, TypeError) as exc:
            raise AdapterUnavailableError(
                f"Codex app-server capability probe failed: {exc}"
            ) from exc

    async def _resolve_transport(
        self,
        request: EngineRequest,
        execution: ExecutionLease,
        workspace: Workspace,
    ) -> tuple[Any | None, object | None]:
        if self._transport is not None:
            return self._transport, None
        if self._transport_factory is None:
            return None, None
        transport = await invoke_callback(
            self._transport_factory,
            request=request,
            execution=execution,
            workspace=workspace,
            workspace_path=workspace.path,
            env=scoped_environment(execution, workspace),
        )
        if inspect.isawaitable(transport):
            transport = await transport
        client = getattr(transport, "transport", None) or transport
        return client, transport

    @staticmethod
    def _build_prompt(request: EngineRequest) -> str:
        return (
            "Operate only inside the outer ContribAI workspace. "
            "Do not publish or call GitHub write APIs.\n\n"
            f"Task: {request.task.query_text}\n\n{request.context.to_prompt()}"
        )


async def _rpc(transport: Any, method: str, params: Mapping[str, object]) -> object:
    for name in ("request", "send_request", "call"):
        callback = getattr(transport, name, None)
        if callback is None:
            continue
        result = _invoke_rpc(callback, method, params)
        if inspect.isawaitable(result):
            return await result
        return result
    send = getattr(transport, "send", None)
    if send is not None:
        result = send({"jsonrpc": "2.0", "id": method, "method": method, "params": dict(params)})
        if inspect.isawaitable(result):
            return await result
        return result
    raise AdapterUnavailableError("Codex app-server transport has no JSON-RPC request method")


def _invoke_rpc(
    callback: Callable[..., object],
    method: str,
    params: Mapping[str, object],
) -> object:
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return callback(method, params)
    parameters = tuple(signature.parameters.values())
    if any(parameter.kind is parameter.VAR_KEYWORD for parameter in parameters):
        return callback(method=method, params=dict(params))
    if len(parameters) >= 2:
        return callback(method, params)
    if len(parameters) == 1:
        return callback({"method": method, "params": dict(params)})
    return callback()


async def _collect_transport_events(method: Callable[..., object]) -> list[object]:
    raw = method()
    if inspect.isawaitable(raw):
        raw = await raw
    if isinstance(raw, AsyncIterable) or hasattr(raw, "__aiter__"):
        values = []
        async for event in raw:
            values.append(event)
        return values
    if raw is None:
        return []
    if isinstance(raw, Mapping):
        return [raw]
    return list(raw) if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)) else [raw]


def _identifier(value: object, key: str) -> str | None:
    if isinstance(value, Mapping):
        candidate = value.get(key) or value.get(key.lower()) or value.get("id")
        return str(candidate) if candidate else None
    return None


def _protocol_version(value: object) -> str | None:
    if isinstance(value, Mapping):
        candidate = value.get("protocolVersion") or value.get("protocol_version")
        return str(candidate) if candidate else None
    return None


def _events_from_value(value: object) -> list[object]:
    if isinstance(value, Mapping):
        raw = value.get("events", ())
        return list(raw) if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)) else []
    return []


def _terminal_status(events: Iterable[object]) -> EngineStatus | None:
    result: EngineStatus | None = None
    for event in events:
        if not isinstance(event, Mapping):
            continue
        raw = str(event.get("type") or event.get("status") or "").lower()
        if "approval" in raw or "permission" in raw:
            result = EngineStatus.WAITING_FOR_APPROVAL
        elif raw in {"turn/completed", "turn.completed", "completed", "done"}:
            result = EngineStatus.COMPLETED
        elif raw in {"turn/failed", "turn.failed", "error", "failed"}:
            result = EngineStatus.FAILED
        elif raw in {"turn/cancelled", "turn.cancelled", "cancelled", "aborted"}:
            result = EngineStatus.CANCELLED
    return result


def _usage_from_events(events: Iterable[object]) -> EngineUsage:
    prompt_tokens = completion_tokens = tool_calls = 0
    for event in events:
        if not isinstance(event, Mapping):
            continue
        usage = event.get("usage")
        if isinstance(usage, Mapping):
            prompt_tokens += int(usage.get("prompt_tokens", 0))
            completion_tokens += int(usage.get("completion_tokens", 0))
            tool_calls += int(usage.get("tool_calls", 0))
    return EngineUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        tool_calls=tool_calls,
    )


async def _cleanup_transport(transport: Any, server: object | None) -> None:
    target = server if server is not None else transport
    for name in ("close", "stop", "shutdown"):
        method = getattr(target, name, None)
        if method is None:
            continue
        result = method()
        if inspect.isawaitable(result):
            await result
        return


__all__ = ["CodexAppServerDriver"]
