"""Candidate ranking tests."""

from __future__ import annotations

from contribai.engines.candidates import CandidateSet, PatchCandidate
from contribai.verification.models import (
    VerificationEvidence,
    VerificationReport,
    VerificationStatus,
)
from contribai.verification.ranker import CandidateRanker


def _candidate(attempt_id: str, patch: str) -> PatchCandidate:
    return PatchCandidate.from_patch(
        attempt_id=attempt_id,
        base_sha="base-1",
        patch=patch,
        changed_files=("src/service.py",),
    )


def _report(
    candidate: PatchCandidate, status: VerificationStatus, score: float
) -> VerificationReport:
    evidence = VerificationEvidence(
        check="tests",
        passed=status is VerificationStatus.PASSED,
        status="VERIFIED",
        command="pytest",
        exit_code=0,
        output="ok",
    )
    return VerificationReport(
        status=status,
        baseline_passed=True,
        syntax_passed=True,
        tests_passed=status is VerificationStatus.PASSED,
        lint_passed=True,
        typecheck_passed=True,
        security_passed=True,
        quality_score=score,
        tests_run=1,
        tests_failed=0 if status is VerificationStatus.PASSED else 1,
        evidence=(evidence,),
        candidate_hash=candidate.patch_sha256,
    )


def test_ranker_prefers_verified_quality_and_keeps_all_reports() -> None:
    first = _candidate("a", "first")
    second = _candidate("b", "second")
    candidates = CandidateSet.from_candidates((first, second))
    reports = {
        first.patch_sha256: _report(first, VerificationStatus.INCONCLUSIVE, 1.0),
        second.patch_sha256: _report(second, VerificationStatus.PASSED, 0.8),
    }

    ranking = CandidateRanker().rank(candidates, reports)

    assert ranking.best is not None
    assert ranking.best.candidate is second
    assert len(ranking.items) == 2
    assert ranking.items[0].report.status is VerificationStatus.PASSED
    assert ranking.items[1].report.status is VerificationStatus.INCONCLUSIVE
