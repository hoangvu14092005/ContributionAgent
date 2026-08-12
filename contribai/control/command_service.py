"""Single command boundary for CLI, Web, MCP, scheduler and webhooks."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import asdict
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
from contribai.verification.models import VerificationReport, VerificationStatus


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
                or existing.budget != snapshot
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

    async def record_verification(
        self,
        work_id: str,
        report: VerificationReport,
    ) -> tuple[WorkItem, str]:
        """Persist verification evidence and advance only a publishable report."""
        item = await self.get(work_id)
        if item.state is not WorkState.VERIFYING:
            raise CommandStateError("Verification evidence requires a verifying WorkItem")
        if not report.candidate_hash.strip():
            raise ValueError("VerificationReport candidate_hash must not be empty")

        verification_id = report.verification_id
        report_json = json.dumps(asdict(report), sort_keys=True, separators=(",", ":"))
        lock = connection_transaction_lock(self._memory.connection)
        async with lock:
            cursor = await self._memory.connection.execute(
                """
                SELECT work_item_id, attempt, status, report_json
                FROM verification_reports WHERE id = ?
                """,
                (verification_id,),
            )
            existing = await cursor.fetchone()
            if existing is None:
                await self._memory.connection.execute(
                    """
                    INSERT INTO verification_reports
                        (id, work_item_id, attempt, status, report_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        verification_id,
                        work_id,
                        item.attempt,
                        report.status.value,
                        report_json,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                await self._memory.connection.commit()
            elif (
                str(existing[0]) != work_id
                or int(existing[1]) != item.attempt
                or str(existing[2]) != report.status.value
                or str(existing[3]) != report_json
            ):
                raise CommandStateError("Verification ID is already bound to different evidence")

        target = WorkState.VERIFIED if report.publishable else WorkState.NEEDS_FIX
        updated = await self._memory.work_items.transition(
            work_id,
            target,
            expected_version=item.version,
            reason=f"verification {report.status.value}",
            payload={"verification_id": verification_id, "candidate_hash": report.candidate_hash},
        )
        return updated, verification_id

    async def request_review(
        self,
        work_id: str,
        candidate_hash: str,
        *,
        required_side_effects=(),
    ) -> ReviewRequest:
        """Persist a review request only for verified work."""
        item = await self.get(work_id)
        if item.state is not WorkState.VERIFIED:
            raise CommandStateError("Review requires a verified WorkItem")
        request = await self._reviews.request(
            work_id,
            candidate_hash,
            required_side_effects=required_side_effects,
        )
        await self._memory.work_items.transition(
            work_id,
            WorkState.REVIEW_PENDING,
            expected_version=item.version,
            reason="review requested",
            payload={"review_id": request.id, "candidate_hash": candidate_hash},
        )
        return request

    async def approve(self, review_id: str, candidate_hash: str) -> WorkItem:
        """Approve one hash-bound review and advance its WorkItem."""
        pending = await self._reviews.get(review_id)
        item = await self.get(pending.work_id)
        if item.state is not WorkState.REVIEW_PENDING or pending.attempt != item.attempt:
            raise CommandStateError("Review approval requires the current review-pending attempt")
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
        pending = await self._reviews.get(review_id)
        item = await self.get(pending.work_id)
        if item.state is not WorkState.REVIEW_PENDING or pending.attempt != item.attempt:
            raise CommandStateError("Review rejection requires the current review-pending attempt")
        request = await self._reviews.decide(
            review_id,
            ReviewDecision(ReviewDecision.REJECT, reason=reason, candidate_hash=candidate_hash),
        )
        return await self._apply_review_result(request, "rejected", WorkState.NEEDS_FIX)

    async def reserve_publish_quota(
        self,
        work_id: str,
        *,
        provider: str,
        amount: Mapping[str, JsonValue],
        expires_at: datetime | None = None,
    ) -> str:
        """Persist one quota proof for a live approved WorkItem."""
        item = await self.get(work_id)
        if item.mode is not ExecutionMode.LIVE or item.state is not WorkState.APPROVED:
            raise CommandStateError("Publish quota can only be reserved for approved live work")
        if not provider.strip():
            raise ValueError("quota provider must not be empty")
        amount_json = json.dumps(
            dict(amount),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        expiry = _aware(expires_at) if expires_at else None
        if expiry is not None and expiry <= datetime.now(UTC):
            raise ValueError("quota reservation expiry must be in the future")
        reservation_id = _quota_id(work_id, item.attempt, provider, amount_json)
        now = datetime.now(UTC).isoformat()
        lock = connection_transaction_lock(self._memory.connection)
        async with lock:
            cursor = await self._memory.connection.execute(
                """
                SELECT work_item_id, provider, amount_json, status, expires_at
                FROM quota_reservations WHERE id = ?
                """,
                (reservation_id,),
            )
            existing = await cursor.fetchone()
            expected_expiry = expiry.isoformat() if expiry else None
            if existing is None:
                await self._memory.connection.execute(
                    """
                    INSERT INTO quota_reservations
                        (id, work_item_id, provider, amount_json, status,
                         expires_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'reserved', ?, ?, ?)
                    """,
                    (
                        reservation_id,
                        work_id,
                        provider,
                        amount_json,
                        expected_expiry,
                        now,
                        now,
                    ),
                )
                await self._memory.connection.commit()
            elif (
                str(existing[0]) != work_id
                or str(existing[1]) != provider
                or str(existing[2]) != amount_json
                or str(existing[3]) != "reserved"
                or (str(existing[4]) if existing[4] is not None else None) != expected_expiry
            ):
                raise CommandStateError("Quota reservation ID conflicts with persisted proof")
        return reservation_id

    async def issue_publish_permit(
        self,
        work_id: str,
        review_id: str,
        *,
        base_sha: str,
        patch_sha256: str,
        publish_sha256: str,
        verification_id: str,
        quota_reservation_id: str,
        expires_at: datetime,
        approved_side_effects: frozenset[PublishSideEffect] | None = None,
    ) -> PublishPermit:
        """Issue a durable permit only after state, review and proof bindings match."""
        item = await self.get(work_id)
        if item.mode is not ExecutionMode.LIVE:
            raise CommandStateError("PublishPermit requires an explicit live WorkItem")
        if item.state not in {WorkState.APPROVED, WorkState.PUBLISH_RESERVED}:
            raise CommandStateError("PublishPermit requires an approved WorkItem")
        review = await self._reviews.get(review_id)
        if (
            review.work_id != work_id
            or review.attempt != item.attempt
            or review.decision is None
            or not review.decision.approved
        ):
            raise CommandStateError("PublishPermit requires an approved matching review")
        if review.candidate_hash != publish_sha256:
            raise CommandStateError("PublishPermit publish hash does not match review proof")
        if not all(
            value.strip()
            for value in (
                base_sha,
                patch_sha256,
                publish_sha256,
                verification_id,
                quota_reservation_id,
            )
        ):
            raise ValueError("PublishPermit proof bindings must not be empty")
        expiry = _aware(expires_at)
        now = datetime.now(UTC)
        if expiry <= now:
            raise ValueError("PublishPermit expiry must be in the future")

        reviewed_effects = review.decision.approved_side_effects
        effects = frozenset(
            approved_side_effects if approved_side_effects is not None else reviewed_effects
        )
        if not effects.issubset(reviewed_effects):
            raise CommandStateError("PublishPermit cannot widen the reviewed side-effect scope")
        if PublishSideEffect.CREATE_PR not in effects:
            raise CommandStateError("PublishPermit requires reviewed create_pr side effect")

        lock = connection_transaction_lock(self._memory.connection)
        async with lock:
            verification_cursor = await self._memory.connection.execute(
                """
                SELECT status, report_json FROM verification_reports
                WHERE id = ? AND work_item_id = ? AND attempt = ?
                """,
                (verification_id, work_id, item.attempt),
            )
            verification = await verification_cursor.fetchone()
            if verification is None or str(verification[0]) != VerificationStatus.PASSED.value:
                raise CommandStateError("PublishPermit requires persisted passing verification")
            try:
                verification_payload = json.loads(str(verification[1]))
            except (TypeError, ValueError) as exc:
                raise CommandStateError("Persisted verification proof is malformed") from exc
            if verification_payload.get("candidate_hash") != patch_sha256:
                raise CommandStateError("Verification proof does not match the reviewed patch")

            quota_cursor = await self._memory.connection.execute(
                """
                SELECT status, expires_at FROM quota_reservations
                WHERE id = ? AND work_item_id = ?
                """,
                (quota_reservation_id, work_id),
            )
            quota = await quota_cursor.fetchone()
            if quota is None or str(quota[0]) not in {"reserved", "bound"}:
                raise CommandStateError("PublishPermit requires an active quota reservation")
            if quota[1] is not None and _aware(datetime.fromisoformat(str(quota[1]))) <= now:
                raise CommandStateError("Publish quota reservation has expired")

        permit_id = _permit_id(
            work_id,
            review_id,
            patch_sha256,
            publish_sha256,
            verification_id,
        )
        permit = PublishPermit(
            work_id=work_id,
            repo=item.repo,
            base_sha=base_sha,
            patch_sha256=patch_sha256,
            publish_sha256=publish_sha256,
            verification_id=verification_id,
            review_id=review_id,
            approved_side_effects=effects,
            quota_reservation_id=quota_reservation_id,
            expires_at=expiry,
        )

        lock = connection_transaction_lock(self._memory.connection)
        async with lock:
            cursor = await self._memory.connection.execute(
                """
                SELECT patch_hash, publish_hash, approved_side_effects_json, expires_at,
                       base_sha, verification_id, quota_reservation_id
                FROM publish_permits WHERE id = ?
                """,
                (permit_id,),
            )
            existing = await cursor.fetchone()
            if existing is not None:
                if (
                    str(existing[0]) != patch_sha256
                    or frozenset(
                        PublishSideEffect(effect) for effect in json.loads(str(existing[2]) or "[]")
                    )
                    != effects
                    or str(existing[1] or "") != publish_sha256
                    or _aware(datetime.fromisoformat(str(existing[3]))) != expiry
                    or str(existing[4] or "") != base_sha
                    or str(existing[5] or "") != verification_id
                    or str(existing[6] or "") != quota_reservation_id
                ):
                    raise CommandStateError("PublishPermit ID conflicts with persisted permit")
                return permit

        if quota is None or str(quota[0]) != "reserved":
            raise CommandStateError("New PublishPermit requires a reserved quota")

        if item.state is WorkState.APPROVED:
            item = await self._memory.work_items.transition(
                work_id,
                WorkState.PUBLISH_RESERVED,
                expected_version=item.version,
                reason="publish permit reserved",
                payload={
                    "review_id": review_id,
                    "verification_id": verification_id,
                    "quota_reservation_id": quota_reservation_id,
                    "candidate_hash": publish_sha256,
                    "patch_sha256": patch_sha256,
                },
            )

        lock = connection_transaction_lock(self._memory.connection)
        async with lock:
            await self._memory.connection.execute(
                """
                INSERT INTO publish_permits
                    (id, work_item_id, review_request_id, patch_hash, publish_hash, base_sha,
                     verification_id, quota_reservation_id, approved_side_effects_json,
                     expires_at, consumed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    permit_id,
                    work_id,
                    review_id,
                    patch_sha256,
                    publish_sha256,
                    base_sha,
                    verification_id,
                    quota_reservation_id,
                    json.dumps(sorted(effect.value for effect in effects)),
                    expiry.isoformat(),
                    datetime.now(UTC).isoformat(),
                ),
            )
            await self._memory.connection.execute(
                """
                UPDATE quota_reservations SET status = 'bound', updated_at = ?
                WHERE id = ? AND status = 'reserved'
                """,
                (datetime.now(UTC).isoformat(), quota_reservation_id),
            )
            await self._memory.connection.commit()
        return permit

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
        if request.attempt != item.attempt or target not in allowed_transitions(item.state):
            raise CommandStateError(
                f"Cannot apply review {event_name} while WorkItem is {item.state.value}"
            )
        return await self._memory.work_items.transition(
            item.id,
            target,
            expected_version=item.version,
            reason=event_name,
            payload={"review_id": request.id, "candidate_hash": request.candidate_hash},
        )


def _canonical_repo(value: str) -> str:
    text = value.strip().rstrip("/")
    if not text:
        raise ValueError("repo must not be empty")
    parsed = urlparse(text)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != "github.com":
            raise ValueError("repo URL must be an https://github.com/owner/name repository")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2:
            raise ValueError("repo URL must contain exactly owner/name")
        text = "/".join(parts)
    parts = text.split("/")
    if len(parts) != 2 or any(not part.strip() or part in {".", ".."} for part in parts):
        raise ValueError("repo must be in canonical owner/name form")
    if any(char.isspace() for char in text) or any(char in text for char in "?#\\\x00"):
        raise ValueError("repo contains invalid characters")
    return text


def _work_id(repo: str, issue_number: int | None, idempotency_key: str | None) -> str:
    identity = idempotency_key or f"{repo}#{issue_number or ''}:{uuid.uuid4().hex}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"work-{digest}"


def _permit_id(
    work_id: str,
    review_id: str,
    patch_sha256: str,
    publish_sha256: str,
    verification_id: str,
) -> str:
    identity = "\x00".join((work_id, review_id, patch_sha256, publish_sha256, verification_id))
    return f"permit-{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _quota_id(work_id: str, attempt: int, provider: str, amount_json: str) -> str:
    identity = "\x00".join((work_id, str(attempt), provider, amount_json))
    return f"quota-{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = ["CommandService", "CommandStateError"]
