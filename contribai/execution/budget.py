"""Race-safe execution budgets shared by engines and control-plane steps."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

from contribai.domain.work_item import BudgetSnapshot

T = TypeVar("T")


class BudgetExceededError(RuntimeError):
    """Raised when an execution cannot consume any more budget."""


@dataclass
class ExecutionBudget:
    """Mutable, lock-protected counters for one execution attempt."""

    max_steps: int
    max_cost_usd: float
    max_wall_time_sec: float
    max_tool_failures: int
    clock: Callable[[], float] = field(default=time.monotonic, repr=False, compare=False)
    _started_at: float = field(init=False, repr=False)
    _steps_used: int = field(default=0, init=False)
    _cost_usd: float = field(default=0.0, init=False)
    _tool_failures: int = field(default=0, init=False)
    _exhausted_reason: str | None = field(default=None, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.max_steps < 0 or self.max_cost_usd < 0 or self.max_wall_time_sec <= 0:
            raise ValueError("ExecutionBudget limits must be non-negative and wall time positive")
        if self.max_tool_failures < 0:
            raise ValueError("max_tool_failures must be non-negative")
        self._started_at = self.clock()

    @property
    def steps_used(self) -> int:
        return self._steps_used

    @property
    def cost_usd(self) -> float:
        return self._cost_usd

    @property
    def tool_failures(self) -> int:
        return self._tool_failures

    @property
    def exhausted(self) -> bool:
        return self._exhausted_reason is not None

    @property
    def exhausted_reason(self) -> str | None:
        return self._exhausted_reason

    def _check_time(self) -> None:
        if self.clock() - self._started_at >= self.max_wall_time_sec:
            self._exhaust("wall time")

    def _exhaust(self, reason: str) -> None:
        if self._exhausted_reason is None:
            self._exhausted_reason = reason
        raise BudgetExceededError(f"Execution budget exhausted: {self._exhausted_reason}")

    def _mark_limit_if_reached(self) -> None:
        """Make a consumed limit sticky without rejecting the boundary operation."""
        if self._exhausted_reason is not None:
            return
        if self._steps_used > 0 and self._steps_used >= self.max_steps:
            self._exhausted_reason = "steps"
        elif self._cost_usd > 0 and self._cost_usd >= self.max_cost_usd:
            self._exhausted_reason = "cost"
        elif self._tool_failures > 0 and self._tool_failures >= self.max_tool_failures:
            self._exhausted_reason = "tool failures"

    def _raise_if_exhausted(self) -> None:
        self._check_time()
        self._mark_limit_if_reached()
        if self._exhausted_reason:
            raise BudgetExceededError(f"Execution budget exhausted: {self._exhausted_reason}")

    async def consume_step(self, count: int = 1) -> None:
        """Consume steps atomically; exact boundary is allowed."""
        if count <= 0:
            raise ValueError("step count must be positive")
        async with self._lock:
            self._raise_if_exhausted()
            if self._steps_used + count > self.max_steps:
                self._exhaust("steps")
            self._steps_used += count
            self._mark_limit_if_reached()

    async def add_cost(self, amount_usd: float) -> None:
        """Consume model/tool cost atomically."""
        if amount_usd < 0:
            raise ValueError("cost must be non-negative")
        async with self._lock:
            self._raise_if_exhausted()
            if self._cost_usd + amount_usd > self.max_cost_usd:
                self._exhaust("cost")
            self._cost_usd += amount_usd
            self._mark_limit_if_reached()

    async def record_tool_failure(self) -> None:
        """Record one tool failure and exhaust when the cap is crossed."""
        async with self._lock:
            self._raise_if_exhausted()
            if self._tool_failures + 1 > self.max_tool_failures:
                self._exhaust("tool failures")
            self._tool_failures += 1
            self._mark_limit_if_reached()

    async def ensure_publish_allowed(self) -> None:
        """Fail closed before a publish callback when any limit is exhausted."""
        async with self._lock:
            self._raise_if_exhausted()

    async def run_if_allowed(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run an async operation only after the budget gate passes."""
        await self.ensure_publish_allowed()
        result = operation()
        if not inspect.isawaitable(result):
            raise TypeError("budget-gated operation must return an awaitable")
        return await result

    def snapshot(self) -> BudgetSnapshot:
        """Export a JSON-compatible immutable snapshot for WorkItem storage."""
        return BudgetSnapshot.from_mapping(
            {
                "max_steps": self.max_steps,
                "max_cost_usd": self.max_cost_usd,
                "max_wall_time_sec": self.max_wall_time_sec,
                "max_tool_failures": self.max_tool_failures,
                "steps_used": self.steps_used,
                "cost_usd": self.cost_usd,
                "tool_failures": self.tool_failures,
                "exhausted_reason": self._exhausted_reason,
            }
        )

    @classmethod
    def from_snapshot(
        cls,
        snapshot: BudgetSnapshot,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> ExecutionBudget:
        values = snapshot.to_mapping()
        budget = cls(
            max_steps=int(values.get("max_steps", 0)),
            max_cost_usd=float(values.get("max_cost_usd", 0.0)),
            max_wall_time_sec=float(values.get("max_wall_time_sec", 1.0)),
            max_tool_failures=int(values.get("max_tool_failures", 0)),
            clock=clock,
        )
        budget._steps_used = int(values.get("steps_used", 0))
        budget._cost_usd = float(values.get("cost_usd", 0.0))
        budget._tool_failures = int(values.get("tool_failures", 0))
        reason = values.get("exhausted_reason")
        budget._exhausted_reason = str(reason) if reason else None
        return budget
