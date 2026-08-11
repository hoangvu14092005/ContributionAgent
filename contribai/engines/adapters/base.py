"""Shared safety and result helpers for optional external engine drivers."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from contribai.engines.models import (
    EngineBoundaryError,
    EngineOutcome,
    EngineRequest,
    EngineStatus,
    EngineUsage,
    ExecutionLease,
)
from contribai.execution.budget import BudgetExceededError
from contribai.execution.trajectory import ExecutionEvent
from contribai.execution.workspaces.base import Workspace

MAX_ADAPTER_OUTPUT_CHARS = 64_000
MAX_ADAPTER_EVENT_CHARS = 4_000
MAX_ADAPTER_EVENTS = 1_000


class AdapterUnavailableError(RuntimeError):
    """Raised when an optional runtime is not installed or cannot be used."""


@dataclass(frozen=True, slots=True)
class AdapterResult:
    """Runtime-native result normalized before it becomes ``EngineOutcome``."""

    status: EngineStatus = EngineStatus.COMPLETED
    exit_reason: str = "completed"
    events: tuple[ExecutionEvent, ...] = ()
    usage: EngineUsage = field(default_factory=EngineUsage)
    cost_usd: float = 0.0
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", EngineStatus(self.status))
        object.__setattr__(self, "events", normalize_events(self.events))


def require_workspace(execution: ExecutionLease) -> Workspace:
    """Return the exact outer workspace bound to this execution lease."""
    workspace = execution.workspace
    if workspace is None:
        raise AdapterUnavailableError("external engine requires an outer workspace lease")
    path = Path(workspace.path)
    if not path.is_absolute():
        raise EngineBoundaryError("outer workspace path must be absolute")
    if getattr(workspace, "snapshot_id", None) != execution.workspace_ref:
        raise EngineBoundaryError("outer workspace snapshot does not match execution lease")
    if getattr(workspace, "attempt_id", None) != execution.attempt_id:
        raise EngineBoundaryError("outer workspace attempt does not match execution lease")
    return workspace


def require_execution_scope(request: EngineRequest, execution: ExecutionLease) -> None:
    """Bind a request to one exact work item, attempt and budget contract."""
    if request.work_id != execution.work_id:
        raise EngineBoundaryError("engine request work scope does not match execution lease")
    if request.attempt_id != execution.attempt_id:
        raise EngineBoundaryError("engine request attempt scope does not match execution lease")
    request_limits = (
        request.budget.max_steps,
        request.budget.max_cost_usd,
        request.budget.max_wall_time_sec,
        request.budget.max_tool_failures,
    )
    execution_limits = (
        execution.budget.max_steps,
        execution.budget.max_cost_usd,
        execution.budget.max_wall_time_sec,
        execution.budget.max_tool_failures,
    )
    if request_limits != execution_limits:
        raise EngineBoundaryError("engine request budget does not match execution lease")


def scoped_environment(execution: ExecutionLease, workspace: Workspace) -> dict[str, str]:
    """Build a scrubbed process environment with only a scoped model lease."""
    policy = getattr(workspace, "policy", None)
    extra = execution.scoped_model_environment()
    if policy is None:
        if extra:
            raise AdapterUnavailableError(
                "workspace does not expose a resource policy for scoped credentials"
            )
        return {}
    return policy.sanitized_environment(
        extra,
        allow_scoped_model_credentials=bool(extra),
    )


def require_model_access(execution: ExecutionLease) -> None:
    """Require either an opaque gateway handle or a scoped credential lease."""
    if execution.model_gateway is None and execution.credential_lease is None:
        raise AdapterUnavailableError("external engine requires a model gateway or scoped lease")
    if execution.credential_lease and execution.credential_lease.work_id != execution.work_id:
        raise EngineBoundaryError("credential lease work scope does not match execution")
    if execution.credential_lease and execution.credential_lease.attempt_id != execution.attempt_id:
        raise EngineBoundaryError("credential lease attempt scope does not match execution")
    if execution.credential_lease and execution.credential_lease.expires_at <= __import__(
        "datetime"
    ).datetime.now(execution.credential_lease.expires_at.tzinfo):
        raise EngineBoundaryError("credential lease has expired")


def bounded_text(value: object, *, limit: int = MAX_ADAPTER_EVENT_CHARS) -> str:
    return str(value)[:limit]


def normalize_events(values: Iterable[object]) -> tuple[ExecutionEvent, ...]:
    normalized: list[ExecutionEvent] = []
    for raw in values:
        if len(normalized) >= MAX_ADAPTER_EVENTS:
            break
        if isinstance(raw, ExecutionEvent):
            normalized.append(raw)
            continue
        if isinstance(raw, Mapping):
            event_type = raw.get("kind") or raw.get("type") or raw.get("event") or "runtime"
            data = {str(key): _bound_event_value(item) for key, item in list(raw.items())[:64]}
            normalized.append(ExecutionEvent(bounded_text(event_type, limit=200), data))
            continue
        normalized.append(ExecutionEvent("output", {"line": bounded_text(raw)}))
    return tuple(normalized)


def _bound_event_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _bound_event_value(item) for key, item in list(value.items())[:64]}
    if isinstance(value, list | tuple):
        return tuple(_bound_event_value(item) for item in value[:64])
    if isinstance(value, str):
        return bounded_text(value)
    if isinstance(value, int | float | bool) or value is None:
        return value
    return bounded_text(value)


def coerce_result(value: object) -> AdapterResult | EngineOutcome:
    if isinstance(value, EngineOutcome):
        return value
    if value is None:
        return AdapterResult()
    if isinstance(value, AdapterResult):
        return value
    if isinstance(value, Mapping):
        usage_value = value.get("usage", {})
        if isinstance(usage_value, EngineUsage):
            usage = usage_value
        elif isinstance(usage_value, Mapping):
            usage = EngineUsage(
                prompt_tokens=int(usage_value.get("prompt_tokens", 0)),
                completion_tokens=int(usage_value.get("completion_tokens", 0)),
                tool_calls=int(usage_value.get("tool_calls", 0)),
                duration_sec=float(usage_value.get("duration_sec", 0.0)),
            )
        else:
            usage = EngineUsage()
        raw_events = value.get("events", ())
        if isinstance(raw_events, (str, bytes)):
            raw_events = (
                (
                    raw_events.decode(errors="replace")
                    if isinstance(raw_events, bytes)
                    else raw_events
                ),
            )
        metadata = value.get("metadata", {})
        if not isinstance(metadata, Mapping):
            metadata = {"runtime_metadata": bounded_text(metadata)}
        return AdapterResult(
            status=EngineStatus(value.get("status", EngineStatus.COMPLETED)),
            exit_reason=bounded_text(value.get("exit_reason", "completed"), limit=2_000),
            events=normalize_events(raw_events),
            usage=usage,
            cost_usd=float(value.get("cost_usd", 0.0)),
            metadata=metadata,
        )
    raise TypeError("external engine runtime must return AdapterResult, EngineOutcome or mapping")


async def invoke_callback(
    callback: Callable[..., object],
    *,
    request: EngineRequest,
    execution: ExecutionLease,
    workspace: Workspace,
    prompt: str,
    **extra: object,
) -> object:
    values: dict[str, object] = {
        "request": request,
        "execution": execution,
        "workspace": workspace,
        "workspace_path": workspace.path,
        "prompt": prompt,
        "model_gateway": execution.model_gateway,
        "credential_lease": execution.credential_lease,
        "lease": execution.credential_lease,
        **extra,
    }
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        result = callback(request, execution)
    else:
        parameters = tuple(signature.parameters.values())
        if any(parameter.kind is parameter.VAR_KEYWORD for parameter in parameters):
            result = callback(**values)
        else:
            keyword_values = {
                parameter.name: values[parameter.name]
                for parameter in parameters
                if parameter.name in values
                and parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
            }
            missing_required = [
                parameter
                for parameter in parameters
                if parameter.default is parameter.empty
                and parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
                and parameter.name not in keyword_values
            ]
            if missing_required:
                result = callback(request, execution)
            else:
                result = callback(**keyword_values)
    if inspect.isawaitable(result):
        return await result
    return result


class ExternalEngineDriver:
    """Base implementation for optional drivers with common fail-closed rules."""

    engine_name = "external"
    engine_version = "external@unknown"

    def __init__(
        self,
        *,
        require_gateway: bool = True,
        max_events: int = MAX_ADAPTER_EVENTS,
    ) -> None:
        self.require_gateway = require_gateway
        self.max_events = max(1, max_events)

    async def run(self, request: EngineRequest, execution: ExecutionLease) -> EngineOutcome:
        started = time.monotonic()
        trajectory_id = f"{request.work_id}:{request.attempt_id}:{self.engine_name}"
        try:
            execution.assert_active()
            if execution.cancelled:
                return self.make_outcome(
                    request,
                    EngineStatus.CANCELLED,
                    "cancelled before external execution",
                    trajectory_id,
                    started,
                )
            workspace = require_workspace(execution)
            require_execution_scope(request, execution)
            if self.require_gateway:
                require_model_access(execution)
            await execution.budget.consume_step()
            raw_result = await self.execute_runtime(request, execution, workspace, started)
            result = coerce_result(raw_result)
            if isinstance(result, EngineOutcome):
                return result
            if result.cost_usd:
                await execution.budget.add_cost(result.cost_usd)
            if execution.cancelled:
                return self.make_outcome(
                    request,
                    EngineStatus.CANCELLED,
                    "external execution cancelled",
                    trajectory_id,
                    started,
                    events=result.events,
                    usage=result.usage,
                    cost_usd=result.cost_usd,
                    metadata=result.metadata,
                )
            if execution.is_expired():
                raise EngineBoundaryError("execution lease has expired")
            return self.make_outcome(
                request,
                result.status,
                result.exit_reason,
                trajectory_id,
                started,
                events=result.events,
                usage=result.usage,
                cost_usd=result.cost_usd,
                metadata=result.metadata,
            )
        except asyncio.CancelledError:
            return self.make_outcome(
                request,
                EngineStatus.CANCELLED,
                "external execution task cancelled",
                trajectory_id,
                started,
            )
        except AdapterUnavailableError as exc:
            return self.make_outcome(
                request, EngineStatus.UNSUPPORTED, str(exc), trajectory_id, started
            )
        except BudgetExceededError as exc:
            return self.make_outcome(
                request, EngineStatus.TIMED_OUT, str(exc), trajectory_id, started
            )
        except TimeoutError as exc:
            return self.make_outcome(
                request, EngineStatus.TIMED_OUT, str(exc), trajectory_id, started
            )
        except EngineBoundaryError as exc:
            return self.make_outcome(
                request,
                EngineStatus.CANCELLED if execution.cancelled else EngineStatus.TIMED_OUT,
                str(exc),
                trajectory_id,
                started,
            )
        except Exception as exc:
            return self.make_outcome(
                request,
                EngineStatus.FAILED,
                f"{self.engine_name} execution failed: {exc}",
                trajectory_id,
                started,
            )

    async def execute_runtime(
        self,
        request: EngineRequest,
        execution: ExecutionLease,
        workspace: Workspace,
        started: float,
    ) -> object:
        raise NotImplementedError

    def make_outcome(
        self,
        request: EngineRequest,
        status: EngineStatus,
        reason: str,
        trajectory_id: str,
        started: float,
        *,
        events: Iterable[ExecutionEvent] = (),
        usage: EngineUsage | None = None,
        cost_usd: float = 0.0,
        metadata: Mapping[str, object] | None = None,
    ) -> EngineOutcome:
        usage = usage or EngineUsage()
        if usage.duration_sec == 0:
            usage = EngineUsage(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                tool_calls=usage.tool_calls,
                duration_sec=max(0.0, time.monotonic() - started),
            )
        bounded_events = tuple(events)[-self.max_events :]
        return EngineOutcome(
            status=status,
            exit_reason=reason,
            events=bounded_events,
            usage=usage,
            cost_usd=cost_usd,
            trajectory_id=trajectory_id,
            engine_version=self.engine_version,
            metadata={
                "adapter": self.engine_name,
                "work_id": request.work_id,
                "attempt_id": request.attempt_id,
                **(metadata or {}),
            },
        )


async def wait_with_cancel(
    operation: Awaitable[object],
    execution: ExecutionLease,
    *,
    timeout_sec: float | None = None,
    on_cancel: Callable[[], Awaitable[object] | object] | None = None,
) -> object:
    operation_task = asyncio.create_task(operation)
    cancel_task = (
        asyncio.create_task(execution.cancel_event.wait()) if execution.cancel_event else None
    )
    try:
        tasks = {operation_task}
        if cancel_task:
            tasks.add(cancel_task)
        done, _ = await asyncio.wait(
            tasks,
            timeout=timeout_sec,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            operation_task.cancel()
            await asyncio.gather(operation_task, return_exceptions=True)
            if on_cancel:
                cleanup = on_cancel()
                if inspect.isawaitable(cleanup):
                    await cleanup
            raise TimeoutError("external engine runtime timed out")
        if cancel_task and cancel_task in done and cancel_task.result():
            operation_task.cancel()
            await asyncio.gather(operation_task, return_exceptions=True)
            if on_cancel:
                cleanup = on_cancel()
                if inspect.isawaitable(cleanup):
                    await cleanup
            raise asyncio.CancelledError
        return operation_task.result()
    finally:
        if cancel_task:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)


__all__ = [
    "MAX_ADAPTER_EVENTS",
    "MAX_ADAPTER_EVENT_CHARS",
    "MAX_ADAPTER_OUTPUT_CHARS",
    "AdapterResult",
    "AdapterUnavailableError",
    "ExternalEngineDriver",
    "bounded_text",
    "coerce_result",
    "invoke_callback",
    "normalize_events",
    "require_execution_scope",
    "require_model_access",
    "require_workspace",
    "scoped_environment",
    "wait_with_cancel",
]
