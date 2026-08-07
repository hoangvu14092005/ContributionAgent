"""Persistence and proof tests for ReviewService."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from contribai.control.mode import ExecutionMode
from contribai.domain.work_item import BudgetSnapshot, WorkItem
from contribai.orchestrator.review_gate import HumanReviewer, ReviewGate
from contribai.publishing.permit import PublishSideEffect
from contribai.review.models import (
    CandidateHashMismatchError,
    ReviewDecision,
    ReviewStatus,
)
from contribai.review.service import ReviewExpiredError, ReviewService


async def _work_item(memory, work_id: str = "work-review") -> WorkItem:
    item = WorkItem.new(
        work_id=work_id,
        repo="owner/repo",
        issue_number=7,
        mode=ExecutionMode.REVIEW_ONLY,
        budget=BudgetSnapshot.empty(),
    )
    await memory.work_items.create(item)
    return item


@pytest.mark.asyncio
async def test_request_decide_and_get_persist_hash_bound_proof(memory) -> None:
    item = await _work_item(memory)
    service = ReviewService(memory)

    request = await service.request(
        item.id,
        "candidate-1",
        required_side_effects=(PublishSideEffect.CREATE_PR,),
    )
    decision = await service.decide(
        request.id,
        ReviewDecision(
            ReviewDecision.APPROVE,
            reason="verified",
            candidate_hash="candidate-1",
            approved_side_effects=frozenset({PublishSideEffect.CREATE_PR}),
        ),
    )
    loaded = await service.get(request.id)

    assert decision.status is ReviewStatus.APPROVED
    assert loaded.candidate_hash == "candidate-1"
    assert loaded.decision is not None
    assert loaded.decision.approved_side_effects == frozenset({PublishSideEffect.CREATE_PR})


@pytest.mark.asyncio
async def test_request_is_idempotent_for_same_pending_candidate(memory) -> None:
    item = await _work_item(memory)
    service = ReviewService(memory)

    first = await service.request(item.id, "candidate-1")
    second = await service.request(item.id, "candidate-1")

    assert first.id == second.id
    assert second.status is ReviewStatus.PENDING


@pytest.mark.asyncio
async def test_candidate_hash_mismatch_and_expiry_fail_closed(memory) -> None:
    item = await _work_item(memory)
    current = [datetime.now(UTC)]
    service = ReviewService(memory, clock=lambda: current[0])
    request = await service.request(item.id, "candidate-1", ttl_seconds=60)

    with pytest.raises(CandidateHashMismatchError):
        await service.decide(
            request.id,
            ReviewDecision(ReviewDecision.APPROVE, candidate_hash="candidate-2"),
        )

    current[0] += timedelta(seconds=61)
    expired = await service.get(request.id)
    assert expired.status is ReviewStatus.EXPIRED
    with pytest.raises(ReviewExpiredError):
        await service.decide(
            request.id,
            ReviewDecision(ReviewDecision.APPROVE, candidate_hash="candidate-1"),
        )


@pytest.mark.asyncio
async def test_service_does_not_touch_tty(memory, monkeypatch) -> None:
    item = await _work_item(memory)
    service = ReviewService(memory)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("ReviewService must not require a TTY")

    monkeypatch.setattr("builtins.input", fail_if_called)
    request = await service.request(item.id, "candidate-1")

    assert request.pending


@pytest.mark.asyncio
async def test_terminal_reviewer_adapter_persists_decision(memory) -> None:
    item = await _work_item(memory, "work-terminal-review")
    service = ReviewService(memory)
    reviewer = HumanReviewer(auto_approve=True, review_service=service)
    gate = ReviewGate(reviewer, explicit_human_review=True)

    decision = await gate.review(
        object(),
        object(),
        "owner/repo",
        planned_side_effects=(PublishSideEffect.CREATE_PR,),
        work_id=item.id,
        candidate_hash="candidate-terminal",
    )
    cursor = await memory.connection.execute(
        "SELECT id FROM review_requests WHERE work_item_id = ?",
        (item.id,),
    )
    review_id = (await cursor.fetchone())[0]
    persisted = await service.get(review_id)

    assert decision.approved
    assert persisted.status is ReviewStatus.APPROVED
    assert persisted.decision is not None
    assert persisted.decision.candidate_hash == "candidate-terminal"
