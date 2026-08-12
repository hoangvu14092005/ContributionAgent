"""Changed-line-first dynamic review context tests."""

from __future__ import annotations

from contribai.context.rules import RepoRules
from contribai.core.models import Issue
from contribai.review.dynamic_context import build_dynamic_review_context


def test_dynamic_context_prioritizes_diff_symbols_rules_issue_and_evidence() -> None:
    diff = """diff --git a/src/service.py b/src/service.py
--- a/src/service.py
+++ b/src/service.py
@@ -1,3 +1,4 @@
 def service(value):
-    return value
+    return normalize(value)
"""
    context = build_dynamic_review_context(
        diff=diff,
        files={"src/service.py": "def service(value):\n    return normalize(value)\n"},
        repo_rules=RepoRules.from_files(
            {"AGENTS.md": "Never modify generated files. Run pytest before review."}
        ).resolve(),
        issue=Issue(number=7, title="Fix stale service", body="Expected normalized output."),
        verification=["pytest: passed", "ruff: passed"],
    )

    assert context.changed_lines[0].path == "src/service.py"
    assert context.changed_lines[0].kind == "removed"
    assert context.enclosing_symbols
    assert "Repository rules" in context.text
    assert "#7: Fix stale service" in context.text
    assert "pytest: passed" in context.text


def test_dynamic_context_is_bounded() -> None:
    context = build_dynamic_review_context(diff="+" + "x" * 1_000, max_chars=100)

    assert len(context.text) == 100
