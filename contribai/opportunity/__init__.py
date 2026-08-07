"""Read-only opportunity scoring for contribution work selection."""

from contribai.opportunity.engine import OpportunityEngine, OpportunitySource
from contribai.opportunity.models import (
    CandidateFinding,
    ContributionOpportunity,
    OpportunityCandidate,
    OpportunityEvidence,
    OpportunityScore,
)
from contribai.opportunity.scoring import score_opportunity

__all__ = [
    "CandidateFinding",
    "ContributionOpportunity",
    "OpportunityCandidate",
    "OpportunityEngine",
    "OpportunityEvidence",
    "OpportunityScore",
    "OpportunitySource",
    "score_opportunity",
]
