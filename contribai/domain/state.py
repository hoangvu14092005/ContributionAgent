"""Authoritative lifecycle for contribution work items."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class WorkState(StrEnum):
    """Persisted states in the contribution control plane."""

    DISCOVERED = "discovered"
    QUALIFIED = "qualified"
    RESERVED = "reserved"
    PREPARING = "preparing"
    SOLVING = "solving"
    ENGINE_WAITING_APPROVAL = "engine_waiting_approval"
    PATCH_COLLECTING = "patch_collecting"
    PATCHED = "patched"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    REVIEW_PENDING = "review_pending"
    APPROVED = "approved"
    PUBLISH_RESERVED = "publish_reserved"
    PUBLISHED = "published"
    CI_RUNNING = "ci_running"
    MERGED = "merged"
    CLOSED = "closed"
    NEEDS_FIX = "needs_fix"


class InvalidWorkTransitionError(ValueError):
    """Raised when a lifecycle edge is not explicitly permitted."""


_ALLOWED_TRANSITIONS: Final[Mapping[WorkState, frozenset[WorkState]]] = MappingProxyType(
    {
        WorkState.DISCOVERED: frozenset({WorkState.QUALIFIED, WorkState.CLOSED}),
        WorkState.QUALIFIED: frozenset({WorkState.RESERVED, WorkState.CLOSED}),
        WorkState.RESERVED: frozenset({WorkState.PREPARING, WorkState.NEEDS_FIX, WorkState.CLOSED}),
        WorkState.PREPARING: frozenset({WorkState.SOLVING, WorkState.NEEDS_FIX, WorkState.CLOSED}),
        WorkState.SOLVING: frozenset(
            {
                WorkState.ENGINE_WAITING_APPROVAL,
                WorkState.PATCH_COLLECTING,
                WorkState.NEEDS_FIX,
                WorkState.CLOSED,
            }
        ),
        WorkState.ENGINE_WAITING_APPROVAL: frozenset(
            {WorkState.SOLVING, WorkState.NEEDS_FIX, WorkState.CLOSED}
        ),
        WorkState.PATCH_COLLECTING: frozenset(
            {WorkState.PATCHED, WorkState.NEEDS_FIX, WorkState.CLOSED}
        ),
        WorkState.PATCHED: frozenset({WorkState.VERIFYING, WorkState.NEEDS_FIX, WorkState.CLOSED}),
        WorkState.VERIFYING: frozenset({WorkState.VERIFIED, WorkState.NEEDS_FIX, WorkState.CLOSED}),
        WorkState.VERIFIED: frozenset(
            {WorkState.REVIEW_PENDING, WorkState.NEEDS_FIX, WorkState.CLOSED}
        ),
        WorkState.REVIEW_PENDING: frozenset(
            {WorkState.APPROVED, WorkState.NEEDS_FIX, WorkState.CLOSED}
        ),
        WorkState.APPROVED: frozenset(
            {WorkState.PUBLISH_RESERVED, WorkState.NEEDS_FIX, WorkState.CLOSED}
        ),
        WorkState.PUBLISH_RESERVED: frozenset({WorkState.PUBLISHED}),
        WorkState.PUBLISHED: frozenset({WorkState.CI_RUNNING, WorkState.CLOSED}),
        WorkState.CI_RUNNING: frozenset({WorkState.MERGED, WorkState.CLOSED}),
        WorkState.MERGED: frozenset(),
        WorkState.CLOSED: frozenset(),
        # Retrying is deliberately available only through WorkItemRepository.retry().
        WorkState.NEEDS_FIX: frozenset({WorkState.CLOSED}),
    }
)


def allowed_transitions(state: WorkState) -> frozenset[WorkState]:
    """Return the immutable allowlist for ``state``."""
    return _ALLOWED_TRANSITIONS[WorkState(state)]


def validate_transition(current: WorkState, target: WorkState) -> None:
    """Fail closed unless ``current -> target`` is explicitly allowed."""
    current = WorkState(current)
    target = WorkState(target)
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidWorkTransitionError(
            f"Illegal WorkItem transition: {current.value} -> {target.value}"
        )
