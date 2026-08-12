"""Evidence-thresholded outcome learning for opportunity ranking."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from contribai.storage.outcomes import ContributionOutcome, summarize_outcomes


@dataclass(frozen=True, slots=True)
class LearnedRepoPreference:
    """Smoothed learned signals safe to feed into expected-value scoring."""

    repo: str
    sample_size: int
    evidence_sufficient: bool
    acceptance_probability: float
    merge_probability: float
    avg_review_hours: float
    requested_changes_rate: float
    ci_pass_rate: float
    preferred_types: tuple[str, ...]
    rejected_types: tuple[str, ...]

    @property
    def evidence_label(self) -> str:
        """Return a human-readable evidence state for score explanations."""
        return "sufficient" if self.evidence_sufficient else "prior_only"


class OutcomeLearner:
    """Learn repository preferences with Bayesian smoothing and a hard threshold."""

    def __init__(self, *, evidence_threshold: int = 5) -> None:
        if evidence_threshold <= 0:
            raise ValueError("evidence_threshold must be positive")
        self.evidence_threshold = evidence_threshold

    def learn(
        self,
        outcomes: Iterable[ContributionOutcome],
        *,
        repo: str,
    ) -> LearnedRepoPreference:
        """Return a stable profile; small samples remain close to neutral priors."""
        summary = summarize_outcomes(
            outcomes,
            repo=repo,
            evidence_threshold=self.evidence_threshold,
        )
        return LearnedRepoPreference(
            repo=repo,
            sample_size=summary.sample_size,
            evidence_sufficient=summary.evidence_sufficient,
            acceptance_probability=summary.acceptance_probability,
            merge_probability=summary.merge_probability,
            avg_review_hours=summary.avg_review_hours,
            requested_changes_rate=summary.requested_changes_rate,
            ci_pass_rate=summary.ci_pass_rate,
            preferred_types=summary.preferred_types,
            rejected_types=summary.rejected_types,
        )

    def contribution_type_signal(
        self,
        preference: LearnedRepoPreference,
        contribution_type: str,
    ) -> float:
        """Return a bounded preference multiplier for a contribution type."""
        if not preference.evidence_sufficient:
            return 0.5
        normalized = contribution_type.strip().lower()
        if normalized in {value.lower() for value in preference.preferred_types}:
            return 0.75
        if normalized in {value.lower() for value in preference.rejected_types}:
            return 0.25
        return 0.5


def outcome_type_for_issue(title: str, labels: Iterable[str] = ()) -> str:
    """Classify an issue into the same coarse type vocabulary as repo history."""
    text = f"{title} {' '.join(labels)}".lower()
    if any(token in text for token in ("security", "cve", "auth", "xss")):
        return "security"
    if any(token in text for token in ("test", "coverage", "pytest")):
        return "test"
    if any(token in text for token in ("doc", "readme", "comment")):
        return "docs"
    if any(token in text for token in ("feature", "feat", "add", "support")):
        return "feature"
    if any(token in text for token in ("perf", "optimize", "speed", "cache")):
        return "performance"
    return "bug_fix"


__all__ = [
    "LearnedRepoPreference",
    "OutcomeLearner",
    "outcome_type_for_issue",
]
