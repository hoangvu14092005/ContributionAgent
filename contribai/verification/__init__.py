"""Evidence-backed patch verification and candidate ranking."""

from contribai.verification.engine import VerificationEngine, VerificationPlan
from contribai.verification.models import (
    FailureContext,
    VerificationEvidence,
    VerificationReport,
    VerificationStatus,
)
from contribai.verification.ranker import CandidateRanker, RankedCandidateSet

__all__ = [
    "CandidateRanker",
    "FailureContext",
    "RankedCandidateSet",
    "VerificationEngine",
    "VerificationEvidence",
    "VerificationPlan",
    "VerificationReport",
    "VerificationStatus",
]
