"""Bounded immutable execution trajectory events."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    """One immutable event emitted by an engine or control-plane step."""

    kind: str
    data: Mapping[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("ExecutionEvent kind must not be empty")
        frozen = _freeze(self.data)
        if not isinstance(frozen, MappingProxyType):
            raise TypeError("ExecutionEvent data must be a mapping")
        object.__setattr__(self, "data", frozen)


class AgentTrajectory:
    """Lock-protected, bounded event log with detached snapshots."""

    def __init__(self, *, max_events: int = 1000):
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        self._events: deque[ExecutionEvent] = deque(maxlen=max_events)
        self._lock = asyncio.Lock()

    async def append(self, event: ExecutionEvent) -> None:
        if not isinstance(event, ExecutionEvent):
            raise TypeError("trajectory accepts ExecutionEvent values")
        async with self._lock:
            self._events.append(event)

    def snapshot(self) -> list[ExecutionEvent]:
        """Return immutable event objects in insertion order."""
        return list(self._events)
