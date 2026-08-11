"""Immutable opportunity scoring models with explainable evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from contribai.core.models import Finding, Issue


@dataclass(frozen=True, slots=True)
class ContributionOpportunity:
    """Stable score input contract for one repository/issue opportunity."""

    repo: str
    issue_number: int | None
    maintainer_receptiveness: float
    issue_clarity: float
    reproducibility: float
    testability: float
    conflict_risk: float
    estimated_cost_usd: float
    expected_impact: float
    merge_probability: float
    evidence: tuple[OpportunityEvidence, ...] = ()
    expected_value: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "maintainer_receptiveness",
            "issue_clarity",
            "reproducibility",
            "testability",
            "conflict_risk",
            "expected_impact",
            "merge_probability",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd must be non-negative")
        object.__setattr__(self, "evidence", tuple(self.evidence))


@dataclass(frozen=True, slots=True)
class CandidateFinding:
    """Read-only code-scan candidate; it is not a publish command."""

    finding: Finding

    @property
    def title(self) -> str:
        return self.finding.title

    @property
    def file_path(self) -> str:
        return self.finding.file_path


@dataclass(frozen=True, slots=True)
class OpportunityEvidence:
    """One feature contribution and rationale in an opportunity score."""

    feature: str
    value: float
    rationale: str


@dataclass(frozen=True, slots=True)
class OpportunityScore:
    """Expected contribution value and its component probabilities."""

    probability_correct_patch: float
    probability_maintainer_wants: float
    probability_merge: float
    impact: float
    cost: float
    risk_penalty: float
    spam_penalty: float
    expected_value: float
    evidence: tuple[OpportunityEvidence, ...]

    def __post_init__(self) -> None:
        for name in (
            "probability_correct_patch",
            "probability_maintainer_wants",
            "probability_merge",
            "impact",
            "cost",
            "risk_penalty",
            "spam_penalty",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        object.__setattr__(self, "evidence", tuple(self.evidence))


class OpportunitySource(StrEnum):
    """Origin of a candidate opportunity."""

    ISSUE = "issue"
    CODE_SCAN = "code_scan"


@dataclass(frozen=True, slots=True)
class OpportunityCandidate:
    """Rankable work candidate without any GitHub write capability."""

    repo: str
    source: OpportunitySource
    task: Issue | CandidateFinding
    score: OpportunityScore
    issue_number: int | None = None
    opportunity: ContributionOpportunity | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", OpportunitySource(self.source))
        if self.source is OpportunitySource.ISSUE and not isinstance(self.task, Issue):
            raise TypeError("issue opportunity must carry an Issue")
        if self.source is OpportunitySource.CODE_SCAN and not isinstance(
            self.task, CandidateFinding
        ):
            raise TypeError("code-scan opportunity must carry CandidateFinding")
        if self.opportunity is None:
            object.__setattr__(
                self,
                "opportunity",
                ContributionOpportunity(
                    repo=self.repo,
                    issue_number=self.issue_number,
                    maintainer_receptiveness=self.score.probability_maintainer_wants,
                    issue_clarity=self.score.probability_correct_patch,
                    reproducibility=self.score.probability_correct_patch,
                    testability=self.score.impact,
                    conflict_risk=self.score.risk_penalty,
                    estimated_cost_usd=self.score.cost * 10.0,
                    expected_impact=self.score.impact,
                    merge_probability=self.score.probability_merge,
                    evidence=self.score.evidence,
                    expected_value=self.score.expected_value,
                ),
            )


__all__ = [
    "CandidateFinding",
    "ContributionOpportunity",
    "OpportunityCandidate",
    "OpportunityEvidence",
    "OpportunityScore",
    "OpportunitySource",
]
