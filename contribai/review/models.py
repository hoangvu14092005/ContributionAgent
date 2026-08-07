"""Immutable review request and decision contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from contribai.publishing.permit import PublishSideEffect


class ReviewStatus(StrEnum):
    """Durable states for a human review request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ReviewStateError(RuntimeError):
    """Raised when a review decision cannot be applied to its current state."""


class CandidateHashMismatchError(ValueError):
    """Raised when a decision is not bound to the reviewed candidate."""


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """A review action carrying the candidate proof used to make it."""

    APPROVE: ClassVar[str] = "approve"
    REJECT: ClassVar[str] = "reject"
    SKIP: ClassVar[str] = "skip"

    action: str
    reason: str = ""
    candidate_hash: str | None = None
    approved_side_effects: frozenset[PublishSideEffect] = frozenset()

    def __post_init__(self) -> None:
        action = str(self.action).strip().lower()
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "reason", str(self.reason or "")[:2_000])
        object.__setattr__(
            self,
            "approved_side_effects",
            frozenset(PublishSideEffect(effect) for effect in self.approved_side_effects)
            if action == self.APPROVE
            else frozenset(),
        )

    @property
    def approved(self) -> bool:
        """Whether this decision explicitly approves the candidate."""
        return self.action == self.APPROVE

    @property
    def rejected(self) -> bool:
        """Whether this decision rejects the candidate."""
        return self.action == self.REJECT

    @property
    def skipped(self) -> bool:
        """Whether this decision defers the candidate."""
        return self.action == self.SKIP

    def bind(self, candidate_hash: str) -> ReviewDecision:
        """Return the same decision bound to a candidate fingerprint."""
        return ReviewDecision(
            action=self.action,
            reason=self.reason,
            candidate_hash=candidate_hash,
            approved_side_effects=self.approved_side_effects,
        )


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """Persisted review request and immutable candidate binding."""

    id: str
    work_id: str
    candidate_hash: str
    status: ReviewStatus
    attempt: int
    required_side_effects: frozenset[PublishSideEffect]
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    decision: ReviewDecision | None = None

    @property
    def review_id(self) -> str:
        """Alias used by PublishPermit and external entrypoints."""
        return self.id

    @property
    def pending(self) -> bool:
        """Whether an external reviewer may still decide this request."""
        return self.status is ReviewStatus.PENDING


__all__ = [
    "CandidateHashMismatchError",
    "ReviewDecision",
    "ReviewRequest",
    "ReviewStateError",
    "ReviewStatus",
]
