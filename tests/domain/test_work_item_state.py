"""Domain contract tests for persistent contribution work items."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from contribai.control.mode import ExecutionMode
from contribai.domain.state import InvalidWorkTransitionError, WorkState, validate_transition
from contribai.domain.work_item import BudgetSnapshot, WorkItem


def test_patched_cannot_skip_verification_and_publish() -> None:
    with pytest.raises(InvalidWorkTransitionError):
        validate_transition(WorkState.PATCHED, WorkState.PUBLISHED)


def test_backward_transition_is_rejected() -> None:
    with pytest.raises(InvalidWorkTransitionError):
        validate_transition(WorkState.VERIFIED, WorkState.SOLVING)


@pytest.mark.parametrize("terminal", [WorkState.MERGED, WorkState.CLOSED])
def test_terminal_states_reject_every_transition(terminal: WorkState) -> None:
    for candidate in WorkState:
        with pytest.raises(InvalidWorkTransitionError):
            validate_transition(terminal, candidate)


def test_engine_approval_wait_has_explicit_resume_and_cancel_edges() -> None:
    validate_transition(WorkState.SOLVING, WorkState.ENGINE_WAITING_APPROVAL)
    validate_transition(WorkState.ENGINE_WAITING_APPROVAL, WorkState.SOLVING)
    validate_transition(WorkState.ENGINE_WAITING_APPROVAL, WorkState.NEEDS_FIX)
    validate_transition(WorkState.ENGINE_WAITING_APPROVAL, WorkState.CLOSED)


def test_retry_cannot_be_expressed_as_an_ordinary_transition() -> None:
    with pytest.raises(InvalidWorkTransitionError):
        validate_transition(WorkState.NEEDS_FIX, WorkState.PREPARING)


def test_budget_snapshot_is_canonical_serializable_and_immutable() -> None:
    budget = BudgetSnapshot.from_mapping(
        {
            "max_steps": 12,
            "provider": "openai",
            "limits": {"wall_time_sec": 90.0, "tools": ["shell", "patch"]},
        }
    )

    assert budget.to_mapping() == {
        "limits": {"tools": ["shell", "patch"], "wall_time_sec": 90.0},
        "max_steps": 12,
        "provider": "openai",
    }
    assert BudgetSnapshot.from_json(budget.to_json()) == budget

    returned = budget.to_mapping()
    returned["max_steps"] = 999
    assert budget.to_mapping()["max_steps"] == 12

    with pytest.raises(FrozenInstanceError):
        budget._canonical_json = "{}"  # type: ignore[misc]


def test_work_item_is_an_immutable_snapshot() -> None:
    item = WorkItem.new(
        work_id="work-1",
        repo="owner/repo",
        issue_number=7,
        mode=ExecutionMode.SHADOW,
        budget=BudgetSnapshot.from_mapping({"max_steps": 3}),
    )

    assert item.state is WorkState.DISCOVERED
    assert item.attempt == 1
    assert item.version == 0
    with pytest.raises(FrozenInstanceError):
        item.attempt = 2  # type: ignore[misc]
