"""Single command boundary for CLI, Web, MCP, scheduler and webhooks."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlparse

from contribai.control.mode import ExecutionMode
from contribai.domain.state import WorkState, allowed_transitions
from contribai.domain.work_item import BudgetSnapshot, JsonValue, WorkItem
from contribai.orchestrator.memory import Memory
from contribai.publishing.permit import PublishPermit, PublishSideEffect
from contribai.review.models import ReviewDecision, ReviewRequest
from contribai.review.service import ReviewService
from contribai.storage.work_items import (
    DuplicateWorkItemError,
    WorkItemNotFoundError,
    connection_transaction_lock,
)


class CommandStateError(RuntimeError):
    """Raised when a command cannot be applied to the current WorkItem state."""


class CommandService:
    """Submit lifecycle commands without holding GitHub write capability."""

    def __init__(self, memory: Memory, *, review_service: ReviewService | None = None) -> None:
        self._memory = memory
        self._reviews = review_service or ReviewService(memory)

    async def submit(
        self,
        repo: str,
        *,
        issue_number: int | None = None,
        mode: ExecutionMode = ExecutionMode.SHADOW,
        budget: BudgetSnapshot | Mapping[str, JsonValue] | None = None,
        idempotency_key: str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> WorkItem:
        """Create or return one discovered WorkItem for an entrypoint command."""
        canonical_repo = _canonical_repo(repo)
        execution_mode = ExecutionMode(mode)
        if issue_number is not None and issue_number <= 0:
            raise ValueError("issue_number must be positive")
        snapshot = (
            budget
            if isinstance(budget, BudgetSnapshot)
            else BudgetSnapshot.from_mapping(dict(budget or {}))
        )
        work_id = _work_id(canonical_repo, issue_number, idempotency_key)
        item = WorkItem.new(
            work_id=work_id,
            repo=canonical_repo,
            issue_number=issue_number,
            mode=execution_mode,
            budget=snapshot,
        )
        try:
            await self._memory.work_items.create(item)
        except DuplicateWorkItemError:
            existing = await self.get(work_id)
            if (
                existing.mode is not execution_mode
                or existing.repo != canonical_repo
                or existing.issue_number != issue_number
            ):
                raise CommandStateError(
                    f"Idempotency key already binds {work_id} to another command"
                ) from None
            return existing

        payload: dict[str, JsonValue] = {
            "command": "submit",
            "mode": execution_mode.value,
            "idempotency_key": idempotency_key or work_id,
        }
        if metadata:
            payload["metadata"] = cast(JsonValue, dict(metadata))
        await self._memory.work_items.append_event(
            work_id,
            "command_submitted",
            expected_version=item.version,
            payload=payload,
        )
        return item

    async def get(self, work_id: str) -> WorkItem:
        """Return the current WorkItem snapshot."""
        item = await self._memory.work_items.get(work_id)
        if item is None:
            raise WorkItemNotFoundError(work_id)
        return item

    async def request_review(
        self,
        work_id: str,
        candidate_hash: str,
        *,
        required_side_effects=(),
    ) -> ReviewRequest:
        """Persist a review request and advance verified work to review pending."""
        item = await self.get(work_id)
        request = await self._reviews.request(
            work_id,
            candidate_hash,
            required_side_effects=required_side_effects,
        )
        if item.state is WorkState.VERIFIED:
            await self._memory.work_items.transition(
                work_id,
                WorkState.REVIEW_PENDING,
                expected_version=item.version,
                reason="review requested",
                payload={"review_id": request.id, "candidate_hash": candidate_hash},
            )
        else:
            await self._memory.work_items.append_event(
                work_id,
                "review_requested",
                expected_version=item.version,
                payload={"review_id": request.id, "candidate_hash": candidate_hash},
            )
        return request

    async def approve(self, review_id: str, candidate_hash: str) -> WorkItem:
        """Approve one hash-bound review and advance its WorkItem when eligible."""
        pending = await self._reviews.get(review_id)
        request = await self._reviews.decide(
            review_id,
            ReviewDecision(
                ReviewDecision.APPROVE,
                candidate_hash=candidate_hash,
                approved_side_effects=pending.required_side_effects,
            ),
        )
        return await self._apply_review_result(request, "approved", WorkState.APPROVED)

    async def reject(self, review_id: str, candidate_hash: str, *, reason: str = "") -> WorkItem:
        """Reject one hash-bound review and route the WorkItem to repair."""
        request = await self._reviews.decide(
            review_id,
            ReviewDecision(ReviewDecision.REJECT, reason=reason, candidate_hash=candidate_hash),
        )
        return await self._apply_review_result(request, "rejected", WorkState.NEEDS_FIX)

    async def issue_publish_permit(
        self,
        work_id: str,
        review_id: str,
        *,
        base_sha: str,
        patch_sha256: str,
        verification_id: str,
        quota_reservation_id: str,
        expires_at: datetime,
        approved_side_effects: frozenset[PublishSideEffect] | None = None,
    ) -> PublishPermit:
        """Issue a durable permit only after state, review and proof bindings match."""
        item = await self.get(work_id)
        if item.state is not WorkState.APPROVED:
            raise CommandStateError("PublishPermit requires an approved WorkItem")
        review = await self._reviews.get(review_id)
        if review.work_id != work_id or review.decision is None or not review.decision.approved:
            raise CommandStateError("PublishPermit requires an approved matching review")
        if review.candidate_hash != patch_sha256:
            raise CommandStateError("PublishPermit patch hash does not match review proof")
        if not all(
            value.strip()
            for value in (base_sha, patch_sha256, verification_id, quota_reservation_id)
        ):
            raise ValueError("PublishPermit proof bindings must not be empty")
        expiry = _aware(expires_at)
        if expiry <= datetime.now(UTC):
            raise ValueError("PublishPermit expiry must be in the future")
        effects = frozenset(
            approved_side_effects
            if approved_side_effects is not None
            else review.decision.approved_side_effects
        )
        if PublishSideEffect.CREATE_PR not in effects:
            raise CommandStateError("PublishPermit requires reviewed create_pr side effect")

        permit_id = _permit_id(work_id, review_id, patch_sha256, verification_id)
        lock = connection_transaction_lock(self._memory.connection)
        async with lock:
            await self._memory.connection.execute(
                """
                INSERT OR IGNORE INTO publish_permits
                    (id, work_item_id, review_request_id, patch_hash,
                     approved_side_effects_json, expires_at, consumed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    permit_id,
                    work_id,
                    review_id,
                    patch_sha256,
                    json.dumps(sorted(effect.value for effect in effects)),
                    expiry.isoformat(),
                    datetime.now(UTC).isoformat(),
                ),
            )
            await self._memory.connection.commit()
        return PublishPermit(
            work_id=work_id,
            repo=item.repo,
            base_sha=base_sha,
            patch_sha256=patch_sha256,
            verification_id=verification_id,
            review_id=review_id,
            approved_side_effects=effects,
            quota_reservation_id=quota_reservation_id,
            expires_at=expiry,
        )

    async def resume(self, work_id: str) -> WorkItem:
        """Resume a repairable WorkItem through its explicit retry transition."""
        item = await self.get(work_id)
        if item.state is WorkState.NEEDS_FIX:
            return await self._memory.work_items.retry(
                work_id,
                expected_version=item.version,
                reason="resume command",
            )
        await self._memory.work_items.append_event(
            work_id,
            "resume_requested",
            expected_version=item.version,
            payload={"state": item.state.value},
        )
        return item

    async def cancel(self, work_id: str, *, reason: str = "cancel command") -> WorkItem:
        """Close cancellable work, recording a denial at publish boundary states."""
        item = await self.get(work_id)
        if WorkState.CLOSED in allowed_transitions(item.state):
            return await self._memory.work_items.transition(
                work_id,
                WorkState.CLOSED,
                expected_version=item.version,
                reason=reason,
            )
        await self._memory.work_items.append_event(
            work_id,
            "cancel_denied",
            expected_version=item.version,
            payload={"state": item.state.value, "reason": reason},
        )
        return item

    async def _apply_review_result(
        self,
        request: ReviewRequest,
        event_name: str,
        target: WorkState,
    ) -> WorkItem:
        item = await self.get(request.work_id)
        if target in allowed_transitions(item.state):
            return await self._memory.work_items.transition(
                item.id,
                target,
                expected_version=item.version,
                reason=event_name,
                payload={"review_id": request.id, "candidate_hash": request.candidate_hash},
            )
        await self._memory.work_items.append_event(
            item.id,
            f"review_{event_name}",
            expected_version=item.version,
            payload={"review_id": request.id, "candidate_hash": request.candidate_hash},
        )
        return item


def _canonical_repo(value: str) -> str:
    text = value.strip().rstrip("/")
    if not text:
        raise ValueError("repo must not be empty")
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        path = parsed.path.strip("/").split("/")
        if len(path) >= 2:
            text = "/".join(path[:2])
    return text


def _work_id(repo: str, issue_number: int | None, idempotency_key: str | None) -> str:
    identity = idempotency_key or f"{repo}#{issue_number or ''}:{uuid.uuid4().hex}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"work-{digest}"


def _permit_id(work_id: str, review_id: str, patch_sha256: str, verification_id: str) -> str:
    identity = "\x00".join((work_id, review_id, patch_sha256, verification_id))
    return f"permit-{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = ["CommandService", "CommandStateError"]
