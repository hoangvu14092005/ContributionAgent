"""Canonical repository-path safety rules shared by analysis and publishing."""

from __future__ import annotations

from pathlib import PurePosixPath

PROTECTED_META_FILES: frozenset[str] = frozenset(
    {
        "CONTRIBUTING.md",
        ".github/CONTRIBUTING.md",
        "docs/CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
        ".github/CODE_OF_CONDUCT.md",
        "LICENSE",
        "LICENSE.md",
        "LICENSE.txt",
        ".github/FUNDING.yml",
        ".github/SECURITY.md",
        "SECURITY.md",
        ".github/CODEOWNERS",
        ".all-contributorsrc",
    }
)


def normalize_repository_path(path: str) -> str:
    """Normalize a repository-relative path without resolving traversal."""
    normalized = str(path).replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def is_invalid_repository_path(path: str) -> bool:
    """Return whether a path is absolute, empty, NUL-containing, or traverses upward."""
    normalized = normalize_repository_path(path)
    if not normalized or "\x00" in normalized:
        return True
    pure_path = PurePosixPath(normalized)
    return pure_path.is_absolute() or ".." in pure_path.parts


def is_protected_meta_path(path: str) -> bool:
    """Return whether the complete normalized path is governance/protected metadata."""
    normalized = normalize_repository_path(path)
    protected = {item.casefold() for item in PROTECTED_META_FILES}
    return normalized.casefold() in protected


__all__ = [
    "PROTECTED_META_FILES",
    "is_invalid_repository_path",
    "is_protected_meta_path",
    "normalize_repository_path",
]
