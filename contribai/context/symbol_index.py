"""Optional-tree-sitter-compatible symbol index with stdlib parsers."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Symbol:
    """A language-neutral definition/reference summary."""

    path: str
    name: str
    kind: str
    line_start: int
    line_end: int
    signature: str = ""
    references: int = 0

    @property
    def qualified_name(self) -> str:
        return f"{self.path}:{self.name}"


class SymbolIndex:
    """Deterministic index over definitions from common source languages."""

    def __init__(self, symbols: Iterable[Symbol] = ()) -> None:
        self._symbols = tuple(
            sorted(symbols, key=lambda item: (item.path, item.line_start, item.name, item.kind))
        )

    @classmethod
    def build(cls, files: Mapping[str, str]) -> SymbolIndex:
        symbols: list[Symbol] = []
        for path, content in sorted(files.items()):
            symbols.extend(_index_file(path, content))
        return cls(symbols)

    @property
    def symbols(self) -> tuple[Symbol, ...]:
        return self._symbols

    def __iter__(self):
        return iter(self._symbols)

    def __len__(self) -> int:
        return len(self._symbols)

    def find(self, query: str, *, limit: int = 20) -> list[Symbol]:
        """Find exact, qualified or substring symbol matches."""
        normalized = query.lower()
        exact = [
            symbol
            for symbol in self._symbols
            if symbol.name.lower() == normalized or symbol.qualified_name.lower() == normalized
        ]
        if exact:
            return exact[:limit]
        return [
            symbol
            for symbol in self._symbols
            if normalized in symbol.name.lower() or normalized in symbol.path.lower()
        ][:limit]

    def for_path(self, path: str) -> tuple[Symbol, ...]:
        return tuple(symbol for symbol in self._symbols if symbol.path == path)

    def to_prompt(self, *, max_tokens: int = 4_000) -> str:
        max_chars = max_tokens * 4
        lines = []
        for symbol in self._symbols:
            signature = f" — {symbol.signature}" if symbol.signature else ""
            lines.append(
                f"{symbol.path}:{symbol.line_start}-{symbol.line_end} "
                f"{symbol.kind} {symbol.name} refs={symbol.references}{signature}"
            )
        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[:max_chars] + "\n... [truncated]"


def _index_file(path: str, content: str) -> list[Symbol]:
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return _index_python(path, content)
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"}:
        return _index_javascript(path, content)
    if suffix == ".go":
        return _index_regex(
            path, content, [(r"\bfunc\s+(\w+)", "function"), (r"\btype\s+(\w+)", "type")]
        )
    if suffix == ".rs":
        return _index_regex(
            path,
            content,
            [(r"\bfn\s+(\w+)", "function"), (r"\b(?:struct|enum|trait)\s+(\w+)", "type")],
        )
    return []


def _index_python(path: str, content: str) -> list[Symbol]:
    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError:
        return _index_regex(
            path,
            content,
            [(r"^\s*class\s+(\w+)", "class"), (r"^\s*(?:async\s+)?def\s+(\w+)", "function")],
        )
    symbols: list[Symbol] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.class_stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            line_start = node.lineno
            line_end = getattr(node, "end_lineno", line_start)
            symbols.append(
                Symbol(
                    path,
                    node.name,
                    "class",
                    line_start,
                    line_end,
                    f"class {node.name}",
                    max(0, content.count(node.name) - 1),
                )
            )
            self.class_stack.append(node.name)
            self.generic_visit(node)
            self.class_stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function(node, "function")

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function(node, "function")

        def _visit_function(
            self,
            node: ast.FunctionDef | ast.AsyncFunctionDef,
            kind: str,
        ) -> None:
            name = ".".join((*self.class_stack, node.name))
            line_start = node.lineno
            line_end = getattr(node, "end_lineno", line_start)
            symbols.append(
                Symbol(
                    path,
                    name,
                    kind,
                    line_start,
                    line_end,
                    _python_signature(node),
                    max(0, content.count(node.name) - 1),
                )
            )
            self.generic_visit(node)

    Visitor().visit(tree)
    return symbols


def _python_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = [argument.arg for argument in node.args.args]
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{prefix}def {node.name}({', '.join(args)})"


def _index_javascript(path: str, content: str) -> list[Symbol]:
    patterns = [
        (r"\b(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
        (r"\bclass\s+(\w+)", "class"),
        (r"\b(?:interface|type)\s+(\w+)", "type"),
        (r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(?", "variable"),
    ]
    return _index_regex(path, content, patterns)


def _index_regex(path: str, content: str, patterns: list[tuple[str, str]]) -> list[Symbol]:
    symbols: list[Symbol] = []
    for pattern, kind in patterns:
        for match in re.finditer(pattern, content, re.MULTILINE):
            name = match.group(1)
            line_start = content.count("\n", 0, match.start()) + 1
            line_end = line_start
            symbols.append(
                Symbol(
                    path,
                    name,
                    kind,
                    line_start,
                    line_end,
                    match.group(0).strip(),
                    max(0, content.count(name) - 1),
                )
            )
    return sorted(symbols, key=lambda item: (item.line_start, item.name, item.kind))


__all__ = ["Symbol", "SymbolIndex"]
