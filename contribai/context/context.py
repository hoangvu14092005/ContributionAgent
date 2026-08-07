"""Unified, serializable context passed to analysis and repair components."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from contribai.analysis.repo_intel import RepoProfile
from contribai.context.rules import ResolvedRepoRules
from contribai.context.symbol_index import SymbolIndex
from contribai.core.models import (
    AttemptSummary,
    PRSummary,
    RepoContext,
    Repository,
    RepositorySnapshot,
)
from contribai.execution.budget import ExecutionBudget


@dataclass(frozen=True, slots=True)
class ContributionContext:
    """Single source of repository, rules, history and relevant code context."""

    repo: Repository
    repo_snapshot: RepositorySnapshot
    repo_profile: RepoProfile | None
    repo_rules: ResolvedRepoRules
    repo_map: str
    symbol_index: SymbolIndex
    pr_history: list[PRSummary]
    relevant_files: dict[str, str]
    previous_attempts: list[AttemptSummary]
    budget: ExecutionBudget
    max_context_tokens: int = 30_000

    def __post_init__(self) -> None:
        object.__setattr__(self, "pr_history", list(self.pr_history))
        object.__setattr__(self, "relevant_files", dict(sorted(self.relevant_files.items())))
        object.__setattr__(self, "previous_attempts", list(self.previous_attempts))

    @property
    def coding_style(self) -> str:
        """Expose resolved style rules without a second competing context model."""
        return self.repo_rules.to_prompt_context()

    @property
    def context_hash(self) -> str:
        """Return a deterministic hash suitable for attempt/audit metadata."""
        profile = self.repo_profile.to_prompt_context() if self.repo_profile else ""
        payload = {
            "repo": self.repo.full_name,
            "base_sha": self.repo_snapshot.base_sha,
            "rules": self.repo_rules.to_prompt_context(),
            "repo_map": self.repo_map,
            "profile": profile,
            "files": self.relevant_files,
            "prs": [item.model_dump(mode="json") for item in self.pr_history],
            "attempts": [item.model_dump(mode="json") for item in self.previous_attempts],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def to_repo_context(self) -> RepoContext:
        """Adapt once for legacy analyzer/generator code while retaining one source."""
        style = self.repo_rules.to_prompt_context()
        if self.repo_profile:
            style = f"{style}\n\n{self.repo_profile.to_prompt_context()}"
        return RepoContext(
            repo=self.repo,
            file_tree=list(self.repo_snapshot.file_tree),
            readme_content=self.repo_snapshot.readme_content,
            contributing_guide=self.repo_snapshot.contributing_guide,
            relevant_files=dict(self.relevant_files),
            coding_style=style,
            repo_intelligence=self.repo_profile.to_prompt_context() if self.repo_profile else "",
        )

    def to_prompt(self, max_tokens: int | None = None) -> str:
        """Build a deterministic, token-bounded prompt view of this context."""
        max_tokens = max_tokens or self.max_context_tokens
        max_chars = max_tokens * 4
        sections = [f"## Repository: {self.repo.full_name}"]
        if self.repo_profile:
            sections.append(self.repo_profile.to_prompt_context())
        if self.repo_snapshot.readme_content:
            sections.append(f"## README\n{self.repo_snapshot.readme_content}")
        if self.repo_snapshot.contributing_guide:
            sections.append(f"## Contributing Guide\n{self.repo_snapshot.contributing_guide}")
        sections.append(self.repo_rules.to_prompt_context())
        sections.append(f"## Repository Map\n{self.repo_map}")
        if self.pr_history:
            rows = [
                f"- #{item.number}: {item.title} [{item.state}]"
                for item in sorted(self.pr_history, key=lambda item: item.number)
            ]
            sections.append("## PR History\n" + "\n".join(rows))
        if self.previous_attempts:
            rows = [
                f"- {item.attempt_id}: {item.status} {item.reason}".strip()
                for item in self.previous_attempts
            ]
            sections.append("## Previous Attempts\n" + "\n".join(rows))
        for path, content in sorted(self.relevant_files.items()):
            sections.append(f"### {path}\n```\n{content}\n```")
        text = "\n\n".join(section for section in sections if section)
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n... [context truncated]"


# Kept as an explicit alias for code that uses the shorter name from the plan.
RepoSnapshot = RepositorySnapshot

__all__ = [
    "AttemptSummary",
    "ContributionContext",
    "PRSummary",
    "RepoSnapshot",
    "RepositorySnapshot",
]
