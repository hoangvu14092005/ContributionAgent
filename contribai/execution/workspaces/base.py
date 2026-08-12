"""Stable contracts and data objects for execution workspaces."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

WorkspaceStatus = Literal["VERIFIED", "UNVERIFIED", "INCONCLUSIVE"]


def compute_diff_hash(
    *,
    base_sha: str,
    patch: str,
    changed_files: Iterable[str],
    added_files: Iterable[str] = (),
    deleted_files: Iterable[str] = (),
) -> str:
    """Compute the canonical hash shared by workspace and patch collection."""
    payload = "\x00".join(
        (
            base_sha,
            patch,
            *sorted(set(changed_files)),
            "\x01",
            *sorted(set(added_files)),
            "\x02",
            *sorted(set(deleted_files)),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Bounded result of a workspace command."""

    command: str
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_sec: float = 0.0
    timed_out: bool = False
    status: WorkspaceStatus = "VERIFIED"

    @property
    def success(self) -> bool:
        """Whether the command completed and returned zero."""
        return self.status == "VERIFIED" and not self.timed_out and self.returncode == 0


@dataclass(frozen=True, slots=True)
class WorkspaceDiff:
    """Diff evidence collected against an immutable base SHA."""

    base_sha: str
    snapshot_id: str
    attempt_id: str
    patch: str
    changed_files: tuple[str, ...]
    added_files: tuple[str, ...] = ()
    deleted_files: tuple[str, ...] = ()
    status: WorkspaceStatus = "VERIFIED"
    diff_hash: str = ""
    binary_files: tuple[str, ...] = ()
    unreadable_files: tuple[str, ...] = ()

    @property
    def publishable(self) -> bool:
        """Only verified diff evidence may proceed to later control-plane gates."""
        return self.status == "VERIFIED"


@dataclass(frozen=True, slots=True)
class PatchCandidate:
    """A file edit or unified patch that can be applied to a workspace."""

    path: str = ""
    content: str | None = None
    patch: str | None = None
    is_new_file: bool = False
    is_deleted: bool = False

    def __post_init__(self) -> None:
        if self.content is None and self.patch is None and not self.is_deleted:
            raise ValueError("PatchCandidate requires content or patch")
        if self.content is not None and self.patch is not None:
            raise ValueError("PatchCandidate cannot contain both content and patch")
        if self.is_deleted and self.content is not None:
            raise ValueError("deleted PatchCandidate cannot contain content")


class WorkspaceError(RuntimeError):
    """Base error for workspace lifecycle and file operations."""


class WorkspaceUnavailableError(WorkspaceError):
    """Raised when the requested workspace backend is unavailable."""


@runtime_checkable
class Workspace(Protocol):
    """Execution workspace contract shared by native and external engines."""

    base_sha: str
    snapshot_id: str
    attempt_id: str
    path: Path

    async def execute(self, command: str, timeout_sec: float) -> CommandResult: ...

    async def read_file(self, path: str) -> str: ...

    async def write_file(self, path: str, content: str) -> None: ...

    async def apply_patch(self, patch: PatchCandidate) -> None: ...

    async def diff_from_base(self) -> WorkspaceDiff: ...

    async def changed_files(self) -> tuple[str, ...]: ...

    async def reset(self) -> None: ...
