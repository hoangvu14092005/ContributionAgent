"""Collect workspace diffs into control-plane-owned patch candidates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from contribai.engines.candidates import PatchCandidate
from contribai.engines.models import EngineOutcome
from contribai.execution.workspaces.base import Workspace, WorkspaceDiff


class PatchCollectionError(RuntimeError):
    """Raised when a workspace diff cannot be trusted as a candidate."""


@dataclass(frozen=True, slots=True)
class BeforeSnapshot:
    """Clean workspace state captured before an engine starts."""

    base_sha: str
    snapshot_id: str
    attempt_id: str
    tracked_files: tuple[str, ...]


class PatchCollector:
    """Own the only conversion from workspace diff evidence to PatchCandidate."""

    async def capture_before(self, workspace: Workspace) -> BeforeSnapshot:
        """Capture and require a clean attempt before engine execution."""
        changed_files = tuple(sorted(set(await workspace.changed_files())))
        if changed_files:
            raise PatchCollectionError("workspace is not clean before engine execution")
        return BeforeSnapshot(
            base_sha=workspace.base_sha,
            snapshot_id=workspace.snapshot_id,
            attempt_id=workspace.attempt_id,
            tracked_files=changed_files,
        )

    async def collect(
        self,
        workspace: Workspace,
        before: BeforeSnapshot,
        outcome: EngineOutcome,
    ) -> PatchCandidate:
        """Validate terminal engine evidence and collect the diff from the base SHA."""
        if not outcome.terminal:
            raise PatchCollectionError("cannot collect a patch before engine outcome is terminal")
        if workspace.base_sha != before.base_sha:
            raise PatchCollectionError("workspace base SHA changed during engine execution")
        if workspace.snapshot_id != before.snapshot_id or workspace.attempt_id != before.attempt_id:
            raise PatchCollectionError("workspace snapshot identity changed during execution")

        diff = await workspace.diff_from_base()
        self._validate_diff(diff, before)
        expected_hash = PatchCandidate.compute_hash(
            base_sha=diff.base_sha,
            patch=diff.patch,
            changed_files=diff.changed_files,
            added_files=diff.added_files,
            deleted_files=diff.deleted_files,
        )
        if diff.diff_hash and diff.diff_hash != expected_hash:
            raise PatchCollectionError("workspace diff hash is not deterministic")
        return PatchCandidate.from_patch(
            attempt_id=before.attempt_id,
            base_sha=before.base_sha,
            patch=diff.patch,
            changed_files=diff.changed_files,
            added_files=diff.added_files,
            deleted_files=diff.deleted_files,
            binary_files=diff.binary_files,
            unreadable_files=diff.unreadable_files,
        )

    @staticmethod
    def _validate_diff(diff: WorkspaceDiff, before: BeforeSnapshot) -> None:
        if diff.status != "VERIFIED":
            raise PatchCollectionError(f"workspace diff is {diff.status}, not VERIFIED")
        if diff.base_sha != before.base_sha:
            raise PatchCollectionError("workspace diff base SHA does not match before snapshot")
        if diff.snapshot_id != before.snapshot_id or diff.attempt_id != before.attempt_id:
            raise PatchCollectionError("workspace diff snapshot identity does not match")
        if not diff.patch or not diff.changed_files:
            raise PatchCollectionError("workspace produced an empty patch")
        if diff.binary_files or diff.unreadable_files:
            raise PatchCollectionError("workspace diff contains binary or unreadable files")
        paths = (*diff.changed_files, *diff.added_files, *diff.deleted_files)
        for path in paths:
            normalized = path.replace("\\", "/")
            pure_path = PurePosixPath(normalized)
            if pure_path.is_absolute() or ".." in pure_path.parts:
                raise PatchCollectionError(f"patch path is outside workspace: {path}")


__all__ = ["BeforeSnapshot", "PatchCollectionError", "PatchCollector"]
