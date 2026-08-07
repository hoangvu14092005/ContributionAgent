"""Verification pipeline and evidence tests."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from contribai.engines.candidates import PatchCandidate
from contribai.execution.workspaces.base import CommandResult
from contribai.verification.engine import VerificationEngine, VerificationPlan
from contribai.verification.models import VerificationStatus
from contribai.verification.runners import VerificationRunner


@dataclass
class FakeWorkspace:
    results: dict[str, CommandResult]
    applied: list[object] = field(default_factory=list)

    async def apply_patch(self, patch) -> None:
        self.applied.append(patch)

    async def execute(self, command: str, timeout_sec: float) -> CommandResult:
        return self.results.get(
            command,
            CommandResult(command=command, returncode=0, status="VERIFIED"),
        )


class FakeRunner(VerificationRunner):
    async def run(self, workspace, command: str, timeout_sec: float) -> CommandResult:
        return await workspace.execute(command, timeout_sec)


def _candidate() -> PatchCandidate:
    return PatchCandidate.from_patch(
        attempt_id="attempt-1",
        base_sha="base-1",
        patch="diff --git a/src/service.py b/src/service.py\n+return 2\n",
        changed_files=("src/service.py",),
    )


def _plan() -> VerificationPlan:
    return VerificationPlan(
        baseline_command="baseline",
        syntax_command="syntax",
        tests_command="tests",
        lint_command="lint",
        typecheck_command="typecheck",
        security_command="security",
    )


def _ok(command: str) -> CommandResult:
    return CommandResult(command=command, returncode=0, status="VERIFIED")


@pytest.mark.asyncio
async def test_verification_runs_ordered_checks_and_returns_publishable_report() -> None:
    workspace = FakeWorkspace(
        {
            name: _ok(name)
            for name in ("baseline", "syntax", "tests", "lint", "typecheck", "security")
        }
    )
    report = await VerificationEngine(runner=FakeRunner()).verify(
        _candidate(), workspace, plan=_plan()
    )

    assert report.status is VerificationStatus.PASSED
    assert report.publishable is True
    assert report.baseline_passed is True
    assert report.syntax_passed is True
    assert report.tests_passed is True
    assert report.lint_passed is True
    assert report.typecheck_passed is True
    assert report.security_passed is True
    assert report.quality_score == 1.0
    assert len(report.evidence) == 6
    assert workspace.applied


@pytest.mark.asyncio
async def test_verification_baseline_failure_blocks_patch_and_is_failed() -> None:
    workspace = FakeWorkspace({"baseline": CommandResult("baseline", 1, stderr="baseline failed")})

    report = await VerificationEngine(runner=FakeRunner()).verify(
        _candidate(), workspace, plan=_plan()
    )

    assert report.status is VerificationStatus.FAILED
    assert report.publishable is False
    assert report.baseline_passed is False
    assert not workspace.applied
    assert report.failure_context is not None
    assert report.failure_context.command == "baseline"


@pytest.mark.asyncio
async def test_verification_failed_check_and_unavailable_validator_are_distinct() -> None:
    failed = FakeWorkspace(
        {"baseline": _ok("baseline"), "syntax": CommandResult("syntax", 1, stderr="bad syntax")}
    )
    failed_report = await VerificationEngine(runner=FakeRunner()).verify(
        _candidate(), failed, plan=_plan()
    )
    assert failed_report.status is VerificationStatus.FAILED
    assert failed_report.syntax_passed is False

    unavailable = FakeWorkspace(
        {
            name: _ok(name)
            for name in ("baseline", "syntax", "tests", "lint", "typecheck", "security")
        }
        | {"typecheck": CommandResult("typecheck", status="UNVERIFIED")}
    )
    unavailable_report = await VerificationEngine(runner=FakeRunner()).verify(
        _candidate(), unavailable, plan=_plan()
    )
    assert unavailable_report.status is VerificationStatus.INCONCLUSIVE
    assert unavailable_report.publishable is False
    assert unavailable_report.typecheck_passed is False


@pytest.mark.asyncio
async def test_attempts_keep_separate_reports_and_candidate_hash() -> None:
    candidate = _candidate()
    first = FakeWorkspace({name: _ok(name) for name in ("baseline", "syntax", "tests")})
    second = FakeWorkspace({"baseline": _ok("baseline"), "syntax": CommandResult("syntax", 1)})
    plan = VerificationPlan(
        baseline_command="baseline",
        syntax_command="syntax",
        tests_command="tests",
    )

    first_report = await VerificationEngine(runner=FakeRunner()).verify(candidate, first, plan=plan)
    second_report = await VerificationEngine(runner=FakeRunner()).verify(
        candidate, second, plan=plan
    )

    assert first_report.status is VerificationStatus.PASSED
    assert second_report.status is VerificationStatus.FAILED
    assert first_report.candidate_hash == second_report.candidate_hash == candidate.patch_sha256
    assert first is not second
