"""Verification command runner boundary."""

from __future__ import annotations

from typing import Protocol

from contribai.execution.workspaces.base import CommandResult, Workspace


class VerificationRunner(Protocol):
    """Injectable command execution boundary for verification tests and runtimes."""

    async def run(self, workspace: Workspace, command: str, timeout_sec: float) -> CommandResult:
        """Run one bounded validation command in the candidate workspace."""
        ...


class WorkspaceVerificationRunner:
    """Production runner delegating to the isolated Workspace contract."""

    async def run(self, workspace: Workspace, command: str, timeout_sec: float) -> CommandResult:
        return await workspace.execute(command, timeout_sec)


__all__ = ["VerificationRunner", "WorkspaceVerificationRunner"]
