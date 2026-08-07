"""Development workspace backed by a detached Git worktree."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import time
from pathlib import Path

from contribai.execution.resource_policy import ResourcePolicy
from contribai.execution.workspaces.base import (
    CommandResult,
    PatchCandidate,
    WorkspaceDiff,
    WorkspaceError,
    compute_diff_hash,
)

_MAX_OUTPUT_CHARS = 64_000


class LocalWorkspace:
    """A clean, detached Git worktree for local development and tests.

    This backend is intentionally explicit: it is useful for development, but
    callers that require a production sandbox should request ``backend="docker"``
    from :class:`WorkspaceManager` instead of silently falling back here.
    """

    def __init__(
        self,
        repository: Path,
        *,
        base_sha: str,
        snapshot_id: str,
        attempt_id: str,
        workspace_path: Path,
        policy: ResourcePolicy,
    ) -> None:
        self._repository = Path(repository).resolve()
        self._path = Path(workspace_path).resolve()
        self._base_sha = base_sha
        self._snapshot_id = snapshot_id
        self._attempt_id = attempt_id
        self._policy = policy

    @classmethod
    async def create(
        cls,
        repository: Path,
        *,
        base_sha: str,
        snapshot_id: str,
        attempt_id: str,
        workspace_path: Path,
        policy: ResourcePolicy,
    ) -> LocalWorkspace:
        """Create a detached worktree at exactly ``base_sha``."""
        repository = Path(repository).resolve()
        workspace_path = Path(workspace_path).resolve()
        workspace_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_sha = await cls._resolve_sha(repository, base_sha)
        returncode, _, stderr = await cls._run_git(
            repository,
            "worktree",
            "add",
            "--detach",
            str(workspace_path),
            resolved_sha,
        )
        if returncode != 0:
            raise WorkspaceError(stderr.strip() or "git worktree add failed")
        return cls(
            repository,
            base_sha=resolved_sha,
            snapshot_id=snapshot_id,
            attempt_id=attempt_id,
            workspace_path=workspace_path,
            policy=policy,
        )

    @staticmethod
    async def _run_git(cwd: Path, *args: str, input_text: str | None = None):
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE if input_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(
            input_text.encode() if input_text is not None else None
        )
        return process.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")

    @classmethod
    async def _resolve_sha(cls, repository: Path, base_sha: str) -> str:
        if not base_sha.strip():
            raise ValueError("base_sha must not be empty")
        returncode, stdout, stderr = await cls._run_git(
            repository,
            "rev-parse",
            "--verify",
            f"{base_sha}^{{commit}}",
        )
        if returncode != 0:
            raise WorkspaceError(stderr.strip() or f"unknown base SHA: {base_sha}")
        return stdout.strip()

    @property
    def repository(self) -> Path:
        return self._repository

    @property
    def path(self) -> Path:
        return self._path

    @property
    def base_sha(self) -> str:
        return self._base_sha

    @property
    def snapshot_id(self) -> str:
        return self._snapshot_id

    @property
    def attempt_id(self) -> str:
        return self._attempt_id

    @property
    def policy(self) -> ResourcePolicy:
        return self._policy

    async def execute(self, command: str, timeout_sec: float) -> CommandResult:
        """Execute a command with a scrubbed environment and bounded output."""
        if timeout_sec <= 0:
            raise ValueError("timeout_sec must be positive")
        started = time.monotonic()
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=self._path,
            env=self._policy.sanitized_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_sec)
            return CommandResult(
                command=command,
                returncode=process.returncode,
                stdout=stdout.decode(errors="replace")[:_MAX_OUTPUT_CHARS],
                stderr=stderr.decode(errors="replace")[:_MAX_OUTPUT_CHARS],
                duration_sec=round(time.monotonic() - started, 4),
                status="VERIFIED",
            )
        except TimeoutError:
            if os.name == "posix":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            stdout, stderr = await process.communicate()
            return CommandResult(
                command=command,
                returncode=process.returncode,
                stdout=stdout.decode(errors="replace")[:_MAX_OUTPUT_CHARS],
                stderr=stderr.decode(errors="replace")[:_MAX_OUTPUT_CHARS],
                duration_sec=round(time.monotonic() - started, 4),
                timed_out=True,
                status="INCONCLUSIVE",
            )

    def _safe_path(self, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute() or "\x00" in relative_path:
            raise ValueError("workspace paths must be relative and NUL-free")
        resolved = (self._path / candidate).resolve()
        try:
            resolved.relative_to(self._path)
        except ValueError as exc:
            raise ValueError("workspace path escapes the attempt") from exc
        return resolved

    async def read_file(self, path: str) -> str:
        target = self._safe_path(path)
        if not target.is_file():
            raise FileNotFoundError(path)
        return await asyncio.to_thread(target.read_text, encoding="utf-8")

    async def write_file(self, path: str, content: str) -> None:
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_text, content, encoding="utf-8")

    async def apply_patch(self, patch: PatchCandidate) -> None:
        if patch.patch is not None:
            returncode, _, stderr = await self._run_git(
                self._path,
                "apply",
                "--whitespace=nowarn",
                "-",
                input_text=patch.patch,
            )
            if returncode != 0:
                raise WorkspaceError(stderr.strip() or "git apply failed")
            return
        if not patch.path:
            raise ValueError("content PatchCandidate requires a path")
        target = self._safe_path(patch.path)
        if patch.is_deleted:
            if target.exists():
                await asyncio.to_thread(target.unlink)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_text, patch.content or "", encoding="utf-8")

    async def _git_output(self, *args: str) -> tuple[int, str, str]:
        return await self._run_git(self._path, *args)

    async def _stage_untracked(self) -> None:
        returncode, _, stderr = await self._git_output("add", "--intent-to-add", "--", ".")
        if returncode != 0:
            raise WorkspaceError(stderr.strip() or "git intent-to-add failed")

    async def changed_files(self) -> tuple[str, ...]:
        returncode, tracked, stderr = await self._git_output(
            "diff",
            "--name-only",
            "--diff-filter=ACDMRTUXB",
            self._base_sha,
            "--",
        )
        if returncode != 0:
            raise WorkspaceError(stderr.strip() or "git diff failed")
        returncode, untracked, stderr = await self._git_output(
            "ls-files",
            "--others",
            "--exclude-standard",
        )
        if returncode != 0:
            raise WorkspaceError(stderr.strip() or "git ls-files failed")
        names = {line.strip() for line in tracked.splitlines() if line.strip()}
        names.update(line.strip() for line in untracked.splitlines() if line.strip())
        return tuple(sorted(names))

    async def diff_from_base(self) -> WorkspaceDiff:
        try:
            await self._stage_untracked()
            returncode, patch, stderr = await self._git_output(
                "diff",
                "--no-ext-diff",
                "--binary",
                "--full-index",
                self._base_sha,
                "--",
            )
            if returncode != 0:
                raise WorkspaceError(stderr.strip() or "git diff failed")
            changed = await self.changed_files()
            returncode, added, stderr = await self._git_output(
                "diff",
                "--name-only",
                "--diff-filter=A",
                self._base_sha,
                "--",
            )
            if returncode != 0:
                raise WorkspaceError(stderr.strip() or "git added-file diff failed")
            returncode, deleted, stderr = await self._git_output(
                "diff",
                "--name-only",
                "--diff-filter=D",
                self._base_sha,
                "--",
            )
            if returncode != 0:
                raise WorkspaceError(stderr.strip() or "git deleted-file diff failed")
            added_files = tuple(sorted(set(added.splitlines()) & set(changed)))
            deleted_files = tuple(sorted(set(deleted.splitlines()) & set(changed)))
            return WorkspaceDiff(
                base_sha=self._base_sha,
                snapshot_id=self._snapshot_id,
                attempt_id=self._attempt_id,
                patch=patch,
                changed_files=changed,
                added_files=added_files,
                deleted_files=deleted_files,
                status="VERIFIED",
                diff_hash=compute_diff_hash(
                    base_sha=self._base_sha,
                    patch=patch,
                    changed_files=changed,
                    added_files=added_files,
                    deleted_files=deleted_files,
                ),
            )
        except WorkspaceError:
            raise
        except Exception as exc:
            raise WorkspaceError(str(exc)) from exc

    async def reset(self) -> None:
        returncode, _, stderr = await self._git_output("reset", "--hard", self._base_sha)
        if returncode != 0:
            raise WorkspaceError(stderr.strip() or "git reset failed")
        returncode, _, stderr = await self._git_output("clean", "-fdx")
        if returncode != 0:
            raise WorkspaceError(stderr.strip() or "git clean failed")

    async def cleanup(self) -> None:
        if not self._path.exists():
            return
        returncode, _, stderr = await self._run_git(
            self._repository,
            "worktree",
            "remove",
            "--force",
            str(self._path),
        )
        if returncode != 0 and self._path.exists():
            shutil.rmtree(self._path)
            await self._run_git(self._repository, "worktree", "prune")
            if stderr:
                pass
