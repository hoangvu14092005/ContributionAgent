"""Contract tests for execution workspaces."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from contribai.execution.resource_policy import ResourcePolicy
from contribai.execution.workspaces.base import PatchCandidate
from contribai.execution.workspaces.docker import DockerWorkspace
from contribai.execution.workspaces.manager import WorkspaceManager


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch", "main")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Workspace Tests")
    (repo / "README.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.txt")
    _git(repo, "commit", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


@pytest.mark.asyncio
async def test_local_workspace_implements_the_workspace_contract(
    git_repo: tuple[Path, str],
) -> None:
    repo, base_sha = git_repo
    manager = WorkspaceManager(repo)
    workspace = await manager.create_attempt(
        "work-1",
        base_sha,
        "attempt-1",
        ResourcePolicy(),
    )

    try:
        assert workspace.base_sha == base_sha
        assert workspace.snapshot_id
        assert workspace.attempt_id == "attempt-1"

        with pytest.raises(AttributeError):
            workspace.base_sha = "a-different-sha"  # type: ignore[misc]

        await workspace.write_file("generated.py", "value = 1\n")
        assert await workspace.read_file("generated.py") == "value = 1\n"

        await workspace.apply_patch(
            PatchCandidate(
                patch="""--- a/README.txt
+++ b/README.txt
@@ -1 +1 @@
-base
+patched
"""
            )
        )
        assert await workspace.read_file("README.txt") == "patched\n"
        assert await workspace.changed_files() == ("README.txt", "generated.py")

        diff = await workspace.diff_from_base()
        assert diff.base_sha == base_sha
        assert diff.changed_files == ("README.txt", "generated.py")
        assert "patched" in diff.patch
        assert "generated.py" in diff.patch

        await workspace.reset()
        assert await workspace.changed_files() == ()
        assert await workspace.read_file("README.txt") == "base\n"
    finally:
        await manager.destroy_attempt(workspace.snapshot_id)


@pytest.mark.asyncio
async def test_workspace_rejects_paths_outside_the_attempt(git_repo: tuple[Path, str]) -> None:
    repo, base_sha = git_repo
    manager = WorkspaceManager(repo)
    workspace = await manager.create_attempt("work-1", base_sha, "attempt-1", ResourcePolicy())

    try:
        with pytest.raises(ValueError):
            await workspace.read_file("../outside.txt")
        with pytest.raises(ValueError):
            await workspace.write_file("/absolute.txt", "unsafe")
    finally:
        await manager.destroy_attempt(workspace.snapshot_id)


@pytest.mark.asyncio
async def test_execute_returns_an_explicit_timeout_result(git_repo: tuple[Path, str]) -> None:
    repo, base_sha = git_repo
    manager = WorkspaceManager(repo)
    workspace = await manager.create_attempt("work-1", base_sha, "attempt-1", ResourcePolicy())

    try:
        result = await workspace.execute(
            f"{sys.executable} -c 'import time; time.sleep(0.2)'",
            timeout_sec=0.02,
        )
        assert result.timed_out is True
        assert result.success is False
        assert result.status == "INCONCLUSIVE"
    finally:
        await manager.destroy_attempt(workspace.snapshot_id)


def test_docker_command_is_fail_closed_and_resource_bounded(tmp_path: Path) -> None:
    command = DockerWorkspace.build_docker_command(
        image="python:3.12-slim",
        workspace_path=tmp_path,
        command="python -c 'print(1)'",
        policy=ResourcePolicy(),
    )
    joined = " ".join(command)

    assert "--network none" in joined
    assert "--user 65532:65532" in joined
    assert "--cpus 1.0" in joined
    assert "--memory 512m" in joined
    assert "--pids-limit 128" in joined
    assert "--read-only" in joined
    assert "/var/run/docker.sock" not in joined


@pytest.mark.asyncio
async def test_docker_attempt_is_self_contained_git_clone(
    git_repo: tuple[Path, str],
    tmp_path: Path,
) -> None:
    repo, base_sha = git_repo
    manager = WorkspaceManager(
        repo,
        workspace_root=tmp_path / "docker-workspaces",
        backend="docker",
    )
    workspace = await manager.create_attempt(
        "work-docker",
        base_sha,
        "attempt-1",
        ResourcePolicy(),
    )

    try:
        assert isinstance(workspace, DockerWorkspace)
        assert (workspace.path / ".git").is_dir()
        assert _git(workspace.path, "rev-parse", "HEAD") == base_sha
        assert _git(workspace.path, "remote") == ""
        await workspace.write_file("README.txt", "changed\n")
        diff = await workspace.diff_from_base()
        assert diff.changed_files == ("README.txt",)
    finally:
        path = workspace.path
        await manager.destroy_attempt(workspace.snapshot_id)
        assert not path.exists()


@pytest.mark.asyncio
async def test_unavailable_docker_is_not_a_success(git_repo: tuple[Path, str]) -> None:
    repo, base_sha = git_repo
    workspace = DockerWorkspace(
        repo,
        base_sha=base_sha,
        snapshot_id="snapshot-1",
        attempt_id="attempt-1",
        workspace_path=repo,
        docker_available=False,
    )

    result = await workspace.execute("echo should-not-run", timeout_sec=1)

    assert result.success is False
    assert result.status == "UNVERIFIED"


@pytest.mark.asyncio
async def test_sandbox_unavailable_validator_is_not_a_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contribai.sandbox.sandbox import Sandbox

    monkeypatch.setattr("contribai.sandbox.sandbox.shutil.which", lambda _: None)
    result = await Sandbox(enabled=True).validate("print('hello')", "python")

    assert result.success is False
    assert result.status == "UNVERIFIED"
