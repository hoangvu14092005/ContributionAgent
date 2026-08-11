"""Read-only issue-first expected-value opportunity ranking."""

from __future__ import annotations

import re
from collections.abc import Iterable

from contribai.analysis.repo_intel import RepoProfile
from contribai.core.models import Finding, Issue, Repository
from contribai.opportunity.learning import (
    LearnedRepoPreference,
    OutcomeLearner,
    outcome_type_for_issue,
)
from contribai.opportunity.models import (
    CandidateFinding,
    ContributionOpportunity,
    OpportunityCandidate,
    OpportunityEvidence,
    OpportunitySource,
)
from contribai.opportunity.scoring import score_opportunity
from contribai.storage.outcomes import ContributionOutcome


class OpportunityEngine:
    """Rank contribution opportunities without knowing publisher internals."""

    HIGH_VALUE_LABELS = frozenset(
        {
            "good first issue",
            "good-first-issue",
            "help wanted",
            "help-wanted",
            "bug",
            "enhancement",
            "easy",
        }
    )

    def __init__(
        self,
        *,
        issue_first: bool = True,
        outcome_learner: OutcomeLearner | None = None,
    ) -> None:
        self.issue_first = issue_first
        self.outcome_learner = outcome_learner or OutcomeLearner()

    def rank(
        self,
        repo: Repository,
        *,
        issues: Iterable[Issue] = (),
        findings: Iterable[Finding] = (),
        profile: RepoProfile | None = None,
        max_candidates: int | None = None,
        outcomes: Iterable[ContributionOutcome] = (),
    ) -> list[OpportunityCandidate]:
        """Return explainable issue/code candidates in deterministic order."""
        learned = self.outcome_learner.learn(outcomes, repo=repo.full_name)
        issue_candidates = [
            self._score_issue(repo, issue, profile, learned)
            for issue in sorted(issues, key=lambda item: (item.number, item.title.lower()))
        ]
        scan_candidates = [
            self._score_finding(repo, finding, profile, learned) for finding in findings
        ]
        candidates = issue_candidates + scan_candidates
        candidates.sort(key=self._sort_key(issue_first=self.issue_first))
        if max_candidates is not None:
            if max_candidates < 0:
                raise ValueError("max_candidates must be non-negative")
            candidates = candidates[:max_candidates]
        return candidates

    def _score_issue(
        self,
        repo: Repository,
        issue: Issue,
        profile: RepoProfile | None,
        learned: LearnedRepoPreference,
    ) -> OpportunityCandidate:
        labels = {label.lower().strip() for label in issue.labels}
        body = issue.body or ""
        label_value = bool(labels & self.HIGH_VALUE_LABELS)
        wants = 0.85 if label_value else 0.55
        if (
            profile
            and "bug_fix" in profile.preferred_types
            and ("bug" in labels or "fix" in issue.title.lower())
        ):
            wants = min(1.0, wants + 0.1)
        issue_type = outcome_type_for_issue(issue.title, issue.labels)
        wants, merge = _apply_learning(
            wants,
            _merge_probability(profile),
            issue_type,
            learned,
            self.outcome_learner,
        )
        clarity = 0.85 if body else 0.45
        reproducibility = (
            0.85 if re.search(r"repro|steps to|expected|actual", body.lower()) else 0.5
        )
        testability = 0.8 if profile is None or profile.tests_present else 0.45
        correct = (clarity * reproducibility * testability) ** (1 / 3)
        if label_value:
            correct = min(1.0, correct + 0.1)
        impact = 0.75 if "bug" in labels else 0.6
        file_refs = len(re.findall(r"[\w./-]+\.[A-Za-z0-9]{1,8}", body))
        cost = min(1.0, 0.15 + len(body) / 10_000 + file_refs * 0.05)
        assigned = bool(getattr(issue, "assignees", []))
        risk = min(1.0, (0.05 if label_value else 0.15) + (0.2 if assigned else 0.0))
        spam = 0.02 if profile and profile.is_active else 0.1
        return self._candidate(
            repo,
            OpportunitySource.ISSUE,
            issue,
            correct,
            wants,
            merge,
            impact,
            cost,
            risk,
            spam,
            issue.number,
            (
                OpportunityEvidence(
                    "issue_label_signal",
                    1.0 if label_value else 0.0,
                    "high-value label present" if label_value else "no high-value label",
                ),
                OpportunityEvidence(
                    "issue_description",
                    correct,
                    "issue has reproducible detail" if issue.body else "issue has no body",
                ),
                OpportunityEvidence(
                    "reproduction_signal",
                    reproducibility,
                    "issue contains reproduction/expected-actual language"
                    if reproducibility > 0.5
                    else "no explicit reproduction signal",
                ),
                OpportunityEvidence(
                    "assignment_conflict",
                    1.0 if assigned else 0.0,
                    "issue has an assignee" if assigned else "issue is unassigned",
                ),
                OpportunityEvidence("testability", testability, "repository test signal"),
                OpportunityEvidence(
                    "maintainer_merge_history", merge, "historical merge probability"
                ),
                OpportunityEvidence("scope_cost", cost, "estimated issue analysis and repair cost"),
                *_learning_evidence(learned),
            ),
            issue_clarity=clarity,
            reproducibility=reproducibility,
            testability=testability,
        )

    def _score_finding(
        self,
        repo: Repository,
        finding: Finding,
        profile: RepoProfile | None,
        learned: LearnedRepoPreference,
    ) -> OpportunityCandidate:
        correct = max(0.0, min(1.0, finding.confidence))
        wants = (
            0.65
            if profile is None
            else (0.8 if _finding_type(finding) in profile.preferred_types else 0.45)
        )
        finding_type = _finding_type(finding)
        wants, merge = _apply_learning(
            wants,
            _merge_probability(profile),
            finding_type,
            learned,
            self.outcome_learner,
        )
        impact = {
            "critical": 1.0,
            "high": 0.85,
            "medium": 0.6,
            "low": 0.35,
        }.get(finding.severity.value, 0.5)
        cost = 0.2 if finding.file_path else 0.45
        risk = 0.1 if finding.confidence >= 0.8 else 0.25
        spam = 0.08
        return self._candidate(
            repo,
            OpportunitySource.CODE_SCAN,
            CandidateFinding(finding),
            correct,
            wants,
            merge,
            impact,
            cost,
            risk,
            spam,
            None,
            (
                OpportunityEvidence("finding_confidence", correct, "analyzer confidence"),
                OpportunityEvidence(
                    "severity_impact", impact, f"finding severity is {finding.severity.value}"
                ),
                OpportunityEvidence(
                    "maintainer_merge_history", merge, "historical merge probability"
                ),
                OpportunityEvidence(
                    "false_positive_risk", risk, "code-scan findings carry false-positive risk"
                ),
                *_learning_evidence(learned),
            ),
            issue_clarity=correct,
            reproducibility=correct,
            testability=impact,
        )

    @staticmethod
    def _candidate(
        repo: Repository,
        source: OpportunitySource,
        task,
        correct: float,
        wants: float,
        merge: float,
        impact: float,
        cost: float,
        risk: float,
        spam: float,
        issue_number: int | None,
        evidence: tuple[OpportunityEvidence, ...],
        *,
        issue_clarity: float,
        reproducibility: float,
        testability: float,
    ) -> OpportunityCandidate:
        opportunity = ContributionOpportunity(
            repo=repo.full_name,
            issue_number=issue_number,
            maintainer_receptiveness=wants,
            issue_clarity=min(1.0, issue_clarity),
            reproducibility=min(1.0, reproducibility),
            testability=min(1.0, testability),
            conflict_risk=min(1.0, risk + spam),
            estimated_cost_usd=cost * 10.0,
            expected_impact=impact,
            merge_probability=merge,
            evidence=evidence,
        )
        score = score_opportunity(opportunity)
        return OpportunityCandidate(repo.full_name, source, task, score, issue_number, opportunity)

    @staticmethod
    def _sort_key(*, issue_first: bool):
        def key(candidate: OpportunityCandidate) -> tuple:
            source_order = 0 if candidate.source is OpportunitySource.ISSUE else 1
            return (
                source_order if issue_first else 0,
                -candidate.score.expected_value,
                candidate.issue_number or 0,
                getattr(candidate.task, "title", "").lower(),
            )

        return key


def _merge_probability(profile: RepoProfile | None) -> float:
    if profile is None:
        return 0.5
    if not profile.is_active or not profile.ai_policy_allows:
        return 0.1
    base = profile.merge_rate if profile.merge_rate > 0 else 0.55
    review_signal = min(profile.avg_review_hours, 168) / 1_000
    backlog_penalty = min(0.25, profile.open_pr_backlog / 100)
    return min(0.95, max(0.1, base + review_signal - backlog_penalty))


def _apply_learning(
    wants: float,
    merge: float,
    contribution_type: str,
    learned: LearnedRepoPreference,
    learner: OutcomeLearner,
) -> tuple[float, float]:
    """Apply learned signals only after the configured evidence threshold."""
    if not learned.evidence_sufficient:
        return wants, merge
    type_signal = learner.contribution_type_signal(learned, contribution_type)
    learned_wants = learned.acceptance_probability
    if type_signal > 0.5:
        learned_wants = min(1.0, learned_wants + 0.15)
    elif type_signal < 0.5:
        learned_wants = max(0.0, learned_wants - 0.15)
    return (wants + learned_wants) / 2, learned.merge_probability


def _learning_evidence(learned: LearnedRepoPreference) -> tuple[OpportunityEvidence, ...]:
    if learned.sample_size == 0:
        return ()
    return (
        OpportunityEvidence(
            "outcome_acceptance",
            learned.acceptance_probability,
            f"{learned.evidence_label} from {learned.sample_size} outcomes",
        ),
        OpportunityEvidence(
            "outcome_merge_probability",
            learned.merge_probability,
            "smoothed repository merge history",
        ),
        OpportunityEvidence(
            "outcome_review_latency",
            min(1.0, learned.avg_review_hours / 168) if learned.avg_review_hours else 0.0,
            "observed maintainer review latency",
        ),
    )


def _finding_type(finding: Finding) -> str:
    mapping = {
        "security_fix": "security",
        "feature_add": "feature",
        "docs_improve": "docs",
        "performance_opt": "performance",
        "refactor": "refactor",
    }
    return mapping.get(finding.type.value, "bug_fix")


__all__ = ["OpportunityEngine", "OpportunitySource"]
