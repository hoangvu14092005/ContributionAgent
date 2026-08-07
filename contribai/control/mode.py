"""Execution modes for contribution runs."""

from __future__ import annotations

from enum import StrEnum


class ExecutionMode(StrEnum):
    """Controls whether a run may reach GitHub write operations."""

    SHADOW = "shadow"
    REVIEW_ONLY = "review_only"
    LIVE = "live"

    @property
    def dry_run(self) -> bool:
        """Map the explicit mode to the legacy pipeline safety flag."""
        return self is not ExecutionMode.LIVE
