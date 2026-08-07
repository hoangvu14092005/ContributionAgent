"""Native in-process engine driver backed by the control-plane workspace lease."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

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


@dataclass(frozen=True, slots=True)
class NativeExecutionResult:
    """Optional result metadata returned by a native operation."""

    status: EngineStatus = EngineStatus.COMPLETED
    exit_reason: str = "completed"
    events: tuple[ExecutionEvent, ...] = ()
    usage: EngineUsage = field(default_factory=EngineUsage)
    cost_usd: float = 0.0
    metadata: Mapping[str, object] = field(default_factory=dict)


NativeOperation = Callable[
    [EngineRequest, ExecutionLease],
    Awaitable[NativeExecutionResult | EngineOutcome | None]
    | NativeExecutionResult
    | EngineOutcome
    | None,
]


class NativeEngineDriver:
    """Run a native operation while keeping patch collection outside the driver."""

    def __init__(
        self,
        operation: NativeOperation | None = None,
        *,
        engine_version: str = "native@1",
    ) -> None:
        self._operation = operation
        self.engine_version = engine_version

    async def run(self, request: EngineRequest, execution: ExecutionLease) -> EngineOutcome:
        """Execute one operation and convert only its audit result to EngineOutcome."""
        started = time.monotonic()
        trajectory_id = f"{request.work_id}:{request.attempt_id}:native"
        try:
            execution.assert_active()
            if execution.cancelled:
                return self._outcome(
                    request,
                    EngineStatus.CANCELLED,
                    "cancelled before native execution",
                    trajectory_id,
                    started,
                )
            await execution.budget.consume_step()
            if self._operation is None:
                return self._outcome(
                    request,
                    EngineStatus.UNSUPPORTED,
                    "native operation is not configured",
                    trajectory_id,
                    started,
                )
            raw_result = self._operation(request, execution)
            result = await raw_result if inspect.isawaitable(raw_result) else raw_result
            if isinstance(result, EngineOutcome):
                return result
            if result is None:
                result = NativeExecutionResult()
            if not isinstance(result, NativeExecutionResult):
                raise TypeError("native operation must return NativeExecutionResult or None")
            if result.cost_usd:
                await execution.budget.add_cost(result.cost_usd)
            if execution.is_expired():
                raise EngineBoundaryError("execution lease has expired")
            if execution.cancelled:
                raise EngineBoundaryError("native execution was cancelled")
            return self._outcome(
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
            return self._outcome(
                request,
                EngineStatus.CANCELLED,
                "native execution cancelled",
                trajectory_id,
                started,
            )
        except (BudgetExceededError, EngineBoundaryError) as exc:
            return self._outcome(
                request,
                EngineStatus.TIMED_OUT,
                str(exc),
                trajectory_id,
                started,
            )
        except Exception as exc:  # engine failures become evidence, not control-plane crashes
            return self._outcome(
                request,
                EngineStatus.FAILED,
                f"native operation failed: {exc}",
                trajectory_id,
                started,
            )

    def _outcome(
        self,
        request: EngineRequest,
        status: EngineStatus,
        reason: str,
        trajectory_id: str,
        started: float,
        *,
        events: tuple[ExecutionEvent, ...] = (),
        usage: EngineUsage | None = None,
        cost_usd: float = 0.0,
        metadata: Mapping[str, Any] | None = None,
    ) -> EngineOutcome:
        duration = time.monotonic() - started
        usage = usage or EngineUsage()
        if usage.duration_sec == 0:
            usage = EngineUsage(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                tool_calls=usage.tool_calls,
                duration_sec=duration,
            )
        return EngineOutcome(
            status=status,
            exit_reason=reason,
            events=tuple(events),
            usage=usage,
            cost_usd=cost_usd,
            trajectory_id=trajectory_id,
            engine_version=self.engine_version,
            metadata={
                "work_id": request.work_id,
                "attempt_id": request.attempt_id,
                **(metadata or {}),
            },
        )


__all__ = ["NativeEngineDriver", "NativeExecutionResult", "NativeOperation"]
