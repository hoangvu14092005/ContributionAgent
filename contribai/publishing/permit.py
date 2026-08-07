"""Permit and candidate contracts for the GitHub publishing boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from contribai.core.models import Contribution, Repository


class PublishPermitError(ValueError):
    """Raised when a publish permit is incomplete, expired, or mismatched."""


@dataclass(frozen=True)
class PublishPermit:
    """Proof bindings required before any GitHub publish side effect."""

    work_id: str
    repo: str
    base_sha: str
    patch_sha256: str
    verification_id: str
    review_id: str
    quota_reservation_id: str
    expires_at: datetime


class PublishCandidate(Protocol):
    """Minimal payload consumed by the publishing layer.

    Task 10C can adapt its future PatchCandidate to this protocol without making
    GitHubPublisher depend on engine or workspace internals.
    """

    contribution: Contribution
    target_repo: Repository
    base_sha: str
    patch_sha256: str
    guidelines: Any | None
    closes_issue: int | None

    @property
    def repo(self) -> str:
        """Return the canonical ``owner/name`` repository identity."""
        ...


@dataclass(frozen=True)
class ContributionPublishCandidate:
    """Adapter binding today's Contribution model to a publish fingerprint."""

    contribution: Contribution
    target_repo: Repository
    base_sha: str
    patch_sha256: str
    guidelines: Any | None = None
    closes_issue: int | None = None

    @property
    def repo(self) -> str:
        """Return the target repository bound into the fingerprint."""
        return self.target_repo.full_name
