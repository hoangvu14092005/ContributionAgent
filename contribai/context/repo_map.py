"""Token-aware repository map built from symbol definitions and references."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from contribai.context.symbol_index import Symbol, SymbolIndex


@dataclass(frozen=True, slots=True)
class RepoMapEntry:
    """Ranked file-level map entry."""

    path: str
    symbols: tuple[Symbol, ...]
    score: float


@dataclass(frozen=True, slots=True)
class RepoMap:
    """Rendered map plus structured entries for downstream localization."""

    text: str
    entries: tuple[RepoMapEntry, ...]
    token_count: int

    def __str__(self) -> str:
        return self.text


class RepoMapBuilder:
    """Rank definitions/references while enforcing a deterministic token cap."""

    def __init__(self, *, max_context_tokens: int = 4_000) -> None:
        if max_context_tokens <= 0:
            raise ValueError("max_context_tokens must be positive")
        self.max_context_tokens = max_context_tokens

    def build(
        self,
        files: Mapping[str, str],
        symbol_index: SymbolIndex | None = None,
        *,
        max_context_tokens: int | None = None,
    ) -> RepoMap:
        index = symbol_index or SymbolIndex.build(files)
        cap = max_context_tokens or self.max_context_tokens
        by_path: dict[str, list[Symbol]] = {path: [] for path in files}
        for symbol in index:
            by_path.setdefault(symbol.path, []).append(symbol)

        entries = []
        for path, symbols in by_path.items():
            depth = path.count("/")
            score = sum(2.0 + min(symbol.references, 20) * 0.1 for symbol in symbols)
            score += max(0.0, 3.0 - depth * 0.25)
            if path.rsplit("/", 1)[-1].lower() in {"main.py", "app.py", "index.ts", "lib.rs"}:
                score += 2.0
            entries.append(RepoMapEntry(path, tuple(sorted(symbols, key=_symbol_key)), score))
        entries.sort(key=lambda entry: (-entry.score, entry.path))

        lines: list[str] = []
        max_chars = cap * 4
        used = 0
        selected: list[RepoMapEntry] = []
        for entry in entries:
            if entry.symbols:
                definitions = ", ".join(
                    f"{symbol.name}({symbol.kind})@{symbol.line_start}" for symbol in entry.symbols
                )
                line = f"{entry.path} [score={entry.score:.2f}]: {definitions}"
            else:
                line = f"{entry.path} [score={entry.score:.2f}]: (no indexed symbols)"
            extra = len(line) + (1 if lines else 0)
            if used + extra > max_chars:
                continue
            lines.append(line)
            selected.append(entry)
            used += extra

        text = "\n".join(lines)
        return RepoMap(text=text, entries=tuple(selected), token_count=(len(text) + 3) // 4)


def build_repo_map(
    files: Mapping[str, str],
    *,
    symbol_index: SymbolIndex | None = None,
    max_context_tokens: int = 4_000,
) -> RepoMap:
    """Convenience function for callers that do not need a builder instance."""
    return RepoMapBuilder(max_context_tokens=max_context_tokens).build(
        files,
        symbol_index,
    )


def _symbol_key(symbol: Symbol) -> tuple[int, str, str]:
    return symbol.line_start, symbol.name, symbol.kind


__all__ = ["RepoMap", "RepoMapBuilder", "RepoMapEntry", "build_repo_map"]
