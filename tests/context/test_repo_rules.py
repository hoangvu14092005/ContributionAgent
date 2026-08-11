"""Repository rule discovery and deterministic resolution tests."""

from __future__ import annotations

from contribai.context.rules import RepoRules


def test_rules_collect_readme_contributing_and_agent_documents() -> None:
    rules = RepoRules.from_files(
        {
            "README.md": "Use pytest and keep public APIs documented.",
            "CONTRIBUTING.md": "Pull requests must link an existing issue.",
            "AGENTS.md": "Never modify generated/ files. Run ruff before review.",
            ".claude/rules/python.md": "Python files use 4 spaces.",
            "src/generated/out.py": "ignored source content",
        }
    )

    resolved = rules.resolve()
    assert "Pull requests must link" in resolved.instructions
    assert "Never modify generated/" in resolved.instructions
    assert "Python files use 4 spaces" in resolved.instructions
    assert "generated/" in resolved.forbidden_paths
    assert resolved.requires_issue_link is True
    assert resolved.instructions_for("src/app.py")
    assert "generated" in resolved.instructions_for("src/generated/out.py").lower()


def test_rules_are_stable_and_filter_irrelevant_files() -> None:
    files = {
        "AGENTS.md": "Run tests.",
        "src/z.py": "print('z')",
        "src/a.py": "print('a')",
        "node_modules/pkg/README.md": "not a repo rule",
    }
    first = RepoRules.from_files(files).resolve()
    second = RepoRules.from_files(dict(reversed(list(files.items())))).resolve()

    assert first == second
    assert "not a repo rule" not in first.instructions
