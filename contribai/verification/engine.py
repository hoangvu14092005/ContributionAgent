"""Baseline-preserving verification pipeline for isolated patch candidates."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from contribai.engines.candidates import PatchCandidate
from contribai.execution.workspaces.base import CommandResult, Workspace
from contribai.execution.workspaces.base import PatchCandidate as WorkspacePatch
from contribai.verification.models import (
    FailureContext,
    VerificationEvidence,
    VerificationReport,
    VerificationStatus,
)
from contribai.verification.runners import VerificationRunner, WorkspaceVerificationRunner


@dataclass(frozen=True, slots=True)
class VerificationPlan:
    """Commands run in order after baseline preservation."""

    baseline_command: str = "git diff --quiet"
    syntax_command: str | None = None
    tests_command: str | None = None
    lint_command: str | None = None
    typecheck_command: str | None = None
    security_command: str | None = None
    timeout_sec: float = 120.0

    def __post_init__(self) -> None:
        if self.timeout_sec <= 0:
            raise ValueError("verification timeout must be positive")


class VerificationEngine:
    """Verify each candidate in its own workspace and preserve all evidence."""

    def __init__(self, *, runner: VerificationRunner | None = None) -> None:
        self._runner = runner or WorkspaceVerificationRunner()

    async def verify(
        self,
        candidate: PatchCandidate,
        workspace: Workspace,
        *,
        plan: VerificationPlan | None = None,
    ) -> VerificationReport:
        """Run baseline, apply, syntax, tests, lint, typecheck and security checks."""
        plan = plan or VerificationPlan(syntax_command=_default_syntax_command(candidate))
        evidence: list[VerificationEvidence] = []
        baseline = await self._run_check(
            workspace, "baseline", plan.baseline_command, plan.timeout_sec
        )
        evidence.append(baseline[0])
        if not baseline[1].success:
            return self._report(
                candidate,
                VerificationStatus.FAILED
                if baseline[1].status == "VERIFIED"
                else VerificationStatus.INCONCLUSIVE,
                evidence,
                baseline[2],
            )

        try:
            await workspace.apply_patch(WorkspacePatch(patch=candidate.patch))
        except Exception as exc:
            evidence.append(
                VerificationEvidence(
                    check="patch_apply",
                    passed=False,
                    status="VERIFIED",
                    command="git apply",
                    output=str(exc),
                )
            )
            return self._report(
                candidate,
                VerificationStatus.FAILED,
                evidence,
                FailureContext("git apply", None, traceback=str(exc), recoverable=False),
            )

        checks = (
            ("syntax", plan.syntax_command),
            ("tests", plan.tests_command),
            ("lint", plan.lint_command),
            ("typecheck", plan.typecheck_command),
            ("security", plan.security_command),
        )
        first_failure: FailureContext | None = None
        for name, command in checks:
            item, _result, failure = await self._run_check(
                workspace, name, command, plan.timeout_sec
            )
            evidence.append(item)
            if failure and first_failure is None:
                first_failure = failure

        status = self._status_for(evidence)
        tests_run = sum(1 for item in evidence if item.check == "tests" and item.command)
        tests_failed = sum(
            1
            for item in evidence
            if item.check == "tests" and item.status == "VERIFIED" and not item.passed
        )
        return self._report(
            candidate,
            status,
            evidence,
            first_failure,
            tests_run=tests_run,
            tests_failed=tests_failed,
        )

    async def _run_check(
        self,
        workspace: Workspace,
        name: str,
        command: str | None,
        timeout_sec: float,
    ) -> tuple[VerificationEvidence, CommandResult, FailureContext | None]:
        if not command:
            result = CommandResult(command="", status="UNVERIFIED")
            return (
                VerificationEvidence(name, False, "UNVERIFIED", output="validator not configured"),
                result,
                None,
            )
        try:
            result = await self._runner.run(workspace, command, timeout_sec)
        except Exception as exc:
            result = CommandResult(command=command, status="INCONCLUSIVE", stderr=str(exc))
        passed = result.success
        status = result.status
        evidence = VerificationEvidence(
            check=name,
            passed=passed,
            status=status,
            command=command,
            exit_code=result.returncode,
            output=(result.stdout + "\n" + result.stderr).strip(),
            duration_sec=result.duration_sec,
            recoverable=not passed and status == "VERIFIED",
        )
        failure = None
        if not passed and status == "VERIFIED":
            failure = FailureContext(
                command=command,
                exit_code=result.returncode,
                traceback=result.stderr,
                recoverable=not result.timed_out,
            )
        return evidence, result, failure

    @staticmethod
    def _status_for(evidence: list[VerificationEvidence]) -> VerificationStatus:
        configured = [item for item in evidence if item.command]
        post_patch = [item for item in configured if item.check != "baseline"]
        if not post_patch:
            return VerificationStatus.INCONCLUSIVE
        if any(not item.passed and item.status == "VERIFIED" for item in configured):
            return VerificationStatus.FAILED
        if any(item.status != "VERIFIED" for item in configured):
            return VerificationStatus.INCONCLUSIVE
        return VerificationStatus.PASSED

    @staticmethod
    def _report(
        candidate: PatchCandidate,
        status: VerificationStatus,
        evidence: list[VerificationEvidence],
        failure_context: FailureContext | None,
        *,
        tests_run: int = 0,
        tests_failed: int = 0,
    ) -> VerificationReport:
        checks = [item for item in evidence if item.check != "patch_apply" and item.command]
        quality_score = sum(1.0 for item in checks if item.passed) / len(checks) if checks else 0.0
        return VerificationReport(
            status=status,
            baseline_passed=_passed(evidence, "baseline"),
            syntax_passed=_passed(evidence, "syntax"),
            tests_passed=_passed(evidence, "tests"),
            lint_passed=_passed(evidence, "lint"),
            typecheck_passed=_passed(evidence, "typecheck"),
            security_passed=_passed(evidence, "security"),
            quality_score=quality_score,
            tests_run=tests_run,
            tests_failed=tests_failed,
            evidence=tuple(evidence),
            candidate_hash=candidate.patch_sha256,
            failure_context=failure_context,
            regression_safety_score=(1.0 if _passed(evidence, "baseline") else 0.0),
            minimality_score=1.0 / max(1, len(candidate.changed_files)),
        )


def _passed(evidence: list[VerificationEvidence], name: str) -> bool:
    return any(item.check == name and item.passed for item in evidence)


def _default_syntax_command(candidate: PatchCandidate) -> str | None:
    python_files = [path for path in candidate.changed_files if Path(path).suffix == ".py"]
    if python_files:
        return "python -m py_compile " + " ".join(shlex.quote(path) for path in python_files)
    return None


__all__ = ["VerificationEngine", "VerificationPlan"]
