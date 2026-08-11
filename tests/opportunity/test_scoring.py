"""Pure contribution opportunity score tests."""

from __future__ import annotations

from contribai.opportunity.models import ContributionOpportunity, OpportunityEvidence
from contribai.opportunity.scoring import score_opportunity


def test_expected_contribution_value_matches_documented_formula() -> None:
    opportunity = ContributionOpportunity(
        repo="owner/repo",
        issue_number=1,
        maintainer_receptiveness=0.8,
        issue_clarity=0.9,
        reproducibility=0.8,
        testability=1.0,
        conflict_risk=0.1,
        estimated_cost_usd=2.0,
        expected_impact=0.75,
        merge_probability=0.6,
        evidence=(OpportunityEvidence("labels", 1.0, "bug"),),
    )

    score = score_opportunity(opportunity)
    probability_correct = (0.9 * 0.8 * 1.0) ** (1 / 3)
    expected = probability_correct * 0.8 * 0.6 * 0.75 - 0.2 - 0.1

    assert score.expected_value == expected
    assert score.probability_correct_patch == probability_correct
    assert len(score.evidence) == 4


def test_conflict_and_cost_penalties_reduce_expected_value() -> None:
    base = dict(
        repo="owner/repo",
        issue_number=None,
        maintainer_receptiveness=0.9,
        issue_clarity=0.9,
        reproducibility=0.9,
        testability=0.9,
        expected_impact=0.8,
        merge_probability=0.8,
    )
    low_penalty = score_opportunity(
        ContributionOpportunity(**base, conflict_risk=0.0, estimated_cost_usd=0.0)
    )
    high_penalty = score_opportunity(
        ContributionOpportunity(**base, conflict_risk=0.5, estimated_cost_usd=5.0)
    )

    assert high_penalty.expected_value < low_penalty.expected_value
