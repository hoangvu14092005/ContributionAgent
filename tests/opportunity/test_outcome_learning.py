"""Outcome learning and evidence-threshold tests."""

from __future__ import annotations

from contribai.core.models import Issue, Repository
from contribai.opportunity.engine import OpportunityEngine
from contribai.opportunity.learning import OutcomeLearner
from contribai.storage.outcomes import ContributionOutcome, OutcomeStatus


def _outcome(
    index: int,
    status: OutcomeStatus,
    *,
    contribution_type: str = "bug_fix",
) -> ContributionOutcome:
    return ContributionOutcome(
        repo="owner/repo",
        work_id=f"work-{index}",
        contribution_type=contribution_type,
        status=status,
        requested_changes=1 if status is OutcomeStatus.REJECTED else 0,
        ci_passed=status is not OutcomeStatus.REJECTED,
        review_latency_hours=12.0,
        cost_usd=0.2,
        wall_time_sec=30.0,
    )


def test_outcome_learner_keeps_small_samples_close_to_neutral_prior() -> None:
    learner = OutcomeLearner(evidence_threshold=5)
    preference = learner.learn(
        [_outcome(1, OutcomeStatus.REJECTED)],
        repo="owner/repo",
    )

    assert preference.evidence_sufficient is False
    assert 0.25 < preference.acceptance_probability < 0.5
    assert 0.25 < preference.merge_probability < 0.5
    assert preference.preferred_types == ()
    assert preference.rejected_types == ()


def test_sufficient_outcomes_change_expected_value_with_explainable_signal() -> None:
    repo = Repository(owner="owner", name="repo", full_name="owner/repo", language="Python")
    issue = Issue(
        number=7,
        title="Fix crash in retry path",
        body="Steps to reproduce: run the command; expected behavior is success.",
        labels=["bug"],
    )
    outcomes = [_outcome(index, OutcomeStatus.MERGED) for index in range(5)]

    baseline = OpportunityEngine().rank(repo, issues=[issue])[0]
    learned = OpportunityEngine().rank(repo, issues=[issue], outcomes=outcomes)[0]

    assert learned.score.expected_value > baseline.score.expected_value
    assert any(item.feature == "outcome_acceptance" for item in learned.score.evidence)
    assert any(item.feature == "outcome_merge_probability" for item in learned.score.evidence)


async def test_memory_round_trips_structured_outcomes(memory) -> None:
    await memory.record_contribution_outcome(_outcome(1, OutcomeStatus.ACCEPTED))
    await memory.record_contribution_outcome(_outcome(2, OutcomeStatus.MERGED))

    outcomes = await memory.get_contribution_outcomes("owner/repo")
    summary = await memory.outcomes.summarize("owner/repo", evidence_threshold=5)

    assert {item.status for item in outcomes} == {
        OutcomeStatus.ACCEPTED,
        OutcomeStatus.MERGED,
    }
    assert summary.sample_size == 2
    assert summary.evidence_sufficient is False
    assert summary.avg_review_hours == 12.0
