"""Haystack-style pipeline core for ContribAI.

Layer C extracted this from `contribai/orchestrator/pipeline.py`. The
:class:`Pipeline` class chains step functions into a deterministic flow
without depending on the real Haystack library.

Design notes:

- :class:`PipelineState` is a dataclass that travels through every step.
  Steps mutate the state in place (no copies), so ordering matters.
- :class:`PipelineContext` is a frozen dataclass holding collaborator
  references. Steps are pure functions over ``(ctx, state)``.
- The conductor checks ``state.skip_reason`` after every step. Setting it
  short-circuits the rest of the pipeline — see
  :data:`contribai.orchestrator.pipeline_constants.SkipReason` for valid
  reasons.

Adding a new pipeline (e.g. for issue-driven mode): compose the steps and
call :meth:`Pipeline.run`. Adding a new step: write ``async def step(ctx,
state) -> None``, append to a list, and pass to :class:`Pipeline`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from contribai.orchestrator.pipeline_constants import SkipReason

if TYPE_CHECKING:
    from contribai.analysis.analyzer import CodeAnalyzer
    from contribai.analysis.repo_intel import RepoIntelligence, RepoProfile
    from contribai.core.config import ContribAIConfig
    from contribai.core.events import EventBus
    from contribai.core.models import (
        AnalysisResult,
        Contribution,
        FileNode,
        Finding,
        PRResult,
        RepoContext,
        Repository,
    )
    from contribai.core.quotas import AsyncPRQuota
    from contribai.generator.engine import ContributionGenerator
    from contribai.github.client import GitHubClient
    from contribai.github.guidelines import RepoGuidelines
    from contribai.issues.solver import IssueSolver
    from contribai.llm.provider import LLMProvider
    from contribai.orchestrator.memory import Memory
    from contribai.orchestrator.pipeline import PipelineResult
    from contribai.orchestrator.review_gate import HumanReviewer
    from contribai.pr.manager import PRManager

logger = logging.getLogger(__name__)


#: Step signature: takes frozen context + mutable state, returns nothing.
#: Steps mutate ``state`` in place. The conductor awaits each step in order.
PipelineStep = Callable[["PipelineContext", "PipelineState"], Awaitable[None]]


# ── State ─────────────────────────────────────────────────────────────────────


@dataclass
class PipelineState:
    """Mutable state passed through every step of the pipeline.

    Input fields (``repo``, ``dry_run``, ``max_prs``, ``closes_issue``) are
    set by the caller before :meth:`Pipeline.run` and treated as immutable
    thereafter. All other fields are populated by steps in order.

    ``skip_reason`` is checked by the conductor after every step; setting it
    short-circuits the rest of the pipeline. The :class:`PipelineResult`
    instance in ``result`` is the eventual return value — every step that
    produces a PR / contribution / error appends to it.
    """

    # ── Input (immutable from caller) ──────────────────────────────────
    repo: Repository
    dry_run: bool = False
    max_prs: int = 5
    issue_number: int | None = None
    # Set by ``solve_issue_step`` for issue-mode runs. Entries are aligned with
    # ``state.validated_findings`` until generation creates an envelope.
    closes_issues: list[int | None] = field(default_factory=list)

    # ── Step 1 outputs (load_repo_context_step) ───────────────────────
    cached_context: str | None = None
    guidelines: RepoGuidelines | None = None
    repo_profile: RepoProfile | None = None
    pr_history_context: str = ""
    extra_context: str = ""
    skip_reason: SkipReason | None = None

    # ── Step 2 outputs (run_analysis_step / solve_issue_step) ─────────
    analysis: AnalysisResult | None = None
    findings: list[Finding] = field(default_factory=list)

    # ── Step 3 outputs (validate_findings_step / validate_issue_step) ─
    file_tree: list[FileNode] = field(default_factory=list)
    relevant_files: dict[str, str] = field(default_factory=dict)
    context: RepoContext | None = None
    validated_findings: list[Finding] = field(default_factory=list)

    # ── Step 4 outputs (generate_contribution_step) ───────────────────
    contributions: list[Contribution] = field(default_factory=list)
    contribution_envelopes: list[ContributionEnvelope] = field(default_factory=list)

    # ── Step 5 outputs (submit_pr_step) ───────────────────────────────
    prs: list[PRResult] = field(default_factory=list)

    # ── Accumulator ───────────────────────────────────────────────────
    result: PipelineResult = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # PipelineResult cannot be the default factory in the dataclass
        # annotation because of the ``field(default=None)`` workaround above.
        if self.result is None:
            from contribai.orchestrator.pipeline import PipelineResult

            self.result = PipelineResult()


# ── Context ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PipelineContext:
    """Frozen collaborator bag passed to every step.

    Steps never mutate this object. ``solver`` is ``None`` for analysis
    mode and populated for issue mode.
    """

    github: GitHubClient
    llm: LLMProvider
    memory: Memory
    analyzer: CodeAnalyzer
    generator: ContributionGenerator
    pr_manager: PRManager
    reviewer: HumanReviewer
    repo_intel: RepoIntelligence
    event_bus: EventBus
    config: ContribAIConfig
    solver: IssueSolver | None = None  # populated by `_build_pipeline_context` in issue mode
    review_and_publish: Callable[..., Awaitable[PRResult | None]] | None = None
    check_ci: Callable[..., Awaitable[None]] | None = None
    pr_quota: AsyncPRQuota | None = None
    controlled_publish: bool = False

    def set_task(self, task_name: str) -> None:
        """Set task context on the LLM (no-op for providers without it).

        Equivalent to the old ``ContribPipeline._set_task`` instance method.
        Callers should use this rather than reaching into ``self.llm``.
        """
        # Import lazily to avoid pulling MultiModelProvider at module import.
        from contribai.llm.provider import MultiModelProvider

        if isinstance(self.llm, MultiModelProvider):
            import contextlib

            from contribai.llm.models import TaskType

            with contextlib.suppress(ValueError):
                self.llm.set_task(TaskType(task_name))


@dataclass(frozen=True, slots=True)
class ContributionEnvelope:
    """Keep generated contribution data bound to its originating issue."""

    contribution: Contribution
    closes_issue: int | None = None


# ── Conductor ─────────────────────────────────────────────────────────────────


class Pipeline:
    """Tiny Haystack-style orchestrator: state-in / state-out steps.

    Example::

        pipeline = Pipeline([
            load_repo_context_step,
            run_analysis_step,
            validate_findings_step,
            generate_contribution_step,
            submit_pr_step,
        ])
        result = (await pipeline.run(state, ctx)).result

    The conductor:
      1. Awaits each step in order. Steps mutate ``state`` in place.
      2. After each step, checks ``state.skip_reason``. If set, logs and
         breaks out of the loop.
      3. Propagates exceptions to the caller (the existing
         ``ContribPipeline._guarded`` aggregator catches them per-repo).
    """

    def __init__(self, steps: list[PipelineStep], *, label: str = ""):
        self._steps = list(steps)
        self._label = label or type(self).__name__

    @property
    def steps(self) -> list[PipelineStep]:
        """Read-only view of the configured steps (useful for tests)."""
        return list(self._steps)

    async def run(
        self,
        state: PipelineState,
        ctx: PipelineContext,
    ) -> PipelineState:
        """Execute the configured steps in order, returning ``state``.

        The state is mutated in place; the return value is the same
        object for convenience (so callers can write
        ``state = await pipeline.run(state, ctx)``).
        """
        for step in self._steps:
            await step(ctx, state)
            if state.skip_reason is not None:
                logger.info(
                    "🛑 Pipeline[%s] short-circuit at %s: %s",
                    self._label,
                    getattr(step, "__name__", repr(step)),
                    state.skip_reason,
                )
                break
        # Always mark the repo as analyzed in the accumulator, matching the
        # legacy `_process_repo` behavior (`result.repos_analyzed = 1`).
        state.result.repos_analyzed = 1
        return state


__all__ = [
    "ContributionEnvelope",
    "Pipeline",
    "PipelineContext",
    "PipelineState",
    "PipelineStep",
]
