"""Minimal driver protocol separating engine execution from patch authority."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from contribai.engines.models import (
    EngineBoundaryError,
    EngineOutcome,
    EngineRequest,
    ExecutionLease,
)


@runtime_checkable
class EngineDriver(Protocol):
    """Common boundary implemented by in-process, CLI and server drivers."""

    async def run(
        self,
        request: EngineRequest,
        execution: ExecutionLease,
    ) -> EngineOutcome:
        """Run an attempt inside the supplied lease and return audit evidence."""
        ...


__all__ = ["EngineBoundaryError", "EngineDriver"]
