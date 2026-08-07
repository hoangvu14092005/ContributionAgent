"""Integration tests for the orchestrator pipeline (Layer C).

These tests exercise the full ``ContribPipeline._process_repo`` and
``_process_repo_issues`` orchestrators against the new step-based
implementation. They mirror the structure of
``tests/integration/test_pipeline.py`` but specifically validate:

- Analysis mode happy path (analyzer → validate → generate → submit)
- Analysis mode dry-run (no PR submission)
- Issue mode happy path (solve_issue → validate_issue → generate → submit)
- AI policy short-circuit (early exit before analysis)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from contribai.core.config import (
    ContribAIConfig,
    GitHubConfig,
    LLMConfig,
    StorageConfig,
)
from contribai.core.models import (
    AnalysisResult,
    Contribution,
    ContributionType,
    FileChange,
    FileNode,
    Finding,
    Issue,
    PRResult,
    Repository,
    Severity,
)
from contribai.orchestrator.pipeline import ContribPipeline


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def pipeline_config(tmp_path):
    return ContribAIConfig(
        github=GitHubConfig(token="ghp_test", max_repos_per_run=1, max_prs_per_day=5),
        llm=LLMConfig(provider="gemini", api_key="test_key"),
        storage=StorageConfig(db_path=str(tmp_path / "test.db")),
    )


@pytest.fixture
def mock_repo():
    return Repository(
        owner="testorg",
        name="testrepo",
        full_name="testorg/testrepo",
        description="Test repo for integration",
        language="python",
        stars=500,
        forks=50,
        open_issues=10,
        default_branch="main",
        has_license=True,
    )


@pytest.fixture
def sample_finding():
    return Finding(
        type=ContributionType.SECURITY_FIX,
        severity=Severity.HIGH,
        title="SQL injection",
        description="Parameterize queries",
        file_path="main.py",
    )


@pytest.fixture
def sample_contribution(sample_finding):
    return Contribution(
        finding=sample_finding,
        contribution_type=ContributionType.SECURITY_FIX,
        title="Parameterize queries",
        description="Use parameterized queries",
        changes=[FileChange(path="main.py", new_content="# safe")],
        commit_message="fix: parameterize queries",
        branch_name="fix/sql",
    )


def _stub_collaborators(
    pipeline: ContribPipeline,
    repo: Repository,
    sample_finding: Finding,
    sample_contribution: Contribution,
) -> "Memory":
    """Wire AsyncMock collaborators onto a pipeline instance."""
    pipeline._github = AsyncMock()
    pipeline._github.close = AsyncMock()
    pipeline._github.get_file_tree = AsyncMock(
        return_value=[FileNode(path="main.py", type="blob", size=500, sha="abc")]
    )
    pipeline._github.get_file_content = AsyncMock(return_value="print('hello')")
    pipeline._github.list_pull_requests = AsyncMock(return_value=[])
    pipeline._github.get_authenticated_user = AsyncMock(return_value={"login": "test"})
    pipeline._github._get = AsyncMock(side_effect=Exception("not used"))

    pipeline._llm = AsyncMock()
    pipeline._llm.complete = AsyncMock(return_value="VALID: real issue")
    pipeline._llm.close = AsyncMock()

    pipeline._analyzer = AsyncMock()
    pipeline._analyzer.analyze = AsyncMock(
        return_value=AnalysisResult(
            repo=repo,
            findings=[sample_finding],
            analyzed_files=1,
            analysis_duration_sec=0.5,
        )
    )

    pipeline._generator = AsyncMock()
    pipeline._generator.generate = AsyncMock(return_value=sample_contribution)

    pipeline._pr_manager = AsyncMock()
    pipeline._pr_manager.create_pr = AsyncMock(
        return_value=PRResult(
            repo=repo,
            contribution=sample_contribution,
            pr_number=42,
            pr_url="https://github.com/test/pr/42",
            branch_name="fix/sql",
            fork_full_name="bot/testrepo",
        )
    )

    pipeline._reviewer = AsyncMock()
    from contribai.orchestrator.review_gate import ReviewDecision

    pipeline._reviewer.review = AsyncMock(
        return_value=ReviewDecision(ReviewDecision.APPROVE)
    )

    pipeline._repo_intel = AsyncMock()
    pipeline._repo_intel.profile = AsyncMock(return_value=None)

    from contribai.orchestrator.memory import Memory

    pipeline._memory = Memory(pipeline.config.storage.resolved_db_path)
    return pipeline._memory


def _approved_decision():
    from contribai.orchestrator.review_gate import ReviewDecision

    return ReviewDecision(ReviewDecision.APPROVE)


# ── Analysis mode ────────────────────────────────────────────────────────────


class TestAnalysisModeE2E:
    @pytest.mark.asyncio
    async def test_full_flow_creates_one_pr(
        self, pipeline_config, mock_repo, sample_finding, sample_contribution
    ):
        """Analysis mode: 1 finding → validate → generate → 1 PR."""
        pipeline = ContribPipeline(pipeline_config)
        pipeline._analyzer = AsyncMock()
        pipeline._analyzer.analyze = AsyncMock(
            return_value=AnalysisResult(
                repo=mock_repo, findings=[sample_finding], analyzed_files=1
            )
        )
        mem = _stub_collaborators(
            pipeline, mock_repo, sample_finding, sample_contribution
        )
        await mem.init()

        with patch.object(pipeline, "_init_components", new=AsyncMock()):
            result = await pipeline._process_repo(mock_repo, dry_run=False, max_prs=5)

        await mem.close()

        assert pipeline._pr_manager.create_pr.await_count == 1
        assert result.prs_created == 1
        assert result.contributions_generated == 1
        assert result.repos_analyzed == 1

    @pytest.mark.asyncio
    async def test_dry_run_skips_pr_creation(
        self, pipeline_config, mock_repo, sample_finding, sample_contribution
    ):
        """Dry-run mode: generator runs but PR submission is skipped."""
        pipeline = ContribPipeline(pipeline_config)
        pipeline._analyzer = AsyncMock()
        pipeline._analyzer.analyze = AsyncMock(
            return_value=AnalysisResult(
                repo=mock_repo, findings=[sample_finding], analyzed_files=1
            )
        )
        mem = _stub_collaborators(
            pipeline, mock_repo, sample_finding, sample_contribution
        )
        await mem.init()

        with patch.object(pipeline, "_init_components", new=AsyncMock()):
            result = await pipeline._process_repo(mock_repo, dry_run=True, max_prs=5)

        await mem.close()

        # Generator ran (state.contributions populated)
        assert pipeline._generator.generate.await_count == 1
        # PR submit did NOT run
        assert pipeline._pr_manager.create_pr.await_count == 0
        assert result.prs_created == 0
        assert result.contributions_generated == 1  # bumped in step 4 even in dry-run
        assert result.repos_analyzed == 1


# ── AI policy short-circuit ──────────────────────────────────────────────────


class TestShortCircuit:
    @pytest.mark.asyncio
    async def test_ai_policy_skip_exits_before_analysis(
        self, pipeline_config, mock_repo, sample_finding, sample_contribution
    ):
        """AI policy skip → state.skip_reason='ai_policy' → no analysis."""
        pipeline = ContribPipeline(pipeline_config)
        pipeline._analyzer = AsyncMock()
        pipeline._analyzer.analyze = AsyncMock()
        mem = _stub_collaborators(
            pipeline, mock_repo, sample_finding, sample_contribution
        )
        await mem.init()

        # Patch `_check_ai_policy` inside `steps.py` to return True (ban).
        from contribai.orchestrator import steps as steps_mod

        original = steps_mod._check_ai_policy
        steps_mod._check_ai_policy = AsyncMock(return_value=True)
        try:
            with patch.object(pipeline, "_init_components", new=AsyncMock()):
                result = await pipeline._process_repo(mock_repo, dry_run=False)
        finally:
            steps_mod._check_ai_policy = original
        await mem.close()

        # Analyzer never called
        assert pipeline._analyzer.analyze.await_count == 0
        assert result.repos_analyzed == 1
        assert result.contributions_generated == 0
        assert result.prs_created == 0


# ── Issue mode ───────────────────────────────────────────────────────────────


class TestIssueModeE2E:
    @pytest.mark.asyncio
    async def test_issue_flow_creates_pr_with_closes_issue(
        self, pipeline_config, mock_repo, sample_finding, sample_contribution
    ):
        """Issue mode: 1 issue → solve → generate → 1 PR with closes_issue."""
        pipeline = ContribPipeline(pipeline_config)

        # Stubs
        pipeline._analyzer = AsyncMock()
        mem = _stub_collaborators(
            pipeline, mock_repo, sample_finding, sample_contribution
        )
        await mem.init()

        # Patch IssueSolver so we don't import the real one
        fake_solve_result = [sample_finding]
        fake_issues = [
            Issue(
                number=7,
                title="Bug",
                body="Steps to reproduce...",
                labels=["bug"],
                state="open",
            )
        ]
        fake_solver = AsyncMock()
        fake_solver.fetch_solvable_issues = AsyncMock(return_value=fake_issues)
        fake_solver.solve_issue_deep = AsyncMock(return_value=fake_solve_result)

        with patch.object(pipeline, "_init_components", new=AsyncMock()), \
             patch(
                 "contribai.issues.solver.IssueSolver",
                 return_value=fake_solver,
             ):
            result = await pipeline._process_repo_issues(mock_repo, dry_run=False, max_prs=1)

        await mem.close()

        # PR created with `closes_issue=7`
        assert pipeline._pr_manager.create_pr.await_count == 1
        kwargs = pipeline._pr_manager.create_pr.await_args.kwargs
        assert kwargs.get("closes_issue") == 7
        assert result.prs_created == 1