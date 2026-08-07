"""SQLite-backed, TTY-independent review request service."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

from contribai.orchestrator.memory import Memory
from contribai.publishing.permit import PublishSideEffect
from contribai.review.models import (
    CandidateHashMismatchError,
    ReviewDecision,
    ReviewRequest,
    ReviewStateError,
    ReviewStatus,
)
from contribai.storage.work_items import connection_transaction_lock


class ReviewNotFoundError(LookupError):
    """Raised when a review request ID is unknown."""


class ReviewExpiredError(ReviewStateError):
    """Raised when a decision targets an expired review request."""


class ReviewService:
    """Persist and resolve review requests without requiring a terminal."""

    def __init__(
        self,
        memory: Memory,
        *,
        ttl_seconds: float = 24 * 60 * 60,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._memory = memory
        self._ttl = ttl_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = connection_transaction_lock(memory.connection)

    async def request(
        self,
        work_id: str,
        candidate_hash: str,
        *,
        required_side_effects: Iterable[PublishSideEffect] = (),
        ttl_seconds: float | None = None,
    ) -> ReviewRequest:
        """Create or return the pending request for one exact candidate/scope."""
        if not work_id.strip():
            raise ValueError("work_id must not be empty")
        if not candidate_hash.strip():
            raise ValueError("candidate_hash must not be empty")
        work_item = await self._memory.work_items.get(work_id)
        if work_item is None:
            raise ReviewNotFoundError(f"WorkItem not found: {work_id}")

        now = _aware(self._clock())
        ttl = self._ttl if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            raise ValueError("ttl_seconds must be positive")
        expires_at = now + timedelta(seconds=ttl)
        effects = frozenset(PublishSideEffect(effect) for effect in required_side_effects)
        effects_json = json.dumps(sorted(effect.value for effect in effects))

        async with self._lock:
            cursor = await self._memory.connection.execute(
                """
                SELECT id FROM review_requests
                WHERE work_item_id = ? AND attempt = ? AND status = ?
                  AND json_extract(request_json, '$.candidate_hash') = ?
                  AND required_side_effects_json = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (
                    work_id,
                    work_item.attempt,
                    ReviewStatus.PENDING.value,
                    candidate_hash,
                    effects_json,
                ),
            )
            existing = await cursor.fetchone()
            if existing is not None:
                request = await self._get_unlocked(existing[0])
                if request.expires_at > now:
                    return request
                await self._memory.connection.execute(
                    """
                    UPDATE review_requests SET status = ?, updated_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        ReviewStatus.EXPIRED.value,
                        now.isoformat(),
                        request.id,
                        ReviewStatus.PENDING.value,
                    ),
                )

            review_id = f"review-{uuid.uuid4().hex}"
            await self._memory.connection.execute(
                """
                INSERT INTO review_requests
                    (id, work_item_id, attempt, status, required_side_effects_json,
                     request_json, decision_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    review_id,
                    work_id,
                    work_item.attempt,
                    ReviewStatus.PENDING.value,
                    effects_json,
                    json.dumps(
                        {
                            "candidate_hash": candidate_hash,
                            "expires_at": expires_at.isoformat(),
                            "mode": work_item.mode.value,
                        },
                        sort_keys=True,
                    ),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            await self._memory.connection.commit()
            return await self._get_unlocked(review_id)

    async def decide(self, review_id: str, decision: ReviewDecision) -> ReviewRequest:
        """Apply one hash/scope-bound decision and return the persisted request."""
        request = await self.get(review_id)
        if request.status is ReviewStatus.EXPIRED:
            raise ReviewExpiredError(f"Review request expired: {review_id}")
        if request.status is not ReviewStatus.PENDING:
            raise ReviewStateError(f"Review request {review_id} is already {request.status.value}")
        if decision.candidate_hash != request.candidate_hash:
            raise CandidateHashMismatchError(
                "Review decision candidate hash does not match the persisted request"
            )
        if decision.approved and not decision.approved_side_effects.issubset(
            request.required_side_effects
        ):
            raise ReviewStateError(
                "Review decision cannot approve side effects outside the requested scope"
            )

        status = ReviewStatus.APPROVED if decision.approved else ReviewStatus.REJECTED
        now = _aware(self._clock())
        decision_json = json.dumps(
            {
                "action": decision.action,
                "reason": decision.reason,
                "candidate_hash": decision.candidate_hash,
                "approved_side_effects": sorted(
                    effect.value for effect in decision.approved_side_effects
                ),
            },
            sort_keys=True,
        )
        async with self._lock:
            cursor = await self._memory.connection.execute(
                """
                UPDATE review_requests
                SET status = ?, decision_json = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    status.value,
                    decision_json,
                    now.isoformat(),
                    review_id,
                    ReviewStatus.PENDING.value,
                ),
            )
            if cursor.rowcount != 1:
                current = await self._get_unlocked(review_id)
                if current.status is ReviewStatus.EXPIRED:
                    raise ReviewExpiredError(f"Review request expired: {review_id}")
                raise ReviewStateError(f"Review request {review_id} changed concurrently")
            await self._memory.connection.commit()
            return await self._get_unlocked(review_id)

    async def get(self, review_id: str) -> ReviewRequest:
        """Load a review request and expire it when its deadline has passed."""
        if not review_id.strip():
            raise ValueError("review_id must not be empty")
        request = await self._get(review_id)
        if request.status is ReviewStatus.PENDING and request.expires_at <= _aware(self._clock()):
            await self.expire(review_id)
            request = await self._get(review_id)
        return request

    async def expire(self, review_id: str) -> ReviewRequest:
        """Explicitly expire a pending request, useful for scheduler cleanup."""
        now = _aware(self._clock())
        async with self._lock:
            cursor = await self._memory.connection.execute(
                """
                UPDATE review_requests SET status = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    ReviewStatus.EXPIRED.value,
                    now.isoformat(),
                    review_id,
                    ReviewStatus.PENDING.value,
                ),
            )
            if cursor.rowcount:
                await self._memory.connection.commit()
        return await self._get(review_id)

    async def _get(self, review_id: str) -> ReviewRequest:
        async with self._lock:
            return await self._get_unlocked(review_id)

    async def _get_unlocked(self, review_id: str) -> ReviewRequest:
        cursor = await self._memory.connection.execute(
            """
            SELECT id, work_item_id, attempt, status, required_side_effects_json,
                   request_json, decision_json, created_at, updated_at
            FROM review_requests WHERE id = ?
            """,
            (review_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ReviewNotFoundError(f"Review request not found: {review_id}")
        (
            request_id,
            work_id,
            attempt,
            status,
            effects_json,
            request_json,
            decision_json,
            created_at,
            updated_at,
        ) = row
        request_data = json.loads(request_json or "{}")
        return ReviewRequest(
            id=request_id,
            work_id=work_id,
            candidate_hash=request_data["candidate_hash"],
            status=ReviewStatus(status),
            attempt=int(attempt),
            required_side_effects=frozenset(
                PublishSideEffect(effect) for effect in json.loads(effects_json or "[]")
            ),
            created_at=_parse_datetime(created_at),
            updated_at=_parse_datetime(updated_at),
            expires_at=_parse_datetime(request_data["expires_at"]),
            decision=_decode_decision(decision_json),
        )


def _decode_decision(value: str | None) -> ReviewDecision | None:
    if not value:
        return None
    data = json.loads(value)
    return ReviewDecision(
        action=data.get("action", ReviewDecision.SKIP),
        reason=data.get("reason", ""),
        candidate_hash=data.get("candidate_hash"),
        approved_side_effects=frozenset(data.get("approved_side_effects", [])),
    )


def _parse_datetime(value: str) -> datetime:
    return _aware(datetime.fromisoformat(value))


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = ["ReviewExpiredError", "ReviewNotFoundError", "ReviewService"]
