"""Immutable snapshots used by WorkItem persistence.

``BudgetSnapshot`` is the stable Task 5 persistence seam. Task 6 may replace it
with the full ``ExecutionBudget`` behavior while retaining this canonical JSON
representation at the storage boundary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeAlias, cast

from contribai.control.mode import ExecutionMode
from contribai.domain.state import WorkState

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    """Immutable, canonical, JSON-compatible execution budget snapshot."""

    _canonical_json: str

    @classmethod
    def from_mapping(cls, values: dict[str, JsonValue]) -> BudgetSnapshot:
        """Validate and freeze a budget mapping by canonical serialization."""
        if not isinstance(values, dict) or any(not isinstance(key, str) for key in values):
            raise TypeError("BudgetSnapshot requires a string-keyed mapping")
        try:
            canonical = json.dumps(
                values,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("BudgetSnapshot must contain only JSON-compatible values") from exc
        return cls(canonical)

    @classmethod
    def from_json(cls, value: str) -> BudgetSnapshot:
        """Load and canonicalize a persisted budget snapshot."""
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise ValueError("Persisted budget snapshot must be a JSON object")
        return cls.from_mapping(cast(dict[str, JsonValue], decoded))

    @classmethod
    def empty(cls) -> BudgetSnapshot:
        """Return an empty forward-compatible budget snapshot."""
        return cls("{}")

    def to_json(self) -> str:
        """Return the canonical storage representation."""
        return self._canonical_json

    def to_mapping(self) -> dict[str, JsonValue]:
        """Return a detached mutable copy without exposing shared state."""
        return cast(dict[str, JsonValue], json.loads(self._canonical_json))


@dataclass(frozen=True, slots=True)
class WorkItem:
    """Immutable versioned snapshot of one contribution run."""

    id: str
    repo: str
    issue_number: int | None
    mode: ExecutionMode
    state: WorkState
    attempt: int
    budget: BudgetSnapshot
    version: int
    created_at: str
    updated_at: str

    @classmethod
    def new(
        cls,
        *,
        work_id: str,
        repo: str,
        issue_number: int | None,
        mode: ExecutionMode,
        budget: BudgetSnapshot,
    ) -> WorkItem:
        """Create a new, unpersisted WorkItem snapshot."""
        if not work_id.strip():
            raise ValueError("work_id must not be empty")
        if not repo.strip():
            raise ValueError("repo must not be empty")
        if issue_number is not None and issue_number <= 0:
            raise ValueError("issue_number must be positive")
        now = datetime.now(UTC).isoformat()
        return cls(
            id=work_id,
            repo=repo,
            issue_number=issue_number,
            mode=ExecutionMode(mode),
            state=WorkState.DISCOVERED,
            attempt=1,
            budget=budget,
            version=0,
            created_at=now,
            updated_at=now,
        )
