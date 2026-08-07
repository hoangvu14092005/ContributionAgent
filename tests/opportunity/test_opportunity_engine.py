"""Opportunity ranking and issue-first policy tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from contribai.analysis.repo_intel import RepoProfile
from contribai.core.models import ContributionType, Finding, Issue, Repository, Severity
from contribai.opportunity.engine import OpportunityEngine, OpportunitySource
from contribai.opportunity.models import CandidateFinding
from contribai.orchestrator.pipeline import ContribPipeline


def _repo() -> Repository:
    return Repository(
        owner="owner",
        name="repo",
        full_name="owner/repo",
        language="Python",
        stars=10_000,
    )


def _finding() -> Finding:
    return Finding(
        id="finding-1",
        type=ContributionType.CODE_QUALITY,
        severity=Severity.MEDIUM,
        title="Handle stale service value",
        description="The service returns a stale value on the retry path.",
        file_path="src/service.py",
        confidence=0.8,
    )


def test_issue_first_ranking_uses_expected_value_and_evidence() -> None:
    issue = Issue(
        number=42,
        title="Fix stale service value",
        body="The retry path returns stale data.",
        labels=["good first issue", "bug"],
    )
    profile = RepoProfile(
        repo="owner/repo",
        preferred_types=["bug_fix"],
        avg_review_hours=12,
        summary="active",
    )

    ranked = OpportunityEngine().rank(
        _repo(), issues=[issue], findings=[_finding()], profile=profile
    )

    assert ranked
    assert ranked[0].source is OpportunitySource.ISSUE
    assert ranked[0].issue_number == 42
    assert ranked[0].score.expected_value > 0
    assert len(ranked[0].score.evidence) >= 4
    assert all(item.feature and item.rationale for item in ranked[0].score.evidence)


def test_code_scan_is_explicit_candidate_finding_and_never_a_write_operation() -> None:
    ranked = OpportunityEngine().rank(_repo(), findings=[_finding()], issues=[])

    assert len(ranked) == 1
    assert ranked[0].source is OpportunitySource.CODE_SCAN
    assert isinstance(ranked[0].task, CandidateFinding)
    assert ranked[0].task.file_path == "src/service.py"
    assert not hasattr(ranked[0], "publisher")
    assert not hasattr(ranked[0], "github_client")


def test_issue_first_keeps_issues_before_code_scan_even_when_scores_are_close() -> None:
    issue = Issue(number=7, title="Small bug", labels=["bug"])
    ranked = OpportunityEngine().rank(_repo(), issues=[issue], findings=[_finding()])

    assert [item.source for item in ranked] == [
        OpportunitySource.ISSUE,
        OpportunitySource.CODE_SCAN,
    ]


def test_score_evidence_is_deterministic() -> None:
    engine = OpportunityEngine()
    first = engine.rank(_repo(), findings=[_finding()])
    second = engine.rank(_repo(), findings=[_finding()])

    assert first == second
    assert first[0].score.expected_value == first[0].score.expected_value


@pytest.mark.asyncio
async def test_memory_persists_score_evidence(memory) -> None:
    candidate = OpportunityEngine().rank(_repo(), findings=[_finding()])[0]

    score_id = await memory.record_opportunity_score(candidate)
    rows = await memory.get_opportunity_scores("owner/repo")

    assert score_id > 0
    assert rows[0]["source"] == "code_scan"
    assert rows[0]["evidence"]
    assert rows[0]["score"]["expected_value"] == candidate.score.expected_value


@pytest.mark.asyncio
async def test_pipeline_ranks_issue_candidates_and_persists_evidence() -> None:
    pipeline = ContribPipeline(MagicMock())
    pipeline._memory = AsyncMock()
    pipeline._repo_intel = AsyncMock()
    pipeline._repo_intel.profile.return_value = RepoProfile(repo="owner/repo")

    issues = [
        Issue(number=2, title="Small cleanup", body="A short request."),
        Issue(
            number=1,
            title="Fix crash",
            body="Steps to reproduce: run the command; expected behavior is success.",
            labels=["bug", "good first issue"],
        ),
    ]

    ranked = await pipeline._rank_issue_opportunities(_repo(), issues, max_candidates=2)

    assert [issue.number for issue in ranked] == [1, 2]
    assert pipeline._memory.record_opportunity_score.await_count == 2
    persisted = pipeline._memory.record_opportunity_score.await_args_list[0].args[0]
    assert persisted.source is OpportunitySource.ISSUE
