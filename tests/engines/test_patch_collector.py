"""Patch collection safety tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from contribai.engines.models import EngineOutcome, EngineStatus, EngineUsage
from contribai.engines.patch_collector import (
    BeforeSnapshot,
    PatchCollectionError,
    PatchCollector,
)
from contribai.execution.workspaces.base import WorkspaceDiff


@dataclass
class FakeWorkspace:
    base_sha: str = "base-1"
    snapshot_id: str = "snapshot-1"
    attempt_id: str = "attempt-1"
    changed: tuple[str, ...] = ()
    diff: WorkspaceDiff | None = None

    async def changed_files(self) -> tuple[str, ...]:
        return self.changed

    async def diff_from_base(self) -> WorkspaceDiff:
        assert self.diff is not None
        return self.diff


def _outcome(status: EngineStatus = EngineStatus.COMPLETED) -> EngineOutcome:
    return EngineOutcome(
        status=status,
        exit_reason=status.value,
        events=(),
        usage=EngineUsage(),
        cost_usd=0,
        trajectory_id="trajectory-1",
        engine_version="native@1",
    )


def _diff(**overrides) -> WorkspaceDiff:
    values = {
        "base_sha": "base-1",
        "snapshot_id": "snapshot-1",
        "attempt_id": "attempt-1",
        "patch": "diff --git a/src/service.py b/src/service.py\n+return 2\n",
        "changed_files": ("src/service.py",),
        "status": "VERIFIED",
    }
    values.update(overrides)
    return WorkspaceDiff(**values)


@pytest.mark.asyncio
async def test_patch_collector_captures_before_and_collects_deterministic_candidate() -> None:
    workspace = FakeWorkspace(diff=_diff())
    collector = PatchCollector()

    before = await collector.capture_before(workspace)
    workspace.changed = ("src/service.py",)
    candidate = await collector.collect(workspace, before, _outcome())
    repeat = await collector.collect(workspace, before, _outcome())

    assert isinstance(before, BeforeSnapshot)
    assert candidate.base_sha == "base-1"
    assert candidate.changed_files == ("src/service.py",)
    assert candidate.patch_sha256 == repeat.patch_sha256
    assert candidate.candidate_id == repeat.candidate_id
    assert candidate.patch


@pytest.mark.asyncio
async def test_patch_collector_rejects_nonterminal_or_unverified_outcomes() -> None:
    workspace = FakeWorkspace(diff=_diff())
    collector = PatchCollector()
    before = await collector.capture_before(workspace)

    with pytest.raises(PatchCollectionError):
        await collector.collect(workspace, before, _outcome(EngineStatus.WAITING_FOR_APPROVAL))

    workspace.diff = _diff(status="INCONCLUSIVE")
    with pytest.raises(PatchCollectionError):
        await collector.collect(workspace, before, _outcome())


@pytest.mark.asyncio
async def test_patch_collector_rejects_base_change_hash_mismatch_and_escape_path() -> None:
    collector = PatchCollector()
    workspace = FakeWorkspace(diff=_diff())
    before = await collector.capture_before(workspace)

    workspace.base_sha = "changed-base"
    with pytest.raises(PatchCollectionError, match="base SHA"):
        await collector.collect(workspace, before, _outcome())

    workspace.base_sha = "base-1"
    workspace.diff = _diff(diff_hash="wrong")
    with pytest.raises(PatchCollectionError, match="hash"):
        await collector.collect(workspace, before, _outcome())

    workspace.diff = _diff(changed_files=("../outside.py",))
    with pytest.raises(PatchCollectionError, match="outside"):
        await collector.collect(workspace, before, _outcome())
