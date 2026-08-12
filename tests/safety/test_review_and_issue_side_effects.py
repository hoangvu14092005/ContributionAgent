"""Safety invariants for review-gated PR and issue side effects."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from contribai.core.models import (
    AnalysisResult,
    Contribution,
    ContributionType,
    FileChange,
    Finding,
    Issue,
    PRResult,
    PRStatus,
    Repository,
    Severity,
)
from contribai.github.guidelines import RepoGuidelines, fetch_repo_guidelines
from contribai.orchestrator import pipeline as pipeline_module
from contribai.orchestrator import review_gate as review_gate_module
from contribai.orchestrator.pipeline import ContribPipeline
from contribai.orchestrator.review_gate import HumanReviewer, ReviewDecision
from contribai.publishing import permit as permit_module
from contribai.publishing.github_publisher import GitHubPublisher


@pytest.fixture
def repo() -> Repository:
    return Repository(
        owner="acme",
        name="widgets",
        full_name="acme/widgets",
        default_branch="main",
    )


@pytest.fixture
def finding() -> Finding:
    return Finding(
        id="finding-1",
        type=ContributionType.CODE_QUALITY,
        severity=Severity.MEDIUM,
        title="Remove dead branch",
        description="The branch can never run.",
        file_path="src/service.py",
    )


@pytest.fixture
def contribution(finding: Finding) -> Contribution:
    return Contribution(
        finding=finding,
        contribution_type=ContributionType.CODE_QUALITY,
        title="refactor: remove dead branch",
        description="Removed the unreachable branch.",
        changes=[
            FileChange(
                path="src/service.py",
                original_content="if False:\n    run()\n",
                new_content="",
            )
        ],
        commit_message="refactor: remove dead branch",
    )


def _pipeline_with_review(action: str) -> tuple[ContribPipeline, AsyncMock, AsyncMock]:
    pipeline = ContribPipeline(MagicMock())
    review_gate = AsyncMock()
    review_gate.review.return_value = ReviewDecision(action)
    publish_seam = AsyncMock()
    publish_seam.create_pr.return_value = SimpleNamespace(pr_number=17)
    pipeline._review_gate = review_gate
    pipeline._pr_manager = publish_seam
    return pipeline, review_gate, publish_seam


@pytest.mark.parametrize(
    "action",
    [ReviewDecision.REJECT, ReviewDecision.SKIP, "unknown"],
)
@pytest.mark.parametrize("source_issue_number", [None, 41], ids=["code-scan", "issue-solving"])
@pytest.mark.asyncio
async def test_non_approval_makes_zero_publish_attempts_for_shared_seam(
    action: str,
    source_issue_number: int | None,
    repo: Repository,
    finding: Finding,
    contribution: Contribution,
) -> None:
    pipeline, review_gate, publish_seam = _pipeline_with_review(action)
    publish = getattr(pipeline, "_review_and_publish", None)

    assert callable(publish), "both contribution paths need one central review/publish seam"
    result = await publish(
        contribution,
        finding,
        repo,
        RepoGuidelines(),
        closes_issue=source_issue_number,
    )

    assert result is None
    review_gate.review.assert_awaited_once()
    publish_seam.create_pr.assert_not_awaited()


@pytest.mark.parametrize("source_issue_number", [None, 41], ids=["code-scan", "issue-solving"])
@pytest.mark.asyncio
async def test_approve_preserves_publish_flow_for_both_paths(
    source_issue_number: int | None,
    repo: Repository,
    finding: Finding,
    contribution: Contribution,
) -> None:
    pipeline, review_gate, publish_seam = _pipeline_with_review(ReviewDecision.APPROVE)
    publish = getattr(pipeline, "_review_and_publish", None)

    assert callable(publish), "both contribution paths need one central review/publish seam"
    result = await publish(
        contribution,
        finding,
        repo,
        RepoGuidelines(),
        closes_issue=source_issue_number,
    )

    assert result.pr_number == 17
    review_gate.review.assert_awaited_once()
    publish_seam.create_pr.assert_awaited_once()
    assert publish_seam.create_pr.await_args.kwargs.get("closes_issue") == source_issue_number


@pytest.mark.asyncio
async def test_new_issue_side_effect_requires_explicit_human_review(
    repo: Repository,
    finding: Finding,
    contribution: Contribution,
) -> None:
    gate_type = getattr(review_gate_module, "ReviewGate", None)
    side_effect_type = getattr(permit_module, "PublishSideEffect", None)
    assert gate_type is not None
    assert side_effect_type is not None
    assert getattr(review_gate_module, "PublishSideEffect", None) is side_effect_type
    assert not hasattr(review_gate_module, "ReviewSideEffect")

    reviewer = AsyncMock(spec=HumanReviewer)
    reviewer.review.return_value = ReviewDecision(ReviewDecision.APPROVE)
    gate = gate_type(reviewer, explicit_human_review=False)

    decision = await gate.review(
        contribution,
        finding,
        repo.full_name,
        planned_side_effects=(side_effect_type.CREATE_ISSUE, side_effect_type.CREATE_PR),
    )

    assert decision.skipped
    reviewer.review.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_reviewer_receives_planned_issue_side_effect(
    repo: Repository,
    finding: Finding,
    contribution: Contribution,
) -> None:
    gate_type = getattr(review_gate_module, "ReviewGate", None)
    side_effect_type = getattr(permit_module, "PublishSideEffect", None)
    assert gate_type is not None
    assert side_effect_type is not None
    assert getattr(review_gate_module, "PublishSideEffect", None) is side_effect_type

    reviewer = AsyncMock(spec=HumanReviewer)
    reviewer.review.return_value = ReviewDecision(ReviewDecision.APPROVE)
    gate = gate_type(reviewer, explicit_human_review=True)
    planned = (side_effect_type.CREATE_ISSUE, side_effect_type.CREATE_PR)

    decision = await gate.review(
        contribution,
        finding,
        repo.full_name,
        planned_side_effects=planned,
    )

    assert decision.approved
    assert decision.approved_side_effects == frozenset(planned)
    reviewer.review.assert_awaited_once_with(
        contribution,
        finding,
        repo.full_name,
        planned_side_effects=planned,
    )


async def _fetch_guidelines(contributing: str = "", template: str = "") -> RepoGuidelines:
    github = AsyncMock()

    async def get_file_content(owner: str, repo: str, path: str) -> str:
        if path == "CONTRIBUTING.md":
            return contributing
        if path == ".github/PULL_REQUEST_TEMPLATE.md":
            return template
        return ""

    github.get_file_content.side_effect = get_file_content
    return await fetch_repo_guidelines(github, "acme", "widgets")


@pytest.mark.asyncio
async def test_guideline_presence_alone_does_not_require_an_issue_link() -> None:
    guidelines = await _fetch_guidelines(
        contributing="Run tests before opening a pull request.",
        template="## Related issue\nCloses #",
    )

    assert guidelines.has_guidelines
    assert guidelines.requires_issue_link is False
    candidate = SimpleNamespace(guidelines=guidelines)
    assert GitHubPublisher._requires_linked_issue(candidate) is False


@pytest.mark.parametrize(
    "instruction",
    [
        "You must open an issue before submitting a pull request.",
        "All pull requests are required to link to an existing issue.",
    ],
)
@pytest.mark.asyncio
async def test_only_explicit_issue_link_requirements_are_parsed(instruction: str) -> None:
    guidelines = await _fetch_guidelines(contributing=instruction)

    assert guidelines.requires_issue_link is True
    candidate = SimpleNamespace(guidelines=guidelines)
    assert GitHubPublisher._requires_linked_issue(candidate) is True


@pytest.mark.parametrize(
    "instruction",
    [
        "Not all PRs must link to an issue.",
        "PRs must link to an issue only when applicable.",
        "If applicable, link an issue.",
    ],
)
@pytest.mark.asyncio
async def test_conditional_or_negated_issue_guidance_is_not_a_requirement(
    instruction: str,
) -> None:
    guidelines = await _fetch_guidelines(contributing=instruction)

    assert guidelines.requires_issue_link is False


def _runtime_pipeline(
    repo: Repository,
    finding: Finding,
    contribution: Contribution,
    action: str,
) -> ContribPipeline:
    config = MagicMock()
    config.pipeline.max_findings_per_repo = 3
    pipeline = ContribPipeline(config)
    pipeline._memory = AsyncMock()
    pipeline._memory.get_context.return_value = None
    pipeline._memory.get_repo_prs.return_value = []
    pipeline._github = AsyncMock()
    pipeline._github.get_file_tree.return_value = []
    pipeline._github.get_file_content.return_value = "def service():\n    return True\n"
    pipeline._github.list_pull_requests.return_value = []
    pipeline._repo_intel = AsyncMock()
    pipeline._repo_intel.profile.return_value = None
    pipeline._analyzer = AsyncMock()
    pipeline._analyzer.analyze.return_value = AnalysisResult(repo=repo, findings=[finding])
    pipeline._generator = AsyncMock()
    pipeline._generator.generate.return_value = contribution
    pipeline._event_bus = AsyncMock()
    pipeline._review_gate = AsyncMock()
    pipeline._review_gate.review.return_value = ReviewDecision(action)
    pipeline._pr_manager = AsyncMock()
    pipeline._pr_manager.create_pr.return_value = PRResult(
        repo=repo,
        contribution=contribution,
        pr_number=17,
        pr_url="https://github.com/acme/widgets/pull/17",
        status=PRStatus.OPEN,
        branch_name="fix/dead-branch",
        fork_full_name="bot/widgets",
    )
    pipeline._check_ai_policy = AsyncMock(return_value=False)
    pipeline._check_pr_permissions = AsyncMock(return_value=False)
    pipeline._validate_findings = AsyncMock(return_value=[finding])
    pipeline._check_ci_and_close_if_failed = AsyncMock()
    pipeline._identify_key_files = MagicMock(return_value=[])
    pipeline._review_and_publish = AsyncMock(wraps=pipeline._review_and_publish)
    return pipeline


@pytest.mark.parametrize("path", ["code-scan", "issue-solving"])
@pytest.mark.parametrize(
    "action,expected_publish_count",
    [
        (ReviewDecision.REJECT, 0),
        (ReviewDecision.SKIP, 0),
        ("unknown", 0),
        (ReviewDecision.APPROVE, 1),
    ],
)
@pytest.mark.asyncio
async def test_real_pipeline_paths_share_review_gate_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    action: str,
    expected_publish_count: int,
    repo: Repository,
    finding: Finding,
    contribution: Contribution,
) -> None:
    pipeline = _runtime_pipeline(repo, finding, contribution, action)
    monkeypatch.setattr(
        pipeline_module,
        "fetch_repo_guidelines",
        AsyncMock(return_value=RepoGuidelines()),
    )

    if path == "code-scan":
        result = await pipeline._process_repo(repo, dry_run=False, max_prs=1)
    else:
        solver = AsyncMock()
        solver.fetch_solvable_issues.return_value = [Issue(number=41, title="Fix dead branch")]
        solver.solve_issue_deep.return_value = [finding]
        monkeypatch.setattr(pipeline_module, "IssueSolver", lambda **kwargs: solver)
        result = await pipeline._process_repo_issues(repo, dry_run=False, max_prs=1)

    pipeline._review_and_publish.assert_awaited_once()
    assert pipeline._pr_manager.create_pr.await_count == expected_publish_count
    assert result.prs_created == expected_publish_count


@pytest.mark.asyncio
async def test_missing_persisted_issue_provenance_denies_legacy_close_before_lookup(
    repo: Repository,
) -> None:
    pipeline = ContribPipeline(MagicMock())
    pipeline._github = AsyncMock()
    pipeline._github.close_issue = AsyncMock()

    await pipeline._close_linked_issues(repo, 17)

    pipeline._github._get.assert_not_awaited()
    pipeline._github.close_issue.assert_not_awaited()
