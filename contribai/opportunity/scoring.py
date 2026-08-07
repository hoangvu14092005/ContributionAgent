"""Pure, deterministic expected contribution value scoring."""

from __future__ import annotations

from contribai.opportunity.models import ContributionOpportunity, OpportunityScore


def score_opportunity(opportunity: ContributionOpportunity) -> OpportunityScore:
    """Compute expected value from the stable opportunity contract.

    P(correct patch) is intentionally decomposed into clarity, reproducibility
    and testability so the ranking evidence explains where confidence came from.
    """
    probability_correct = (
        opportunity.issue_clarity * opportunity.reproducibility * opportunity.testability
    ) ** (1 / 3)
    cost = min(1.0, opportunity.estimated_cost_usd / 10.0)
    expected_value = (
        probability_correct
        * opportunity.maintainer_receptiveness
        * opportunity.merge_probability
        * opportunity.expected_impact
        - cost
        - opportunity.conflict_risk
    )
    evidence = (
        *opportunity.evidence,
        _evidence(
            "probability_correct_patch",
            probability_correct,
            "geometric mean of clarity, reproducibility and testability",
        ),
        _evidence("estimated_cost", cost, "normalized expected execution cost"),
        _evidence("conflict_risk", opportunity.conflict_risk, "scope/conflict penalty"),
    )
    return OpportunityScore(
        probability_correct_patch=probability_correct,
        probability_maintainer_wants=opportunity.maintainer_receptiveness,
        probability_merge=opportunity.merge_probability,
        impact=opportunity.expected_impact,
        cost=cost,
        risk_penalty=opportunity.conflict_risk,
        spam_penalty=0.0,
        expected_value=expected_value,
        evidence=evidence,
    )


def _evidence(feature: str, value: float, rationale: str):
    from contribai.opportunity.models import OpportunityEvidence

    return OpportunityEvidence(feature, value, rationale)


__all__ = ["score_opportunity"]
