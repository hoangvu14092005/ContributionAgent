"""Contracts for hierarchical task localization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ContributionTask:
    """Repair request details used by deterministic localization."""

    title: str
    description: str = ""
    file_path: str | None = None
    symbol: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    language: str | None = None
    hints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "hints", tuple(self.hints))

    @property
    def query_text(self) -> str:
        """Return all human and machine hints as one searchable string."""
        return " ".join(
            value
            for value in (self.title, self.description, self.file_path, self.symbol, *self.hints)
            if value
        )

    @classmethod
    def from_finding(cls, finding: Any) -> ContributionTask:
        """Adapt a core Finding without coupling core models to localization."""
        return cls(
            title=finding.title,
            description=finding.description,
            file_path=finding.file_path or None,
            line_start=finding.line_start,
            line_end=finding.line_end,
            hints=tuple(value for value in (finding.suggestion,) if value),
        )

    @classmethod
    def from_issue(cls, issue: Any) -> ContributionTask:
        """Adapt a GitHub issue into a localization task."""
        return cls(
            title=issue.title,
            description=issue.body or "",
            hints=tuple(issue.labels),
        )


@dataclass(frozen=True, slots=True)
class LocalizationCandidate:
    """One ranked file/symbol/edit-location hypothesis with evidence."""

    path: str
    symbol: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    evidence: tuple[str, ...] = ()
    score: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(dict.fromkeys(self.evidence)))

    @property
    def edit_location(self) -> tuple[int | None, int | None]:
        """Return the exact line span, if the candidate has one."""
        return self.line_start, self.line_end


@dataclass(frozen=True, slots=True)
class LocalizationSet:
    """Ordered N-best localization hypotheses."""

    candidates: tuple[LocalizationCandidate, ...] = ()

    def __post_init__(self) -> None:
        ordered = sorted(
            self.candidates,
            key=lambda item: (-item.score, item.path, item.line_start or 0, item.symbol or ""),
        )
        object.__setattr__(self, "candidates", tuple(ordered))

    def __iter__(self):
        return iter(self.candidates)

    def __len__(self) -> int:
        return len(self.candidates)

    @property
    def best(self) -> LocalizationCandidate | None:
        """Return the highest-scoring candidate without discarding alternatives."""
        return self.candidates[0] if self.candidates else None

    def top(self, limit: int = 5) -> tuple[LocalizationCandidate, ...]:
        """Return the first N candidates."""
        if limit < 0:
            raise ValueError("limit must be non-negative")
        return self.candidates[:limit]

    def recall_at(
        self,
        limit: int,
        expected_path: str,
        *,
        symbol: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> float:
        """Return binary recall for an expected file/symbol/location at K."""
        if limit <= 0:
            return 0.0
        for candidate in self.candidates[:limit]:
            if candidate.path != expected_path:
                continue
            if symbol and not _same_symbol(candidate.symbol, symbol):
                continue
            if line_start is not None and not _overlaps(
                candidate.line_start, candidate.line_end, line_start, line_end
            ):
                continue
            return 1.0
        return 0.0

    def to_prompt(self, *, max_candidates: int = 5) -> str:
        """Render candidates and evidence for a downstream repair prompt."""
        lines = []
        for index, candidate in enumerate(self.candidates[:max_candidates], start=1):
            location = ""
            if candidate.line_start is not None:
                location = f":{candidate.line_start}-{candidate.line_end or candidate.line_start}"
            symbol = f"::{candidate.symbol}" if candidate.symbol else ""
            evidence = "; ".join(candidate.evidence) or "no evidence"
            lines.append(
                f"{index}. {candidate.path}{location}{symbol} ({candidate.score:.2f}) — {evidence}"
            )
        return "\n".join(lines)


def _same_symbol(actual: str | None, expected: str) -> bool:
    if not actual:
        return False
    actual_normalized = actual.rsplit(".", 1)[-1].lower()
    expected_normalized = expected.rsplit(".", 1)[-1].lower()
    return actual_normalized == expected_normalized or actual.lower() == expected.lower()


def _overlaps(
    actual_start: int | None,
    actual_end: int | None,
    expected_start: int,
    expected_end: int | None,
) -> bool:
    if actual_start is None:
        return False
    actual_end = actual_end or actual_start
    expected_end = expected_end or expected_start
    return actual_start <= expected_end and expected_start <= actual_end


__all__ = ["ContributionTask", "LocalizationCandidate", "LocalizationSet"]
