"""Resolve symbol and line-level edit locations from indexed source."""

from __future__ import annotations

import re
from dataclasses import dataclass

from contribai.context.context import ContributionContext
from contribai.context.symbol_index import Symbol
from contribai.localization.models import ContributionTask


@dataclass(frozen=True, slots=True)
class EditLocation:
    """A concrete source span proposed for a localization candidate."""

    path: str
    symbol: str | None
    line_start: int | None
    line_end: int | None
    evidence: tuple[str, ...] = ()
    score: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(dict.fromkeys(self.evidence)))


def find_edit_locations(
    task: ContributionTask,
    context: ContributionContext,
    path: str,
    *,
    max_locations: int = 6,
) -> tuple[EditLocation, ...]:
    """Find exact lines for one already-ranked file.

    Explicit task lines win. Otherwise symbol definitions are preferred, then
    lines containing task terms, with a deterministic first-line fallback.
    """
    if max_locations <= 0:
        return ()
    content = context.repo_snapshot.files.get(path) or context.relevant_files.get(path, "")
    lines = content.splitlines()
    symbols = context.symbol_index.for_path(path)
    query_terms = _query_terms(task.query_text)
    locations: list[EditLocation] = []

    if task.line_start is not None:
        line_end = task.line_end or task.line_start
        selected_symbol = _symbol_covering(symbols, task.line_start, line_end)
        evidence = [f"explicit task line {task.line_start}-{line_end}"]
        if selected_symbol:
            evidence.append(f"indexed symbol {selected_symbol.name}")
        locations.append(
            EditLocation(
                path,
                selected_symbol.name if selected_symbol else None,
                task.line_start,
                line_end,
                tuple(evidence),
                100.0,
            )
        )

    for symbol in symbols:
        score = 0.0
        evidence: list[str] = [f"indexed {symbol.kind} definition at line {symbol.line_start}"]
        leaf = symbol.name.rsplit(".", 1)[-1].lower()
        if task.symbol and _same_symbol(symbol.name, task.symbol):
            score += 100.0
            evidence.append(f"symbol matches task hint '{task.symbol}'")
        matching_terms = [term for term in query_terms if term in leaf]
        if matching_terms:
            score += 35.0 + len(matching_terms) * 4.0
            evidence.append("task terms match symbol: " + ", ".join(matching_terms[:3]))
        if task.file_path and path == task.file_path:
            score += 10.0
            evidence.append("file matches task path")
        locations.append(
            EditLocation(
                path,
                symbol.name,
                symbol.line_start,
                symbol.line_end,
                tuple(evidence),
                score,
            )
        )

    for line_number, line in enumerate(lines, start=1):
        matching_terms = [term for term in query_terms if term in line.lower()]
        if not matching_terms:
            continue
        locations.append(
            EditLocation(
                path,
                None,
                line_number,
                line_number,
                ("task terms found in source line: " + ", ".join(matching_terms[:3]),),
                15.0 + len(matching_terms) * 2.0,
            )
        )

    if not locations and lines:
        first_nonempty = next(
            (index for index, line in enumerate(lines, start=1) if line.strip()), 1
        )
        locations.append(
            EditLocation(
                path, None, first_nonempty, first_nonempty, ("file has source content",), 1.0
            )
        )
    return tuple(_dedupe_and_sort(locations)[:max_locations])


def _dedupe_and_sort(locations: list[EditLocation]) -> list[EditLocation]:
    unique: dict[tuple[str, str | None, int | None, int | None], EditLocation] = {}
    for location in locations:
        key = (location.path, location.symbol, location.line_start, location.line_end)
        previous = unique.get(key)
        if previous is None or location.score > previous.score:
            unique[key] = location
    return sorted(
        unique.values(),
        key=lambda item: (-item.score, item.line_start or 0, item.symbol or ""),
    )


def _symbol_covering(symbols: tuple[Symbol, ...], start: int, end: int) -> Symbol | None:
    candidates = [
        symbol
        for symbol in symbols
        if symbol.line_start <= start and (symbol.line_end or symbol.line_start) >= end
    ]
    return (
        min(candidates, key=lambda symbol: (symbol.line_end - symbol.line_start, symbol.name))
        if candidates
        else None
    )


def _same_symbol(actual: str, expected: str) -> bool:
    return (
        actual.lower() == expected.lower()
        or actual.rsplit(".", 1)[-1].lower() == expected.rsplit(".", 1)[-1].lower()
    )


def _query_terms(text: str) -> tuple[str, ...]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text.lower())
    stop_words = {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "from",
        "into",
        "change",
        "fix",
        "update",
        "issue",
        "method",
        "function",
        "file",
        "value",
        "return",
    }
    return tuple(dict.fromkeys(word for word in words if word not in stop_words))


__all__ = ["EditLocation", "find_edit_locations"]
