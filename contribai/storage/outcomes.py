"""Outcome persistence and bounded Contribution Value measurements."""

from __future__ import annotations

import asyncio
import json
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class OutcomeStatus(StrEnum):
    """Lifecycle outcomes used for learning maintainer and repository signals."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MERGED = "merged"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class ContributionOutcome:
    """Evidence record for one contribution attempt or submitted PR."""

    repo: str
    work_id: str
    contribution_type: str
    status: OutcomeStatus
    issue_number: int | None = None
    pr_number: int | None = None
    candidate_hash: str = ""
    requested_changes: int = 0
    ci_passed: bool | None = None
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None
    review_latency_hours: float | None = None
    cost_usd: float = 0.0
    wall_time_sec: float = 0.0
    tool_failures: int = 0
    policy_denials: int = 0
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.repo.strip() or not self.work_id.strip():
            raise ValueError("contribution outcome requires repo and work_id")
        if not self.contribution_type.strip():
            raise ValueError("contribution outcome requires contribution_type")
        object.__setattr__(self, "status", OutcomeStatus(self.status))
        for name in ("requested_changes", "tool_failures", "policy_denials"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        for name in ("cost_usd", "wall_time_sec"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.review_latency_hours is not None and self.review_latency_hours < 0:
            raise ValueError("review_latency_hours must be non-negative")
        for value in (self.submitted_at, self.reviewed_at):
            if value is not None and value.tzinfo is None:
                raise ValueError("outcome timestamps must be timezone-aware")
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def accepted(self) -> bool:
        """Whether the contribution reached an accepted PR outcome."""
        return self.status in {OutcomeStatus.ACCEPTED, OutcomeStatus.MERGED}

    @property
    def merged(self) -> bool:
        """Whether the contribution was merged."""
        return self.status is OutcomeStatus.MERGED

    @property
    def review_hours(self) -> float | None:
        """Return explicit or timestamp-derived review latency."""
        if self.review_latency_hours is not None:
            return self.review_latency_hours
        if self.submitted_at is None or self.reviewed_at is None:
            return None
        return max(0.0, (self.reviewed_at - self.submitted_at).total_seconds() / 3600)

    def to_mapping(self) -> dict[str, object]:
        """Return a JSON-compatible detached representation."""
        return {
            "repo": self.repo,
            "work_id": self.work_id,
            "contribution_type": self.contribution_type,
            "status": self.status.value,
            "issue_number": self.issue_number,
            "pr_number": self.pr_number,
            "candidate_hash": self.candidate_hash,
            "requested_changes": self.requested_changes,
            "ci_passed": self.ci_passed,
            "submitted_at": _timestamp(self.submitted_at),
            "reviewed_at": _timestamp(self.reviewed_at),
            "review_latency_hours": self.review_hours,
            "cost_usd": self.cost_usd,
            "wall_time_sec": self.wall_time_sec,
            "tool_failures": self.tool_failures,
            "policy_denials": self.policy_denials,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> ContributionOutcome:
        """Load a persisted or event-shaped outcome mapping."""
        return cls(
            repo=str(values["repo"]),
            work_id=str(values["work_id"]),
            contribution_type=str(
                values.get("contribution_type", values.get("pr_type", "unknown"))
            ),
            status=OutcomeStatus(values["status"] if "status" in values else values["outcome"]),
            issue_number=_optional_int(values.get("issue_number")),
            pr_number=_optional_int(values.get("pr_number")),
            candidate_hash=str(values.get("candidate_hash", "")),
            requested_changes=int(values.get("requested_changes", 0)),
            ci_passed=_optional_bool(values.get("ci_passed")),
            submitted_at=_parse_timestamp(values.get("submitted_at")),
            reviewed_at=_parse_timestamp(values.get("reviewed_at")),
            review_latency_hours=_optional_float(values.get("review_latency_hours")),
            cost_usd=float(values.get("cost_usd", 0.0)),
            wall_time_sec=float(values.get("wall_time_sec", 0.0)),
            tool_failures=int(values.get("tool_failures", 0)),
            policy_denials=int(values.get("policy_denials", 0)),
            metadata=values.get("metadata", {})
            if isinstance(values.get("metadata", {}), Mapping)
            else {},
        )


@dataclass(frozen=True, slots=True)
class OutcomeSummary:
    """Smoothed repository outcome signals with an evidence sufficiency flag."""

    repo: str
    sample_size: int
    evidence_sufficient: bool
    acceptance_probability: float
    merge_probability: float
    avg_review_hours: float
    requested_changes_rate: float
    ci_pass_rate: float
    avg_cost_usd: float
    avg_wall_time_sec: float
    tool_failure_rate: float
    policy_denial_rate: float
    preferred_types: tuple[str, ...] = ()
    rejected_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContributionBenchmark:
    """Aggregate quality and value metrics for a benchmark fixture set."""

    sample_count: int
    localization_recall_at_1: float
    localization_recall_at_3: float
    localization_recall_at_5: float
    patch_apply_rate: float
    baseline_preservation_rate: float
    syntax_pass_rate: float
    targeted_test_pass_rate: float
    regression_test_pass_rate: float
    lint_pass_rate: float
    typecheck_pass_rate: float
    security_pass_rate: float
    avg_cost_usd: float
    avg_wall_time_sec: float
    acceptance_rate: float
    merge_rate: float
    median_review_hours: float
    avg_tool_calls: float
    policy_denials: int

    @classmethod
    def from_samples(cls, samples: Iterable[Mapping[str, object]]) -> ContributionBenchmark:
        """Compute deterministic rates from JSON-compatible benchmark samples."""
        values = list(samples)
        if not values:
            return cls(*(0 for _ in range(19)))
        count = len(values)

        def rate(key: str) -> float:
            return sum(bool(sample.get(key, False)) for sample in values) / count

        def average(key: str) -> float:
            return round(
                sum(float(sample.get(key, 0.0) or 0.0) for sample in values) / count,
                6,
            )

        reviews = sorted(
            float(sample.get("review_hours", 0.0) or 0.0)
            for sample in values
            if sample.get("review_hours") is not None
        )
        policy_denials = sum(int(sample.get("policy_denials", 0) or 0) for sample in values)
        return cls(
            sample_count=count,
            localization_recall_at_1=average("localization_recall_at_1"),
            localization_recall_at_3=average("localization_recall_at_3"),
            localization_recall_at_5=average("localization_recall_at_5"),
            patch_apply_rate=rate("patch_applied"),
            baseline_preservation_rate=rate("baseline_preserved"),
            syntax_pass_rate=rate("syntax_passed"),
            targeted_test_pass_rate=rate("targeted_tests_passed"),
            regression_test_pass_rate=rate("regression_tests_passed"),
            lint_pass_rate=rate("lint_passed"),
            typecheck_pass_rate=rate("typecheck_passed"),
            security_pass_rate=rate("security_passed"),
            avg_cost_usd=average("cost_usd"),
            avg_wall_time_sec=average("wall_time_sec"),
            acceptance_rate=rate("accepted"),
            merge_rate=rate("merged"),
            median_review_hours=statistics.median(reviews) if reviews else 0.0,
            avg_tool_calls=average("tool_calls"),
            policy_denials=policy_denials,
        )


OUTCOME_SCHEMA = """
CREATE TABLE IF NOT EXISTS contribution_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    work_id TEXT NOT NULL,
    contribution_type TEXT NOT NULL,
    status TEXT NOT NULL,
    issue_number INTEGER,
    pr_number INTEGER,
    candidate_hash TEXT DEFAULT '',
    outcome_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE(work_id, status)
);
CREATE INDEX IF NOT EXISTS idx_contribution_outcomes_repo
ON contribution_outcomes(repo, recorded_at);
"""


class OutcomeStore:
    """SQLite repository for structured contribution outcomes."""

    def __init__(self, connection: Any, transaction_lock: asyncio.Lock | None = None) -> None:
        self._connection = connection
        self._lock = transaction_lock or asyncio.Lock()

    async def ensure_schema(self) -> None:
        async with self._lock:
            await self._connection.executescript(OUTCOME_SCHEMA)
            await self._connection.commit()

    async def record(self, outcome: ContributionOutcome) -> int:
        """Persist one idempotent outcome snapshot."""
        async with self._lock:
            cursor = await self._connection.execute(
                """INSERT INTO contribution_outcomes
                   (repo, work_id, contribution_type, status, issue_number,
                    pr_number, candidate_hash, outcome_json, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(work_id, status) DO UPDATE SET
                       outcome_json = excluded.outcome_json,
                       recorded_at = excluded.recorded_at,
                       candidate_hash = excluded.candidate_hash""",
                (
                    outcome.repo,
                    outcome.work_id,
                    outcome.contribution_type,
                    outcome.status.value,
                    outcome.issue_number,
                    outcome.pr_number,
                    outcome.candidate_hash,
                    json.dumps(outcome.to_mapping(), sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )
            await self._connection.commit()
            return int(cursor.lastrowid or 0)

    async def list(
        self,
        repo: str | None = None,
        *,
        limit: int = 200,
    ) -> list[ContributionOutcome]:
        """Return newest outcome snapshots, optionally scoped to one repo."""
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if repo:
            cursor = await self._connection.execute(
                "SELECT outcome_json FROM contribution_outcomes WHERE repo = "
                "? ORDER BY recorded_at DESC LIMIT ?",
                (repo, limit),
            )
        else:
            cursor = await self._connection.execute(
                "SELECT outcome_json FROM contribution_outcomes ORDER BY recorded_at DESC LIMIT ?",
                (limit,),
            )
        return [
            ContributionOutcome.from_mapping(json.loads(row[0])) for row in await cursor.fetchall()
        ]

    async def summarize(
        self,
        repo: str,
        *,
        evidence_threshold: int = 5,
    ) -> OutcomeSummary:
        """Aggregate outcomes while marking whether evidence is sufficient."""
        return summarize_outcomes(
            await self.list(repo),
            repo=repo,
            evidence_threshold=evidence_threshold,
        )


def summarize_outcomes(
    outcomes: Iterable[ContributionOutcome],
    *,
    repo: str,
    evidence_threshold: int = 5,
) -> OutcomeSummary:
    """Calculate smoothed rates without allowing tiny samples to dominate."""
    if evidence_threshold <= 0:
        raise ValueError("evidence_threshold must be positive")
    values = [item for item in outcomes if item.repo == repo]
    sample_size = len(values)
    prior_weight = float(evidence_threshold)
    accepted = sum(item.accepted for item in values)
    merged = sum(item.merged for item in values)
    acceptance_probability = (accepted + 0.5 * prior_weight) / (sample_size + prior_weight)
    merge_probability = (merged + 0.5 * prior_weight) / (sample_size + prior_weight)
    review_hours = [item.review_hours for item in values if item.review_hours is not None]
    ci_values = [item.ci_passed for item in values if item.ci_passed is not None]
    preferred = _types_for(values, accepted=True) if sample_size >= evidence_threshold else ()
    rejected = _types_for(values, accepted=False) if sample_size >= evidence_threshold else ()
    return OutcomeSummary(
        repo=repo,
        sample_size=sample_size,
        evidence_sufficient=sample_size >= evidence_threshold,
        acceptance_probability=acceptance_probability,
        merge_probability=merge_probability,
        avg_review_hours=sum(review_hours) / len(review_hours) if review_hours else 0.0,
        requested_changes_rate=(
            sum(item.requested_changes > 0 for item in values) / sample_size if sample_size else 0.0
        ),
        ci_pass_rate=sum(ci_values) / len(ci_values) if ci_values else 0.0,
        avg_cost_usd=sum(item.cost_usd for item in values) / sample_size if sample_size else 0.0,
        avg_wall_time_sec=(
            sum(item.wall_time_sec for item in values) / sample_size if sample_size else 0.0
        ),
        tool_failure_rate=(
            sum(item.tool_failures > 0 for item in values) / sample_size if sample_size else 0.0
        ),
        policy_denial_rate=(
            sum(item.policy_denials > 0 for item in values) / sample_size if sample_size else 0.0
        ),
        preferred_types=preferred,
        rejected_types=rejected,
    )


def _types_for(values: Iterable[ContributionOutcome], *, accepted: bool) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    for value in values:
        if value.accepted is not accepted:
            continue
        counts[value.contribution_type] = counts.get(value.contribution_type, 0) + 1
    return tuple(sorted(counts, key=lambda item: (-counts[item], item)))


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None and value != "" else None


def _optional_float(value: object) -> float | None:
    return float(value) if value is not None and value != "" else None


def _optional_bool(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "passed"}
    return bool(value)


__all__ = [
    "OUTCOME_SCHEMA",
    "ContributionBenchmark",
    "ContributionOutcome",
    "OutcomeStatus",
    "OutcomeStore",
    "OutcomeSummary",
    "summarize_outcomes",
]
