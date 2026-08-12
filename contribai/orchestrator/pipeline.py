"""Main pipeline orchestrator.

Coordinates the full contribution flow:
discover → analyze → generate → PR.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from contribai.agents.registry import create_default_registry
from contribai.analysis.analyzer import CodeAnalyzer
from contribai.analysis.repo_intel import RepoIntelligence, RepoProfile
from contribai.core.config import ContribAIConfig
from contribai.core.events import Event, EventBus, EventType, FileEventLogger
from contribai.core.middleware import build_default_chain
from contribai.core.models import (
    AnalysisResult,
    DiscoveryCriteria,
    Issue,
    PRResult,
    Repository,
)
from contribai.core.quotas import AsyncPRQuota
from contribai.generator.engine import ContributionGenerator
from contribai.github.client import GitHubClient
from contribai.github.discovery import RepoDiscovery
from contribai.github.guidelines import fetch_repo_guidelines  # noqa: F401
from contribai.issues.solver import IssueSolver
from contribai.llm.provider import create_llm_provider
from contribai.opportunity.engine import OpportunityEngine, OpportunitySource
from contribai.orchestrator.memory import Memory
from contribai.orchestrator.pipeline_core import PipelineContext
from contribai.orchestrator.review_gate import HumanReviewer, ReviewGate
from contribai.pr.manager import PRManager
from contribai.publishing.permit import PublishSideEffect
from contribai.tools.protocol import create_default_tools

logger = logging.getLogger(__name__)
_DEFAULT_ISSUE_SOLVER = IssueSolver

# Layer C: constants and `_titles_similar` moved to
# :mod:`contribai.orchestrator.pipeline_constants`. They are re-imported
# below for back-compat with tests that import them from this module.
from contribai.orchestrator.pipeline_constants import (  # noqa: E402,F401
    PROTECTED_META_FILES,
    SKIP_DIRECTORIES,
    SKIP_EXTENSIONS,
    _titles_similar,
)


@dataclass
class PipelineResult:
    """Result of a pipeline run."""

    repos_analyzed: int = 0
    findings_total: int = 0
    contributions_generated: int = 0
    prs_created: int = 0
    prs: list[PRResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ContribPipeline:
    """Main orchestrator for the contribution pipeline."""

    def __init__(self, config: ContribAIConfig):
        self.config = config
        self._github: GitHubClient | None = None
        self._llm = None
        self._memory: Memory | None = None
        self._analyzer: CodeAnalyzer | None = None
        self._generator: ContributionGenerator | None = None
        self._pr_manager: PRManager | None = None
        self._discovery: RepoDiscovery | None = None
        self._middleware_chain: list = []
        self._agent_registry = None
        self._tool_registry = None
        self._reviewer: HumanReviewer | None = None
        self._review_gate: ReviewGate | None = None
        self._event_bus: EventBus = EventBus()
        self._repo_intel: RepoIntelligence | None = None
        self._opportunity_engine = OpportunityEngine()
        self._controlled_publish: Callable[..., Awaitable[PRResult | None]] | None = None

    async def _init_components(self):
        """Initialize all pipeline components."""
        # LLM — with optional multi-model routing
        mm = self.config.multi_model
        self._llm = create_llm_provider(
            self.config.llm,
            multi_model=mm.enabled,
            strategy=mm.strategy,
        )

        # GitHub
        self._github = GitHubClient(
            token=self.config.github.token,
            rate_limit_buffer=self.config.github.rate_limit_buffer,
        )

        # Memory
        self._memory = Memory(self.config.storage.resolved_db_path)
        await self._memory.init()

        # Analyzer
        # ── Layer B: discover plugins (AnalyzerPlugin instances) and merge
        # them into the analyzer. ``discover()`` is a no-op the second time
        # around; we call it explicitly so the log shows the loaded count.
        from contribai.plugins import discover as discover_plugins

        plugin_registry = discover_plugins()
        self._analyzer = CodeAnalyzer(
            llm=self._llm,
            github=self._github,
            config=self.config.analysis,
            plugin_analyzers=plugin_registry.analyzers,
        )
        if plugin_registry.analyzers:
            logger.info(
                "🔌 Merged %d plugin analyzer(s) into pipeline",
                len(plugin_registry.analyzers),
            )

        # Generator — now with memory for repo_preferences
        self._generator = ContributionGenerator(
            llm=self._llm,
            config=self.config.contribution,
            memory=self._memory,
        )

        # PR Manager
        self._pr_manager = PRManager(github=self._github)

        # Discovery
        self._discovery = RepoDiscovery(
            client=self._github,
            config=self.config.discovery,
        )

        # Middleware chain (DeerFlow pattern)
        self._middleware_chain = build_default_chain(
            max_prs_per_day=self.config.github.max_prs_per_day,
            max_retries=self.config.pipeline.max_retries,
            min_quality_score=self.config.pipeline.min_quality_score,
        )
        logger.info("Middleware chain: %d middlewares loaded", len(self._middleware_chain))

        # Agent registry (DeerFlow pattern)
        self._agent_registry = create_default_registry()
        logger.info(
            "Agent registry: %d agents loaded",
            len(self._agent_registry.list_agents()),
        )

        # Tool registry (DeerFlow pattern)
        self._tool_registry = create_default_tools(
            github_client=self._github,
            llm_provider=self._llm,
        )
        logger.info(
            "Tool registry: %d tools loaded",
            len(self._tool_registry.list_tools()),
        )

        # Repo Intelligence (v4.0)
        self._repo_intel = RepoIntelligence(github=self._github)
        logger.info("🧠 Repo Intelligence: enabled")

        # Human review gate
        if self.config.pipeline.human_review:
            self._reviewer = HumanReviewer()
            logger.info("🔍 Human review gate: ENABLED")
        else:
            self._reviewer = HumanReviewer(auto_approve=True)
            logger.debug("Human review gate: disabled (auto-approve)")
        self._review_gate = ReviewGate(
            self._reviewer,
            explicit_human_review=self.config.pipeline.human_review,
        )

        # Event bus + file logger for observability
        from pathlib import Path

        events_path = Path(self.config.storage.resolved_db_path).parent / "events.jsonl"
        file_logger = FileEventLogger(events_path)
        self._event_bus.subscribe_all(file_logger.handle)
        logger.info("📡 EventBus: logging to %s", events_path)

    async def _cleanup(self):
        """Clean up resources."""
        if self._github:
            await self._github.close()
        if self._llm:
            await self._llm.close()
        if self._memory:
            await self._memory.close()

    # ── Public API ─────────────────────────────────────────────────────────

    async def run(
        self,
        criteria: DiscoveryCriteria | None = None,
        dry_run: bool = False,
        publish_handler_factory: Callable[
            [ContribPipeline], Callable[..., Awaitable[PRResult | None]]
        ]
        | None = None,
    ) -> PipelineResult:
        """Run the full pipeline: discover -> analyze -> generate -> PR.

        Processes multiple repos in parallel using asyncio.Semaphore.

        Args:
            criteria: Optional custom discovery criteria
            dry_run: If True, analyze and generate but don't create PRs
        """
        await self._init_components()
        if publish_handler_factory is not None:
            self._controlled_publish = publish_handler_factory(self)
        result = PipelineResult()
        run_id = await self._memory.start_run()
        await self._event_bus.emit(
            Event(type=EventType.PIPELINE_START, source="pipeline.run", data={"dry_run": dry_run})
        )

        try:
            # Check daily PR limit
            today_prs = await self._memory.get_today_pr_count()
            remaining_prs = self.config.github.max_prs_per_day - today_prs
            if remaining_prs <= 0 and not dry_run:
                logger.warning(
                    "Daily PR limit reached (%d)",
                    self.config.github.max_prs_per_day,
                )
                return result

            # 1. Discover repos
            logger.info("Discovering repositories...")
            repos = await self._discovery.discover(criteria)
            if not repos:
                logger.warning("No repositories found matching criteria")
                return result

            logger.info("Found %d candidate repositories", len(repos))

            pr_quota = None if dry_run else AsyncPRQuota(remaining_prs)

            # Limit to max repos per run
            repos = repos[: self.config.github.max_repos_per_run]

            # 2. Process repos in parallel with semaphore
            max_conc = self.config.pipeline.max_concurrent_repos
            sem = asyncio.Semaphore(max_conc)
            logger.info(
                "Processing %d repos (max %d concurrent)",
                len(repos),
                max_conc,
            )

            async def _guarded(
                repo: Repository,
            ) -> PipelineResult | None:
                async with sem:
                    if await self._memory.has_analyzed(repo.full_name):
                        logger.info(
                            "Skipping %s (already analyzed)",
                            repo.full_name,
                        )
                        return None
                    try:
                        return await self._process_repo(
                            repo,
                            dry_run,
                            remaining_prs,
                            pr_quota=pr_quota,
                        )
                    except Exception as e:
                        msg = f"Error processing {repo.full_name}: {e}"
                        logger.error(msg)
                        err = PipelineResult()
                        err.errors.append(msg)
                        return err

            repo_results = await asyncio.gather(*[_guarded(r) for r in repos])

            # Aggregate results
            for rr in repo_results:
                if rr is None:
                    continue
                result.repos_analyzed += 1
                result.findings_total += rr.findings_total
                result.contributions_generated += rr.contributions_generated
                result.prs_created += rr.prs_created
                result.prs.extend(rr.prs)
                result.errors.extend(rr.errors)

            # Log run
            await self._memory.finish_run(
                run_id,
                repos_analyzed=result.repos_analyzed,
                prs_created=result.prs_created,
                findings=result.findings_total,
                errors=len(result.errors),
            )

        finally:
            self._controlled_publish = None
            await self._event_bus.emit(
                Event(
                    type=EventType.PIPELINE_COMPLETE,
                    source="pipeline.run",
                    data={
                        "repos": result.repos_analyzed,
                        "prs": result.prs_created,
                        "findings": result.findings_total,
                        "errors": len(result.errors),
                    },
                )
            )
            await self._cleanup()

        return result

    async def hunt(
        self,
        *,
        rounds: int = 5,
        delay_sec: int = 30,
        dry_run: bool = False,
        mode: str = "both",
    ) -> PipelineResult:
        """Hunt mode: aggressively discover and contribute to repos.

        Runs multiple discovery rounds with varied criteria.
        For each round:
        1. Discover repos (varied star range, shuffled languages)
        2. Filter to repos that actually merge external PRs
        3. Process each repo through the full pipeline
        4. Wait between rounds to avoid rate limits

        Args:
            rounds: Number of discovery rounds
            delay_sec: Delay between rounds
            dry_run: If True, don't create PRs
            mode: 'analysis' (code scan), 'issues' (issue solving), 'both'
        """
        import random

        await self._init_components()
        total = PipelineResult()

        cfg_min, cfg_max = self.config.discovery.stars_range
        star_tiers = [
            (cfg_min, cfg_max),
            (100, 1000),
            (1000, 5000),
            (5000, 20000),
            (500, 3000),
        ]
        langs = list(self.config.discovery.languages)
        # v4.0: Multi-language expansion — add extra languages for broader reach
        all_languages = list(set([*langs, "javascript", "typescript", "go", "rust"]))

        try:
            for rnd in range(1, rounds + 1):
                today_prs = await self._memory.get_today_pr_count()
                remaining = self.config.github.max_prs_per_day - today_prs
                if remaining <= 0 and not dry_run:
                    logger.warning(
                        "🛑 Daily PR limit reached (%d). Stopping.",
                        self.config.github.max_prs_per_day,
                    )
                    break

                # v4.0: Multi-language — rotate through all languages
                hunt_langs = all_languages if rnd % 2 == 0 else langs
                random.shuffle(hunt_langs)
                stars = star_tiers[(rnd - 1) % len(star_tiers)]
                criteria = DiscoveryCriteria(
                    languages=hunt_langs[:2],
                    stars_min=stars[0],
                    stars_max=stars[1],
                    min_last_activity_days=7,
                    max_results=10,
                )

                logger.info(
                    "🔥 Hunt round %d/%d — %s, ★ %d-%d",
                    rnd,
                    rounds,
                    "/".join(hunt_langs[:2]),
                    stars[0],
                    stars[1],
                )
                await self._event_bus.emit(
                    Event(
                        type=EventType.HUNT_ROUND_START,
                        source="pipeline.hunt",
                        data={"round": rnd, "total": rounds, "stars": list(stars)},
                    )
                )

                repos = await self._discovery.discover(criteria)
                if not repos:
                    logger.info("No repos found this round")
                    if rnd < rounds:
                        await asyncio.sleep(delay_sec)
                    continue

                # Filter to merge-friendly repos
                targets: list[Repository] = []
                for repo in repos[:5]:
                    if await self._memory.has_analyzed(repo.full_name):
                        continue
                    try:
                        prs = await self._github.list_pull_requests(
                            repo.owner,
                            repo.name,
                            state="closed",
                            per_page=10,
                        )
                        merged = [p for p in prs if p.get("merged_at")]
                        if merged:
                            logger.info(
                                "✅ %s — %d merged PRs, good target!",
                                repo.full_name,
                                len(merged),
                            )
                            targets.append(repo)
                    except Exception:
                        pass

                if not targets:
                    logger.info("No merge-friendly repos this round")
                    if rnd < rounds:
                        await asyncio.sleep(delay_sec)
                    continue

                max_targets = self.config.github.max_repos_per_run
                max_conc = self.config.pipeline.max_concurrent_repos
                sem = asyncio.Semaphore(max_conc)
                selected = targets[:max_targets]

                logger.info(
                    "Processing %d repos (max %d concurrent)",
                    len(selected),
                    max_conc,
                )

                # Process repos sequentially with inter-repo delay
                # to avoid RESOURCE_EXHAUSTED rate limits
                delay_between = self.config.pipeline.inter_repo_delay_sec
                for i, repo in enumerate(selected):
                    if remaining <= 0 and not dry_run:
                        logger.warning("PR limit reached mid-round")
                        break
                    rr = await self._hunt_process_repo(repo, mode, dry_run, remaining, sem)
                    total.repos_analyzed += rr.repos_analyzed
                    total.findings_total += rr.findings_total
                    total.contributions_generated += rr.contributions_generated
                    total.prs_created += rr.prs_created
                    total.prs.extend(rr.prs)
                    total.errors.extend(rr.errors)
                    remaining -= rr.prs_created

                    # Inter-repo delay (skip after last repo)
                    if i < len(selected) - 1 and delay_between > 0:
                        logger.debug("⏳ Inter-repo delay: %.1fs", delay_between)
                        await asyncio.sleep(delay_between)

                # ── v4.0: Issue-First Strategy ──────────────────────────────
                # On odd rounds, also search for high-value issues globally
                if mode in ("issues", "both") and rnd % 2 == 1:
                    try:
                        issue_results = await self._hunt_issues_globally(
                            languages=hunt_langs[:2],
                            dry_run=dry_run,
                            max_issues=3,
                        )
                        total.findings_total += issue_results.findings_total
                        total.contributions_generated += issue_results.contributions_generated
                        total.prs_created += issue_results.prs_created
                        total.prs.extend(issue_results.prs)
                        remaining -= issue_results.prs_created
                    except Exception as e:
                        logger.debug("Issue-first hunt failed: %s", e)

                if rnd < rounds:
                    logger.info(
                        "⏳ Waiting %ds before next round...",
                        delay_sec,
                    )
                    await asyncio.sleep(delay_sec)

        finally:
            await self._cleanup()

        return total

    async def _hunt_process_repo(
        self,
        repo: Repository,
        mode: str,
        dry_run: bool,
        remaining: int,
        sem: asyncio.Semaphore,
    ) -> PipelineResult:
        """Process a single repo in hunt mode (used for parallel execution)."""
        async with sem:
            rr = PipelineResult()
            try:
                if mode in ("analysis", "both"):
                    analysis_rr = await self._process_repo(repo, dry_run, remaining)
                    rr.repos_analyzed += analysis_rr.repos_analyzed
                    rr.findings_total += analysis_rr.findings_total
                    rr.contributions_generated += analysis_rr.contributions_generated
                    rr.prs_created += analysis_rr.prs_created
                    rr.prs.extend(analysis_rr.prs)

                if mode in ("issues", "both"):
                    issue_rr = await self._process_repo_issues(
                        repo, dry_run, remaining - rr.prs_created
                    )
                    rr.repos_analyzed = max(rr.repos_analyzed, issue_rr.repos_analyzed)
                    rr.findings_total += issue_rr.findings_total
                    rr.contributions_generated += issue_rr.contributions_generated
                    rr.prs_created += issue_rr.prs_created
                    rr.prs.extend(issue_rr.prs)

                rr.repos_analyzed = max(rr.repos_analyzed, 1)
            except Exception as e:
                rr.errors.append(f"{repo.full_name}: {e}")
                logger.error("Error processing %s: %s", repo.full_name, e)
            return rr

    async def _hunt_issues_globally(
        self,
        languages: list[str],
        dry_run: bool = False,
        max_issues: int = 5,
    ) -> PipelineResult:
        """v4.0: Issue-First Strategy — search GitHub for high-value issues.

        Searches for repos with 'good first issue' or 'help wanted' labels,
        then solves those issues for higher merge rate.

        Args:
            languages: Programming languages to filter by.
            dry_run: If True, don't create PRs.
            max_issues: Maximum issues to process.

        Returns:
            PipelineResult with issue-solving results.
        """
        result = PipelineResult()
        logger.info("🎯 Issue-First: searching for high-value issues...")

        for lang in languages[:2]:
            for label in ["good first issue", "help wanted", "bug"]:
                try:
                    query = f'label:"{label}" language:{lang} state:open stars:>100 archived:false'
                    issues = await self._github.search_issues(query, sort="created", per_page=10)

                    for issue_data in issues[:max_issues]:
                        repo_url = issue_data.get("repository_url", "")
                        if not repo_url:
                            continue

                        # Extract owner/repo from URL
                        parts = repo_url.rstrip("/").split("/")
                        if len(parts) < 2:
                            continue
                        owner, repo_name = parts[-2], parts[-1]
                        full_name = f"{owner}/{repo_name}"

                        # Skip if already analyzed
                        if await self._memory.has_analyzed(full_name):
                            continue

                        # Skip if we already have an active PR
                        past_prs = await self._memory.get_repo_prs(full_name)
                        active = [p for p in past_prs if p.get("status") == "open"]
                        if active:
                            continue

                        logger.info(
                            "🎯 Found issue #%d in %s: %s [%s]",
                            issue_data.get("number", 0),
                            full_name,
                            issue_data.get("title", "?"),
                            label,
                        )

                        # Process the repo in issue mode
                        try:
                            repo = await self._github.get_repo_details(owner, repo_name)
                            sem = asyncio.Semaphore(1)
                            rr = await self._hunt_process_repo(
                                repo, "issues", dry_run, max_issues, sem
                            )
                            result.repos_analyzed += rr.repos_analyzed
                            result.findings_total += rr.findings_total
                            result.contributions_generated += rr.contributions_generated
                            result.prs_created += rr.prs_created
                            result.prs.extend(rr.prs)

                            if result.prs_created >= max_issues:
                                return result
                        except Exception as e:
                            logger.debug(
                                "Failed to process %s for issue: %s",
                                full_name,
                                e,
                            )
                except Exception as e:
                    logger.debug("Issue search failed for %s/%s: %s", lang, label, e)

        return result

    async def run_single(
        self,
        repo_url: str,
        dry_run: bool = False,
    ) -> PipelineResult:
        """Run the pipeline on a single specific repo.

        Args:
            repo_url: GitHub repository URL (e.g., https://github.com/owner/repo)
            dry_run: If True, analyze and generate but don't create PRs
        """
        # Parse URL
        parts = repo_url.rstrip("/").split("/")
        owner, name = parts[-2], parts[-1]

        await self._init_components()
        result = PipelineResult()

        try:
            repo = await self._github.get_repo_details(owner, name)
            repo_result = await self._process_repo(repo, dry_run)
            result.repos_analyzed = 1
            result.findings_total = repo_result.findings_total
            result.contributions_generated = repo_result.contributions_generated
            result.prs_created = repo_result.prs_created
            result.prs = repo_result.prs
        except Exception as e:
            result.errors.append(str(e))
            logger.error("Failed: %s", e)
        finally:
            await self._cleanup()

        return result

    async def run_controlled(
        self,
        repo_url: str,
        *,
        publish_handler_factory: Callable[
            [ContribPipeline], Callable[..., Awaitable[PRResult | None]]
        ],
        issue_number: int | None = None,
        max_prs: int = 1,
    ) -> PipelineResult:
        """Run one repository through the permit-bearing live publish path.

        The factory is invoked only after all pipeline collaborators are
        initialized, allowing a handler to share the read-only GitHub client
        and durable Memory connection while retaining the publisher as the
        sole GitHub write authority.
        """
        if max_prs <= 0:
            raise ValueError("max_prs must be positive")
        parts = repo_url.rstrip("/").split("/")
        if len(parts) < 2:
            raise ValueError("repo_url must identify owner/name")
        owner, name = parts[-2], parts[-1]

        await self._init_components()
        self._controlled_publish = publish_handler_factory(self)
        try:
            repo = await self._github.get_repo_details(owner, name)
            if issue_number is None:
                repo_result = await self._process_repo(
                    repo,
                    dry_run=False,
                    max_prs=max_prs,
                    pr_quota=AsyncPRQuota(max_prs),
                )
            else:
                repo_result = await self._process_repo_issues(
                    repo,
                    dry_run=False,
                    max_prs=max_prs,
                    issue_number=issue_number,
                    pr_quota=AsyncPRQuota(max_prs),
                )
            return repo_result
        finally:
            self._controlled_publish = None
            await self._cleanup()

    async def analyze_only(self, repo_url: str) -> AnalysisResult | None:
        """Analyze a repo without generating contributions or PRs."""
        parts = repo_url.rstrip("/").split("/")
        owner, name = parts[-2], parts[-1]

        await self._init_components()
        try:
            repo = await self._github.get_repo_details(owner, name)
            return await self._analyzer.analyze(repo)
        finally:
            await self._cleanup()

    # ── Internal ───────────────────────────────────────────────────────────

    def _build_pipeline_context(
        self,
        *,
        solver: IssueSolver | None = None,
        pr_quota: AsyncPRQuota | None = None,
    ) -> PipelineContext:
        """Freeze the live collaborators into a :class:`PipelineContext`.

        The :class:`Pipeline` conductor treats ``ctx`` as immutable and
        threads it through every step.
        """
        from contribai.orchestrator.pipeline_core import PipelineContext

        return PipelineContext(
            github=self._github,
            llm=self._llm,
            memory=self._memory,
            analyzer=self._analyzer,
            generator=self._generator,
            pr_manager=self._pr_manager,
            reviewer=self._reviewer,
            repo_intel=self._repo_intel,
            event_bus=self._event_bus,
            config=self.config,
            review_and_publish=(
                self._controlled_publish
                or (self._review_and_publish if self._review_gate is not None else None)
            ),
            check_ci=self._check_ci_and_close_if_failed,
            solver=solver,
            pr_quota=pr_quota,
            controlled_publish=self._controlled_publish is not None,
        )

    async def _review_and_publish(
        self,
        contribution,
        finding,
        repo: Repository,
        guidelines,
        *,
        closes_issue: int | None = None,
    ) -> PRResult | None:
        """Apply the single human-review boundary before legacy publishing."""
        planned_side_effects = [PublishSideEffect.CREATE_PR]
        if closes_issue is None and guidelines.requires_issue_link:
            planned_side_effects.insert(0, PublishSideEffect.CREATE_ISSUE)

        decision = await self._review_gate.review(
            contribution,
            finding,
            repo.full_name,
            planned_side_effects=tuple(planned_side_effects),
        )
        if decision.approved is not True:
            logger.info(
                "Review did not approve %s (decision=%s)",
                contribution.title,
                decision.action,
            )
            return None

        return await self._pr_manager.create_pr(
            contribution,
            repo,
            guidelines=guidelines,
            closes_issue=closes_issue,
        )

    async def _close_linked_issues(
        self,
        repo: Repository,
        pr_number: int,
        *,
        reason: str = "PR was closed",
    ) -> None:
        """Deny issue closing when no permit-bearing publisher command exists."""
        logger.warning(
            "No linked issue was closed for %s PR #%d (%s): a publisher permit is required",
            repo.full_name,
            pr_number,
            reason,
        )

    async def _check_ci_and_close_if_failed(
        self,
        pr_result: PRResult,
        repo: Repository,
        *,
        max_wait_sec: int = 90,
        poll_interval: int = 15,
    ) -> None:
        """Delegate CI monitoring while retaining the instance compatibility seam."""
        from contribai.orchestrator.steps import _check_ci_and_close_if_failed

        await _check_ci_and_close_if_failed(
            self._github,
            self._memory,
            repo,
            pr_result,
            max_wait_sec=max_wait_sec,
            poll_interval=poll_interval,
        )

    async def _process_repo(
        self,
        repo: Repository,
        dry_run: bool,
        max_prs: int = 5,
        *,
        pr_quota: AsyncPRQuota | None = None,
    ) -> PipelineResult:
        """Process a single repository through the analysis pipeline (Layer C).

        Thin orchestrator: builds :class:`PipelineState`, runs the 5-step
        analysis pipeline, returns ``state.result``.
        """
        from contribai.orchestrator.pipeline_core import Pipeline, PipelineState
        from contribai.orchestrator.steps import (
            generate_contribution_step,
            load_repo_context_step,
            run_analysis_step,
            submit_pr_step,
            validate_findings_step,
        )

        logger.info("=" * 60)
        logger.info("📦 Processing: %s", repo.full_name)

        state = PipelineState(repo=repo, dry_run=dry_run, max_prs=max_prs)
        ctx = self._build_pipeline_context(pr_quota=pr_quota)

        analysis_pipeline = Pipeline(
            [
                load_repo_context_step,
                run_analysis_step,
                validate_findings_step,
                generate_contribution_step,
                submit_pr_step,
            ],
            label="analysis",
        )
        return (await analysis_pipeline.run(state, ctx)).result

    async def _process_repo_issues(
        self,
        repo: Repository,
        dry_run: bool,
        max_prs: int = 3,
        *,
        issue_number: int | None = None,
        pr_quota: AsyncPRQuota | None = None,
    ) -> PipelineResult:
        """Process a repo by solving its open Issues (Layer C).

        Issue-mode pipeline reuses ``load_repo_context_step``,
        ``generate_contribution_step``, and ``submit_pr_step`` (3/5). The
        two issue-specific steps are :func:`solve_issue_step` and
        :func:`validate_issue_findings_step`.
        """
        from contribai.orchestrator.pipeline_core import Pipeline, PipelineState
        from contribai.orchestrator.steps import (
            generate_contribution_step,
            load_repo_context_step,
            solve_issue_step,
            submit_pr_step,
            validate_issue_findings_step,
        )

        logger.info("📋 Looking for solvable issues in %s...", repo.full_name)

        state = PipelineState(
            repo=repo,
            dry_run=dry_run,
            max_prs=max_prs,
            issue_number=issue_number,
        )
        solver_factory = IssueSolver
        if solver_factory is _DEFAULT_ISSUE_SOLVER:
            from contribai.issues import solver as solver_module

            solver_factory = solver_module.IssueSolver
        solver = solver_factory(llm=self._llm, github=self._github)
        ctx = self._build_pipeline_context(solver=solver, pr_quota=pr_quota)

        issue_pipeline = Pipeline(
            [
                load_repo_context_step,
                solve_issue_step,
                validate_issue_findings_step,
                generate_contribution_step,
                submit_pr_step,
            ],
            label="issue",
        )
        return (await issue_pipeline.run(state, ctx)).result

    async def _rank_issue_opportunities(
        self, repo: Repository, issues: list[Issue], max_candidates: int
    ) -> list[Issue]:
        """Rank issue candidates and persist their read-only score evidence."""
        profile: RepoProfile | None = None
        if self._repo_intel is not None:
            try:
                profile = await self._repo_intel.profile(repo.owner, repo.name)
            except Exception as exc:
                logger.debug(
                    "Opportunity repo profile failed for %s: %s",
                    repo.full_name,
                    exc,
                )

        outcomes = []
        get_outcomes = getattr(self._memory, "get_contribution_outcomes", None)
        if get_outcomes is not None:
            try:
                loaded_outcomes = await get_outcomes(repo.full_name)
                if isinstance(loaded_outcomes, list):
                    outcomes = loaded_outcomes
            except Exception as exc:
                logger.debug(
                    "Opportunity outcome learning unavailable for %s: %s",
                    repo.full_name,
                    exc,
                )

        candidates = self._opportunity_engine.rank(
            repo,
            issues=issues,
            profile=profile,
            max_candidates=max_candidates,
            outcomes=outcomes,
        )
        issue_candidates = [
            candidate for candidate in candidates if candidate.source is OpportunitySource.ISSUE
        ]

        record_score = getattr(self._memory, "record_opportunity_score", None)
        if record_score is not None:
            for candidate in issue_candidates:
                try:
                    await record_score(candidate)
                except Exception as exc:
                    logger.debug(
                        "Could not persist opportunity score for %s #%s: %s",
                        repo.full_name,
                        candidate.issue_number,
                        exc,
                    )

        return [
            candidate.task for candidate in issue_candidates if isinstance(candidate.task, Issue)
        ]

    def _identify_key_files(self, file_tree: list, repo: Repository) -> list[str]:
        """Back-compat shim — calls :func:`contribai.orchestrator.steps.identify_key_files`.

        Kept so that ``tests/unit/test_pipeline_v2.py::TestIdentifyKeyFiles``
        (which calls ``sample_pipeline._identify_key_files(...)``) keeps
        passing without modification.
        """
        from contribai.orchestrator.steps import identify_key_files

        return identify_key_files(file_tree, repo)

    def _set_task(self, task_name: str) -> None:
        """Set the current task context for multi-model routing.

        Legacy entry point — :class:`PipelineContext.set_task` is the
        preferred API for new code; this method remains so existing
        callers (e.g. ``_process_repo_issues`` shims) keep working.
        """
        from contribai.llm.provider import MultiModelProvider

        if isinstance(self._llm, MultiModelProvider):
            import contextlib

            from contribai.llm.models import TaskType

            with contextlib.suppress(ValueError):
                self._llm.set_task(TaskType(task_name))
