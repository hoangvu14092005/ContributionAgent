"""Docker-backed workspace execution with fail-closed resource flags."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import time
from pathlib import Path

from contribai.execution.resource_policy import ResourcePolicy
from contribai.execution.workspaces.base import CommandResult
from contribai.execution.workspaces.local import _MAX_OUTPUT_CHARS, LocalWorkspace


class DockerWorkspace(LocalWorkspace):
    """Run commands in a non-root, bounded Docker container over a clean worktree."""

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
    ) -> None:
        super().__init__(
            repository,
            base_sha=base_sha,
            snapshot_id=snapshot_id,
            attempt_id=attempt_id,
            workspace_path=workspace_path,
            policy=policy or ResourcePolicy(),
        )
        self.image = image
        self._docker_available = (
            shutil.which("docker") is not None if docker_available is None else docker_available
        )

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
    ) -> list[str]:
        """Build a Docker invocation with no host socket or host credentials."""
        return [
            "docker",
            "run",
            "--rm",
            "--network",
            policy.effective_network,
            "--user",
            "65532:65532",
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
