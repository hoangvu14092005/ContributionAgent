"""Evidence-preserving candidate ranking."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from contribai.engines.candidates import CandidateSet, PatchCandidate
from contribai.verification.models import VerificationReport, VerificationStatus


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """Candidate plus its independent report and normalized ranking score."""

    candidate: PatchCandidate
    report: VerificationReport
    score: float


@dataclass(frozen=True, slots=True)
class RankedCandidateSet:
    """Ordered candidates retaining every candidate/report pair."""

    items: tuple[RankedCandidate, ...]

    @property
    def best(self) -> RankedCandidate | None:
        """Return only a verified candidate; inconclusive evidence cannot publish."""
        return next((item for item in self.items if item.report.publishable), None)


class CandidateRanker:
    """Rank by verification status, safety, quality, minimality and cost."""

    def rank(
        self,
        candidates: CandidateSet,
        reports: Mapping[str, VerificationReport],
    ) -> RankedCandidateSet:
        ranked: list[RankedCandidate] = []
        for candidate in candidates:
            report = reports.get(candidate.patch_sha256)
            if report is None:
                continue
            score = self._score(candidate, report)
            ranked.append(RankedCandidate(candidate, report, score))
        ranked.sort(
            key=lambda item: (
                -item.score,
                item.candidate.patch_sha256,
                item.candidate.attempt_id,
            )
        )
        return RankedCandidateSet(tuple(ranked))

    @staticmethod
    def _score(candidate: PatchCandidate, report: VerificationReport) -> float:
        status_score = {
            VerificationStatus.PASSED: 1.0,
            VerificationStatus.INCONCLUSIVE: 0.2,
            VerificationStatus.FAILED: 0.0,
        }[report.status]
        cost_penalty = min(0.2, report.cost_usd / 100.0)
        return max(
            0.0,
            0.45 * status_score
            + 0.2 * report.regression_safety_score
            + 0.2 * report.quality_score
            + 0.15 * report.minimality_score
            - cost_penalty,
        )


__all__ = ["CandidateRanker", "RankedCandidate", "RankedCandidateSet"]
