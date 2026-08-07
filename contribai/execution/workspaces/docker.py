"""Docker-backed workspace execution with fail-closed resource flags."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import time
from pathlib import Path

from contribai.execution.resource_policy import ResourcePolicy
from contribai.execution.workspaces.base import CommandResult, WorkspaceError
from contribai.execution.workspaces.local import _MAX_OUTPUT_CHARS, LocalWorkspace

_SAFE_CONTAINER_UID = 65532
_SAFE_CONTAINER_GID = 65532


class DockerWorkspace(LocalWorkspace):
    """Run commands in a non-root, bounded Docker container over an isolated clone.

    A normal ``git worktree`` contains a ``.git`` file that points back into the
    host repository. Mounting only that worktree into a container therefore
    breaks Git commands and also creates an unnecessary host-repository
    dependency. Docker attempts instead use a self-contained local clone whose
    Git metadata lives entirely inside the mounted workspace.
    """

    def __init__(
        self,
        repository: Path,
        *,
        base_sha: str,
        snapshot_id: str,
        attempt_id: str,
        workspace_path: Path,
        policy: ResourcePolicy | None = None,
        image: str = "python:3.12-slim",
        docker_available: bool | None = None,
        container_uid: int = _SAFE_CONTAINER_UID,
        container_gid: int = _SAFE_CONTAINER_GID,
    ) -> None:
        super().__init__(
            repository,
            base_sha=base_sha,
            snapshot_id=snapshot_id,
            attempt_id=attempt_id,
            workspace_path=workspace_path,
            policy=policy or ResourcePolicy(),
        )
        if container_uid < 1 or container_gid < 1:
            raise ValueError("Docker workspace must run as a non-root user")
        self.image = image
        self.container_uid = container_uid
        self.container_gid = container_gid
        self._docker_available = (
            shutil.which("docker") is not None if docker_available is None else docker_available
        )

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
        image: str = "python:3.12-slim",
        docker_available: bool | None = None,
    ) -> DockerWorkspace:
        """Create a self-contained detached clone at exactly ``base_sha``."""
        repository = Path(repository).resolve()
        workspace_path = Path(workspace_path).resolve()
        workspace_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_sha = await LocalWorkspace._resolve_sha(repository, base_sha)

        returncode, _, stderr = await LocalWorkspace._run_git(
            workspace_path.parent,
            "clone",
            "--no-checkout",
            "--no-hardlinks",
            str(repository),
            str(workspace_path),
        )
        if returncode != 0:
            raise WorkspaceError(stderr.strip() or "git clone for Docker workspace failed")

        try:
            returncode, _, stderr = await LocalWorkspace._run_git(
                workspace_path,
                "checkout",
                "--detach",
                resolved_sha,
            )
            if returncode != 0:
                raise WorkspaceError(stderr.strip() or "git checkout for Docker workspace failed")

            # An execution attempt must not have a configured remote it could push to.
            await LocalWorkspace._run_git(workspace_path, "remote", "remove", "origin")

            uid, gid = _container_identity()
            if _host_is_root():
                await asyncio.to_thread(_chown_tree, workspace_path, uid, gid)

            return cls(
                repository,
                base_sha=resolved_sha,
                snapshot_id=snapshot_id,
                attempt_id=attempt_id,
                workspace_path=workspace_path,
                policy=policy,
                image=image,
                docker_available=docker_available,
                container_uid=uid,
                container_gid=gid,
            )
        except BaseException:
            shutil.rmtree(workspace_path, ignore_errors=True)
            raise

    @property
    def docker_available(self) -> bool:
        return self._docker_available

    @staticmethod
    def build_docker_command(
        *,
        image: str,
        workspace_path: Path,
        command: str,
        policy: ResourcePolicy,
        container_uid: int = _SAFE_CONTAINER_UID,
        container_gid: int = _SAFE_CONTAINER_GID,
    ) -> list[str]:
        """Build a Docker invocation with no host socket or host credentials."""
        if container_uid < 1 or container_gid < 1:
            raise ValueError("Docker workspace must run as a non-root user")
        return [
            "docker",
            "run",
            "--rm",
            "--network",
            policy.effective_network,
            "--user",
            f"{container_uid}:{container_gid}",
            "--cpus",
            str(policy.cpu_limit),
            "--memory",
            f"{policy.memory_mb}m",
            "--pids-limit",
            str(policy.pids_limit),
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=bind,src={workspace_path},dst=/workspace,rw",
            "--workdir",
            "/workspace",
            image,
            "sh",
            "-lc",
            command,
        ]

    async def execute(self, command: str, timeout_sec: float) -> CommandResult:
        if timeout_sec <= 0:
            raise ValueError("timeout_sec must be positive")
        if not self._docker_available:
            return CommandResult(
                command=command,
                stderr="Docker is unavailable; execution was not verified",
                status="UNVERIFIED",
            )

        argv = self.build_docker_command(
            image=self.image,
            workspace_path=self.path,
            command=command,
            policy=self.policy,
            container_uid=self.container_uid,
            container_gid=self.container_gid,
        )
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *argv,
            env=self.policy.sanitized_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
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
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
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

    async def cleanup(self) -> None:
        """Remove the standalone clone without touching the source repository."""
        if self.path.exists():
            await asyncio.to_thread(shutil.rmtree, self.path, True)


def _host_is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def _container_identity() -> tuple[int, int]:
    """Use host ownership when safe so the bind mount remains writable."""
    geteuid = getattr(os, "geteuid", None)
    getegid = getattr(os, "getegid", None)
    if geteuid and getegid:
        uid = int(geteuid())
        gid = int(getegid())
        if uid > 0 and gid > 0:
            return uid, gid
    return _SAFE_CONTAINER_UID, _SAFE_CONTAINER_GID


def _chown_tree(root: Path, uid: int, gid: int) -> None:
    """Give the non-root container user ownership of a root-created clone."""
    for current, directories, files in os.walk(root):
        for name in (*directories, *files):
            path = Path(current) / name
            with contextlib.suppress(FileNotFoundError, PermissionError):
                os.chown(path, uid, gid, follow_symlinks=False)
    with contextlib.suppress(FileNotFoundError, PermissionError):
        os.chown(root, uid, gid, follow_symlinks=False)
