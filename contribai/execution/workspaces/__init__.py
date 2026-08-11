"""Workspace implementations used by isolated execution attempts."""

from contribai.execution.workspaces.base import (
    CommandResult,
    PatchCandidate,
    Workspace,
    WorkspaceDiff,
)
from contribai.execution.workspaces.docker import DockerWorkspace
from contribai.execution.workspaces.local import LocalWorkspace
from contribai.execution.workspaces.manager import WorkspaceManager

__all__ = [
    "CommandResult",
    "DockerWorkspace",
    "LocalWorkspace",
    "PatchCandidate",
    "Workspace",
    "WorkspaceDiff",
    "WorkspaceManager",
]
