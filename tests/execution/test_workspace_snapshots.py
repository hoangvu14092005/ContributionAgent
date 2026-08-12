"""Snapshot and lifecycle tests for execution workspaces."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from contribai.execution.resource_policy import ResourcePolicy
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
def repo_with_two_commits(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch", "main")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Workspace Tests")
    source = repo / "source.txt"
    source.write_text("base\n", encoding="utf-8")
    _git(repo, "add", "source.txt")
    _git(repo, "commit", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD")
    source.write_text("later\n", encoding="utf-8")
    _git(repo, "commit", "-am", "later")
    return repo, base_sha


@pytest.mark.asyncio
async def test_attempts_are_clean_independent_snapshots(
    repo_with_two_commits: tuple[Path, str],
) -> None:
    repo, base_sha = repo_with_two_commits
    manager = WorkspaceManager(repo)
    first = await manager.create_attempt("work-1", base_sha, "attempt-1", ResourcePolicy())
    second = await manager.create_attempt("work-1", base_sha, "attempt-2", ResourcePolicy())

    try:
        assert first.base_sha == second.base_sha == base_sha
        assert first.snapshot_id != second.snapshot_id
        assert first.attempt_id != second.attempt_id
        assert await first.read_file("source.txt") == "base\n"
        assert await second.read_file("source.txt") == "base\n"

        await first.write_file("source.txt", "first attempt\n")
        await first.write_file("first-only.txt", "first\n")

        assert await first.read_file("source.txt") == "first attempt\n"
        assert await second.read_file("source.txt") == "base\n"
        assert await second.changed_files() == ()
        assert await first.changed_files() == ("first-only.txt", "source.txt")
    finally:
        await manager.destroy_attempt(first.snapshot_id)
        await manager.destroy_attempt(second.snapshot_id)


@pytest.mark.asyncio
async def test_destroy_attempt_removes_snapshot_and_is_idempotent(
    repo_with_two_commits: tuple[Path, str],
) -> None:
    repo, base_sha = repo_with_two_commits
    manager = WorkspaceManager(repo)
    workspace = await manager.create_attempt("work-1", base_sha, "attempt-1", ResourcePolicy())
    workspace_path = workspace.path

    await manager.destroy_attempt(workspace.snapshot_id)
    await manager.destroy_attempt(workspace.snapshot_id)

    assert not workspace_path.exists()
    assert manager.active_snapshot_ids == ()
