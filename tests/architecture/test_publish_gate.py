"""Architecture tests for the single GitHub publishing authority."""

from __future__ import annotations

import ast
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parents[2]
_PRODUCTION_ROOT = _PROJECT_ROOT / "contribai"
_PUBLISHER_PATH = _PRODUCTION_ROOT / "publishing" / "github_publisher.py"
_GITHUB_WRITE_METHODS = frozenset(
    {
        "close_issue",
        "close_pull_request",
        "create_branch",
        "create_issue",
        "create_or_update_file",
        "create_pr_comment",
        "create_pr_review_comment_reply",
        "create_pull_request",
        "delete_repository",
        "fork_repository",
        "update_pull_request",
    }
)


def test_only_github_publisher_calls_github_write_methods() -> None:
    violations: list[str] = []

    for path in sorted(_PRODUCTION_ROOT.rglob("*.py")):
        if path == _PUBLISHER_PATH:
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _GITHUB_WRITE_METHODS
            ):
                relative_path = path.relative_to(_PROJECT_ROOT)
                violations.append(f"{relative_path}:{node.lineno}:{node.func.attr}")

    assert violations == [], "GitHub writes bypass GitHubPublisher:\n" + "\n".join(violations)
