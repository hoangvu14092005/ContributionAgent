"""Workspace lifecycle manager and clean-attempt factory."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Literal

from contribai.execution.resource_policy import ResourcePolicy
from contribai.execution.workspaces.base import Workspace, WorkspaceError
from contribai.execution.workspaces.docker import DockerWorkspace
from contribai.execution.workspaces.local import LocalWorkspace

WorkspaceBackend = Literal["local", "docker"]


class WorkspaceManager:
    """Create and destroy independent snapshots rooted at one base SHA."""

    def __init__(
        self,
        repository: Path,
        *,
        workspace_root: Path | None = None,
        backend: WorkspaceBackend = "local",
        docker_image: str = "python:3.12-slim",
    ) -> None:
        self.repository = Path(repository).resolve()
        if not self.repository.is_dir():
            raise ValueError("repository must be a directory")
        if backend not in {"local", "docker"}:
            raise ValueError("backend must be local or docker")
        self.backend = backend
        self.docker_image = docker_image
        if workspace_root is None:
            workspace_root = Path(tempfile.mkdtemp(prefix="contribai-workspaces-"))
            self._owns_workspace_root = True
        else:
            workspace_root.mkdir(parents=True, exist_ok=True)
            self._owns_workspace_root = False
        self.workspace_root = Path(workspace_root).resolve()
        self._attempts: dict[str, Workspace] = {}

    @property
    def active_snapshot_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._attempts))

    @staticmethod
    def _safe_component(value: str, label: str) -> str:
        if not value.strip() or value in {".", ".."}:
            raise ValueError(f"{label} must not be empty")
        if any(char in value for char in "/\\\x00"):
            raise ValueError(f"{label} must be a path-safe identifier")
        return value

    async def create_attempt(
        self,
        work_id: str,
        base_sha: str,
        attempt_id: str,
        policy: ResourcePolicy,
    ) -> Workspace:
        """Create a clean detached attempt from the requested base SHA."""
        work_component = self._safe_component(work_id, "work_id")
        attempt_component = self._safe_component(attempt_id, "attempt_id")
        snapshot_id = f"{work_component}:{attempt_component}:{uuid.uuid4().hex[:12]}"
        workspace_path = self.workspace_root / (
            f"{work_component}-{attempt_component}-{uuid.uuid4().hex[:12]}"
        )
        local = await LocalWorkspace.create(
            self.repository,
            base_sha=base_sha,
            snapshot_id=snapshot_id,
            attempt_id=attempt_id,
            workspace_path=workspace_path,
            policy=policy,
        )
        workspace: Workspace = local
        if self.backend == "docker":
            workspace = DockerWorkspace(
                self.repository,
                base_sha=local.base_sha,
                snapshot_id=local.snapshot_id,
                attempt_id=local.attempt_id,
                workspace_path=local.path,
                policy=policy,
                image=self.docker_image,
            )
        self._attempts[snapshot_id] = workspace
        return workspace

    async def destroy_attempt(self, snapshot_id: str) -> None:
        """Destroy an attempt; repeated cleanup is intentionally idempotent."""
        workspace = self._attempts.pop(snapshot_id, None)
        if workspace is None:
            return
        cleanup = getattr(workspace, "cleanup", None)
        if cleanup is None:
            raise WorkspaceError("workspace implementation does not support cleanup")
        await cleanup()

    async def destroy_all(self) -> None:
        """Best-effort cleanup for process shutdown."""
        for snapshot_id in tuple(self._attempts):
            await self.destroy_attempt(snapshot_id)
