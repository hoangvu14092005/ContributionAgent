"""Hierarchical file, symbol and exact-location ranking."""

from __future__ import annotations

import re
from pathlib import Path

from contribai.context.context import ContributionContext
from contribai.context.symbol_index import Symbol
from contribai.localization.edit_locations import EditLocation, find_edit_locations
from contribai.localization.models import (
    ContributionTask,
    LocalizationCandidate,
    LocalizationSet,
)


class Localizer:
    """Produce N-best localization candidates without an LLM round trip."""

    def __init__(self, *, max_candidates: int = 12, max_locations_per_file: int = 4) -> None:
        if max_candidates <= 0 or max_locations_per_file <= 0:
            raise ValueError("localization limits must be positive")
        self.max_candidates = max_candidates
        self.max_locations_per_file = max_locations_per_file

    async def locate(
        self,
        task: ContributionTask,
        context: ContributionContext,
    ) -> LocalizationSet:
        """Rank file candidates, then symbols, then exact edit locations."""
        files = _source_files(context)
        if not files:
            return LocalizationSet()
        index = context.symbol_index
        map_scores = {entry.path: entry.score for entry in context.repo_map_entries}
        ranked_files = sorted(
            (
                (
                    path,
                    _score_file(
                        task, path, content, index.for_path(path), map_scores.get(path, 0.0)
                    ),
                )
                for path, content in files.items()
            ),
            key=lambda item: (-item[1][0], item[0]),
        )

        candidates: list[LocalizationCandidate] = []
        for path, (file_score, file_evidence) in ranked_files:
            locations = find_edit_locations(
                task,
                context,
                path,
                max_locations=self.max_locations_per_file,
            )
            if not locations:
                locations = (EditLocation(path, None, None, None, ("file candidate",), 0.0),)
            for location in locations:
                candidates.append(
                    LocalizationCandidate(
                        path=path,
                        symbol=location.symbol,
                        line_start=location.line_start,
                        line_end=location.line_end,
                        evidence=tuple((*file_evidence, *location.evidence)),
                        score=file_score + location.score,
                    )
                )

        candidates = _dedupe_candidates(candidates)
        return LocalizationSet(tuple(candidates[: self.max_candidates]))


def _source_files(context: ContributionContext) -> dict[str, str]:
    files = dict(context.repo_snapshot.files)
    files.update(context.relevant_files)
    return {path: content for path, content in sorted(files.items()) if _is_source(path)}


def _is_source(path: str) -> bool:
    return Path(path).suffix.lower() in {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".java",
        ".rb",
        ".php",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".swift",
        ".kt",
        ".vue",
        ".svelte",
    }


def _score_file(
    task: ContributionTask,
    path: str,
    content: str,
    symbols: tuple[Symbol, ...],
    map_score: float,
) -> tuple[float, tuple[str, ...]]:
    score = min(map_score, 100.0) * 0.2
    evidence: list[str] = []
    normalized_path = path.lower()
    query = task.query_text.lower()
    terms = _terms(query)

    if task.file_path:
        requested = task.file_path.replace("\\", "/").lower()
        if path.lower() == requested:
            score += 1_000.0
            evidence.append("file matches explicit task path")
        elif requested in normalized_path or normalized_path in requested:
            score += 300.0
            evidence.append("file partially matches explicit task path")

    path_matches = [term for term in terms if term in normalized_path]
    if path_matches:
        score += 25.0 * len(path_matches)
        evidence.append("path matches task terms: " + ", ".join(path_matches[:4]))

    symbol_matches = [
        symbol for symbol in symbols if any(_symbol_matches_term(symbol, term) for term in terms)
    ]
    if task.symbol and any(_same_symbol(symbol.name, task.symbol) for symbol in symbols):
        score += 300.0
        evidence.append(f"indexed symbol matches '{task.symbol}'")
    if symbol_matches:
        score += 35.0 * len(symbol_matches)
        evidence.append(
            "indexed symbols match task terms: "
            + ", ".join(symbol.name for symbol in symbol_matches[:4])
        )

    content_lower = content.lower()
    content_matches = [term for term in terms if term in content_lower]
    if content_matches:
        score += min(30.0, sum(content_lower.count(term) for term in content_matches) * 0.5)
        evidence.append("source contains task terms: " + ", ".join(content_matches[:4]))

    if _looks_like_test(path) and not any(term in query for term in ("test", "spec")):
        score -= 12.0
        evidence.append("test path deprioritized for non-test task")
    if not evidence:
        evidence.append("source file from repository snapshot")
    return score, tuple(evidence)


def _dedupe_candidates(candidates: list[LocalizationCandidate]) -> list[LocalizationCandidate]:
    unique: dict[tuple[str, str | None, int | None, int | None], LocalizationCandidate] = {}
    for candidate in candidates:
        key = (candidate.path, candidate.symbol, candidate.line_start, candidate.line_end)
        previous = unique.get(key)
        if previous is None or candidate.score > previous.score:
            unique[key] = candidate
    return sorted(
        unique.values(),
        key=lambda item: (-item.score, item.path, item.line_start or 0, item.symbol or ""),
    )


def _terms(text: str) -> tuple[str, ...]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text)
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
        "safe",
        "safely",
    }
    return tuple(dict.fromkeys(word for word in words if word not in stop_words))


def _symbol_matches_term(symbol: Symbol, term: str) -> bool:
    name = symbol.name.lower()
    return term in name or name in term


def _same_symbol(actual: str, expected: str) -> bool:
    return (
        actual.lower() == expected.lower()
        or actual.rsplit(".", 1)[-1].lower() == expected.rsplit(".", 1)[-1].lower()
    )


def _looks_like_test(path: str) -> bool:
    normalized = path.lower()
    return (
        "/test" in normalized
        or normalized.startswith("test")
        or normalized.endswith("_test.py")
        or normalized.endswith(".test.ts")
    )


__all__ = ["Localizer"]
