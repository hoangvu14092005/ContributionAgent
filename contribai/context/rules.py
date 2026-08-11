"""Repository instruction discovery and deterministic rule resolution."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

_RULE_NAMES = frozenset(
    {
        "AGENTS.md",
        "CLAUDE.md",
        "CONTRIBUTING.md",
        "CONTRIBUTING.rst",
        "PULL_REQUEST_TEMPLATE.md",
    }
)
_SKIP_PARTS = frozenset({".git", "node_modules", "vendor", "dist", "build", "__pycache__"})


@dataclass(frozen=True, slots=True)
class RuleDocument:
    """One discovered instruction document."""

    path: str
    content: str


@dataclass(frozen=True, slots=True)
class ResolvedRepoRules:
    """Merged repository rules consumed by analyzers, engines and review."""

    documents: tuple[RuleDocument, ...] = ()
    instructions: str = ""
    required_checks: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    requires_issue_link: bool = False
    commit_convention: str = "default"
    required_pr_sections: tuple[str, ...] = ()
    coding_style: str = ""

    def applies_to(self, path: str) -> bool:
        """Return whether any explicit forbidden path rule matches a file."""
        return any(fnmatch(path, pattern) or pattern in path for pattern in self.forbidden_paths)

    def instructions_for(self, path: str) -> str:
        """Return global instructions plus path-specific warnings."""
        parts = [self.instructions]
        if self.applies_to(path):
            parts.append(f"Do not modify {path}; it matches a protected path rule.")
        return "\n\n".join(part for part in parts if part)

    def to_prompt_context(self, max_chars: int | None = None) -> str:
        """Format rules in stable source order for an LLM prompt."""
        parts = ["## Repository Rules", self.instructions]
        if self.required_checks:
            parts.append("Required checks: " + "; ".join(self.required_checks))
        if self.forbidden_paths:
            parts.append("Protected paths: " + ", ".join(self.forbidden_paths))
        if self.requires_issue_link:
            parts.append("Every pull request must link an existing issue.")
        if self.coding_style:
            parts.append("Coding style: " + self.coding_style)
        text = "\n".join(part for part in parts if part)
        if max_chars is not None:
            return text[:max_chars]
        return text


class RepoRules:
    """Discover rule documents from a repository or an in-memory file map."""

    def __init__(self, documents: Iterable[RuleDocument] = ()) -> None:
        self._documents = tuple(sorted(documents, key=lambda doc: (doc.path.count("/"), doc.path)))

    @classmethod
    def from_files(cls, files: dict[str, str]) -> RepoRules:
        documents = []
        for path, content in files.items():
            normalized = path.replace("\\", "/")
            parts = set(normalized.split("/"))
            name = normalized.rsplit("/", 1)[-1]
            if any(part in _SKIP_PARTS for part in parts):
                continue
            if name in _RULE_NAMES or normalized.startswith((".agents/", ".claude/")):
                documents.append(RuleDocument(normalized, content))
        return cls(documents)

    @classmethod
    def discover(cls, root: Path) -> RepoRules:
        """Read bounded rule files without traversing generated/vendor trees."""
        root = Path(root).resolve()
        documents: list[RuleDocument] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.stat().st_size > 256_000:
                continue
            relative = path.relative_to(root).as_posix()
            if any(part in _SKIP_PARTS for part in relative.split("/")):
                continue
            name = path.name
            if name not in _RULE_NAMES and not relative.startswith((".agents/", ".claude/")):
                continue
            try:
                documents.append(RuleDocument(relative, path.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError):
                continue
        return cls(documents)

    @classmethod
    def from_documents(cls, files: dict[str, str]) -> RepoRules:
        """Alias used by callers that already call documents rather than files."""
        return cls.from_files(files)

    @property
    def documents(self) -> tuple[RuleDocument, ...]:
        return self._documents

    def resolve(self) -> ResolvedRepoRules:
        instructions = "\n\n".join(
            f"### {document.path}\n{document.content.strip()}"
            for document in self._documents
            if document.content.strip()
        )
        text = instructions.lower()
        required_checks = self._extract_required_checks(text)
        forbidden_paths = self._extract_forbidden_paths(instructions)
        requires_issue_link = self._detect_issue_link_requirement(text)
        commit_convention = self._detect_commit_convention(text)
        required_pr_sections = self._extract_pr_sections(instructions)
        coding_style = self._extract_coding_style(instructions)
        return ResolvedRepoRules(
            documents=self._documents,
            instructions=instructions,
            required_checks=required_checks,
            forbidden_paths=forbidden_paths,
            requires_issue_link=requires_issue_link,
            commit_convention=commit_convention,
            required_pr_sections=required_pr_sections,
            coding_style=coding_style,
        )

    @staticmethod
    def _extract_required_checks(text: str) -> tuple[str, ...]:
        checks = []
        for match in re.finditer(r"(?:run|execute|pass|must run)\s+([^\n.!?]{2,100})", text):
            value = match.group(1).strip(" `")
            if any(token in value for token in ("test", "lint", "format", "typecheck", "check")):
                checks.append(value)
        return tuple(dict.fromkeys(checks))

    @staticmethod
    def _extract_forbidden_paths(text: str) -> tuple[str, ...]:
        patterns: list[str] = []
        for line in text.splitlines():
            if not re.search(
                r"(?:never|do not|don't|must not)\s+(?:modify|edit|change)",
                line,
                re.IGNORECASE,
            ):
                continue
            values = re.findall(r"[`\"']([^`\"']+)[`\"']", line)
            if not values:
                values = re.findall(
                    r"(?:modify|edit|change)\s+([\w./*\-]+)",
                    line,
                    re.IGNORECASE,
                )
            for value in values:
                if value.lower() not in {"modify", "edit", "change", "files"}:
                    patterns.append(value.strip())
        return tuple(dict.fromkeys(patterns))

    @staticmethod
    def _detect_issue_link_requirement(text: str) -> bool:
        clauses = re.split(r"(?:[.!?]\s*|\n+)", text)
        required = re.compile(
            r"(?:pull requests?|prs?).*(?:must|shall|required).*(?:link|reference|close|fix).*issue"
            r"|(?:must|shall|required).*(?:open|create|file).*issue.*before.*(?:pull request|pr)"
        )
        conditional = re.compile(r"(?:only if|when applicable|not all|not every)")
        return any(required.search(clause) and not conditional.search(clause) for clause in clauses)

    @staticmethod
    def _detect_commit_convention(text: str) -> str:
        if "angular commit" in text or re.search(r"(?:feat|fix)\([^\n)]+\):", text):
            return "angular"
        if "conventional commit" in text or re.search(r"(?:feat|fix|chore):", text):
            return "conventional"
        return "default"

    @staticmethod
    def _extract_pr_sections(text: str) -> tuple[str, ...]:
        sections = re.findall(r"^#{1,3}\s+(.+)$", text, re.MULTILINE)
        return tuple(dict.fromkeys(section.strip() for section in sections))

    @staticmethod
    def _extract_coding_style(text: str) -> str:
        lines = [
            line.strip()
            for line in text.splitlines()
            if re.search(r"\b(?:style|indent|format|quote|naming|convention)\b", line)
        ]
        return " ".join(dict.fromkeys(lines))
