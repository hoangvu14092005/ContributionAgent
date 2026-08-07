"""Build one deterministic ContributionContext from repository evidence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from contribai.analysis.repo_intel import RepoProfile
from contribai.context.context import ContributionContext
from contribai.context.repo_map import RepoMapBuilder
from contribai.context.rules import RepoRules, ResolvedRepoRules
from contribai.context.symbol_index import SymbolIndex
from contribai.core.models import (
    AttemptSummary,
    FileNode,
    PRSummary,
    Repository,
    RepositorySnapshot,
)
from contribai.execution.budget import ExecutionBudget


class ContextBuilder:
    """Assemble repository context without competing legacy state objects."""

    def __init__(self, *, max_context_tokens: int = 30_000) -> None:
        if max_context_tokens <= 0:
            raise ValueError("max_context_tokens must be positive")
        self.max_context_tokens = max_context_tokens

    def build(
        self,
        repo: Repository,
        snapshot: RepositorySnapshot | None = None,
        *,
        files: Mapping[str, str] | None = None,
        file_tree: Iterable[FileNode] | None = None,
        readme_content: str | None = None,
        contributing_guide: str | None = None,
        repo_profile: RepoProfile | None = None,
        repo_rules: RepoRules | ResolvedRepoRules | None = None,
        pr_history: Iterable[PRSummary | Mapping[str, Any]] = (),
        previous_attempts: Iterable[AttemptSummary | Mapping[str, Any]] = (),
        budget: ExecutionBudget | None = None,
        base_sha: str = "unknown",
    ) -> ContributionContext:
        if snapshot is None:
            source_files = dict(files or {})
            snapshot = RepositorySnapshot(
                base_sha=base_sha,
                file_tree=list(file_tree or _tree_from_files(source_files)),
                files=source_files,
                readme_content=readme_content or _find_file(source_files, "README.md"),
                contributing_guide=contributing_guide
                or _find_file(source_files, "CONTRIBUTING.md"),
            )
        else:
            source_files = dict(snapshot.files)
            if files:
                source_files.update(files)

        resolved_rules = _resolve_rules(repo_rules, source_files)
        symbol_index = SymbolIndex.build(source_files)
        repo_map = RepoMapBuilder(max_context_tokens=max(1, self.max_context_tokens // 3)).build(
            source_files,
            symbol_index,
        )
        context_budget = budget or ExecutionBudget(
            max_steps=100,
            max_cost_usd=10.0,
            max_wall_time_sec=900.0,
            max_tool_failures=5,
        )
        return ContributionContext(
            repo=repo,
            repo_snapshot=snapshot,
            repo_profile=repo_profile,
            repo_rules=resolved_rules,
            repo_map=repo_map.text,
            symbol_index=symbol_index,
            pr_history=[_coerce_pr(item) for item in pr_history],
            relevant_files=_select_relevant_files(source_files),
            previous_attempts=[_coerce_attempt(item) for item in previous_attempts],
            budget=context_budget,
            max_context_tokens=self.max_context_tokens,
        )

    async def build_async(self, *args, **kwargs) -> ContributionContext:
        """Async facade for pipeline callers that already use awaitable builders."""
        return self.build(*args, **kwargs)


class ContextEngine(ContextBuilder):
    """Compatibility name for the orchestration layer in the architecture plan."""


def _resolve_rules(
    rules: RepoRules | ResolvedRepoRules | None,
    files: Mapping[str, str],
) -> ResolvedRepoRules:
    if isinstance(rules, ResolvedRepoRules):
        return rules
    if isinstance(rules, RepoRules):
        return rules.resolve()
    return RepoRules.from_files(dict(files)).resolve()


def _tree_from_files(files: Mapping[str, str]) -> list[FileNode]:
    return [
        FileNode(path=path, type="blob", size=len(content.encode("utf-8")))
        for path, content in sorted(files.items())
    ]


def _find_file(files: Mapping[str, str], name: str) -> str | None:
    for path, content in sorted(files.items()):
        if path.rsplit("/", 1)[-1].lower() == name.lower():
            return content
    return None


def _select_relevant_files(files: Mapping[str, str]) -> dict[str, str]:
    excluded_names = {
        "readme.md",
        "contributing.md",
        "claude.md",
        "agents.md",
        "pull_request_template.md",
    }
    return {
        path: content
        for path, content in sorted(files.items())
        if path.rsplit("/", 1)[-1].lower() not in excluded_names
    }


def _coerce_pr(value: PRSummary | Mapping[str, Any]) -> PRSummary:
    if isinstance(value, PRSummary):
        return value
    return PRSummary.model_validate(value)


def _coerce_attempt(value: AttemptSummary | Mapping[str, Any]) -> AttemptSummary:
    if isinstance(value, AttemptSummary):
        return value
    return AttemptSummary.model_validate(value)


__all__ = ["ContextBuilder", "ContextEngine"]
