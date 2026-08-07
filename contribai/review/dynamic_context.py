"""Bounded, changed-line-first context for PR review and feedback repair."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from contribai.context.rules import ResolvedRepoRules
from contribai.context.symbol_index import SymbolIndex
from contribai.core.models import Issue
from contribai.verification.models import VerificationReport


@dataclass(frozen=True, slots=True)
class ChangedLine:
    """One added/removed line from a unified diff."""

    path: str
    line: int | None
    content: str
    kind: str


@dataclass(frozen=True, slots=True)
class DynamicReviewContext:
    """Structured review context and its bounded prompt rendering."""

    changed_lines: tuple[ChangedLine, ...]
    enclosing_symbols: tuple[str, ...]
    repo_rules: str
    issue_context: str
    test_evidence: tuple[str, ...]
    text: str

    @property
    def prompt(self) -> str:
        """Alias for callers that pass context directly to an LLM."""
        return self.text


def build_dynamic_review_context(
    *,
    diff: str = "",
    files: Mapping[str, str] | None = None,
    repo_rules: ResolvedRepoRules | str | None = None,
    issue: Issue | str | None = None,
    verification: VerificationReport | Iterable[str] | None = None,
    max_chars: int = 12_000,
) -> DynamicReviewContext:
    """Build context ordered by changed lines, symbols, rules, issue and proof."""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    changed_lines = tuple(_parse_changed_lines(diff))
    symbols = _find_enclosing_symbols(changed_lines, files or {})
    rules_text = (
        repo_rules.to_prompt_context(max_chars=max_chars // 3)
        if isinstance(repo_rules, ResolvedRepoRules)
        else str(repo_rules or "")[: max_chars // 3]
    )
    issue_text = _render_issue(issue, max_chars // 4)
    evidence = tuple(_render_evidence(verification, max_chars // 4))

    sections = [
        _section("Changed lines", _render_changed_lines(changed_lines)),
        _section("Enclosing symbols", "\n".join(symbols)),
        _section("Repository rules", rules_text),
        _section("Issue", issue_text),
        _section("Verification evidence", "\n".join(evidence)),
    ]
    text = "\n\n".join(section for section in sections if section)
    return DynamicReviewContext(
        changed_lines=changed_lines,
        enclosing_symbols=symbols,
        repo_rules=rules_text,
        issue_context=issue_text,
        test_evidence=evidence,
        text=text[:max_chars],
    )


build_dynamic_pr_review_context = build_dynamic_review_context
DynamicPRReviewContext = DynamicReviewContext


def _parse_changed_lines(diff: str) -> list[ChangedLine]:
    lines: list[ChangedLine] = []
    path = ""
    new_line: int | None = None
    old_line: int | None = None
    for raw in diff.splitlines():
        if raw.startswith("+++ b/"):
            path = raw[6:]
            continue
        if raw.startswith("--- a/"):
            continue
        hunk = re.match(r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@", raw)
        if hunk:
            old_line = int(hunk.group("old"))
            new_line = int(hunk.group("new"))
            continue
        if not raw or raw[0] not in "+- " or raw.startswith(("+++", "---")):
            continue
        kind = raw[0]
        if kind == "+":
            lines.append(ChangedLine(path, new_line, raw[1:], "added"))
            if new_line is not None:
                new_line += 1
        elif kind == "-":
            lines.append(ChangedLine(path, old_line, raw[1:], "removed"))
            if old_line is not None:
                old_line += 1
        else:
            if old_line is not None:
                old_line += 1
            if new_line is not None:
                new_line += 1
    return lines


def _find_enclosing_symbols(
    lines: Iterable[ChangedLine], files: Mapping[str, str]
) -> tuple[str, ...]:
    index = SymbolIndex.build(files) if files else None
    if index is None:
        return ()
    names: list[str] = []
    for changed in lines:
        if changed.line is None:
            continue
        candidates = [
            symbol
            for symbol in index
            if symbol.path == changed.path and symbol.line_start <= changed.line
        ]
        if candidates:
            symbol = max(candidates, key=lambda item: (item.line_start, item.name))
            value = f"{symbol.path}:{symbol.line_start} {symbol.kind} {symbol.name}"
            if value not in names:
                names.append(value)
    return tuple(names)


def _render_changed_lines(lines: Iterable[ChangedLine]) -> str:
    return "\n".join(
        f"{line.path}:{line.line or '?'} [{line.kind}] {line.content}" for line in lines
    )


def _render_issue(issue: Issue | str | None, limit: int) -> str:
    if issue is None:
        return ""
    if isinstance(issue, Issue):
        text = f"#{issue.number}: {issue.title}\n{issue.body or ''}".strip()
    else:
        text = str(issue)
    return text[:limit]


def _render_evidence(
    verification: VerificationReport | Iterable[str] | None,
    limit: int,
) -> list[str]:
    if verification is None:
        return []
    if isinstance(verification, VerificationReport):
        return [
            f"{item.check}: status={item.status}, passed={item.passed}, output={item.output[:500]}"
            for item in verification.evidence
        ][: max(1, limit // 180)]
    return [str(item)[:500] for item in verification][: max(1, limit // 180)]


def _section(title: str, content: str) -> str:
    return f"## {title}\n{content}" if content else ""


__all__ = [
    "ChangedLine",
    "DynamicPRReviewContext",
    "DynamicReviewContext",
    "build_dynamic_pr_review_context",
    "build_dynamic_review_context",
]
