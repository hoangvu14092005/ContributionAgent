"""Contribution context, symbol index and repo-map tests."""

from __future__ import annotations

from contribai.context.builder import ContextBuilder
from contribai.context.context import AttemptSummary, PRSummary
from contribai.core.models import Repository


def _repo() -> Repository:
    return Repository(owner="owner", name="repo", full_name="owner/repo", language="Python")


def test_context_unifies_rules_intelligence_history_and_symbols() -> None:
    files = {
        "README.md": "# Repo\nInstall with pytest.",
        "CONTRIBUTING.md": "Use conventional commits.",
        "AGENTS.md": "Run pytest and do not edit generated/.",
        "src/service.py": "class Service:\n    def handle(self, value):\n        return value\n",
        "src/client.ts": "export function fetchData(value) { return value; }\n",
    }
    builder = ContextBuilder(max_context_tokens=500)
    context = builder.build(
        _repo(),
        files=files,
        pr_history=[PRSummary(number=1, title="Fix service", state="merged")],
        previous_attempts=[AttemptSummary(attempt_id="a1", status="failed", reason="lint")],
    )

    assert context.repo.full_name == "owner/repo"
    assert "do not edit generated" in context.repo_rules.instructions.lower()
    assert "Install with pytest" in context.to_prompt()
    assert "Use conventional commits" in context.to_prompt()
    assert "Service.handle" in context.repo_map
    assert context.symbol_index.find("handle")[0].path == "src/service.py"
    assert context.pr_history[0].number == 1
    assert context.previous_attempts[0].reason == "lint"
    assert "Fix service" in context.to_prompt()


def test_context_prompt_is_deterministic_and_respects_token_budget() -> None:
    files = {
        "src/z.py": "def z():\n    return 'z'\n" * 40,
        "src/a.py": "def a():\n    return 'a'\n" * 40,
        "README.md": "readme " * 100,
    }
    builder = ContextBuilder(max_context_tokens=80)
    first = builder.build(_repo(), files=files)
    second = builder.build(_repo(), files=dict(reversed(list(files.items()))))

    assert first.to_prompt() == second.to_prompt()
    assert len(first.to_prompt()) <= 80 * 4 + 200
    assert first.symbol_index.find("a")
