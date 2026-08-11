"""Pipeline constants shared across steps.

Layer C extracted these from `pipeline.py` so that any step (or
non-pipeline caller) can import the canonical sets without dragging in
`ContribPipeline` and its collaborators.

Keeping them in a dedicated module also avoids the circular import that
would happen if `steps.py` re-imported from `pipeline.py`.
"""

from __future__ import annotations

from typing import Literal

from contribai.core.path_policy import PROTECTED_META_FILES

# ── File filters ──────────────────────────────────────────────────────────────

#: File extensions skipped during pre-filter — doc/config-only changes are
#: low-value and PRs that touch only these are typically rejected.
SKIP_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".md",
        ".txt",
        ".rst",
        ".yml",
        ".yaml",
        ".toml",
        ".cfg",
        ".ini",
        ".json",
    }
)


#: Directories skipped during pre-filter — examples, docs, tests, fixtures
#: are not worth PRing.
SKIP_DIRECTORIES: frozenset[str] = frozenset(
    {
        "examples",
        "example",
        "samples",
        "sample",
        "demos",
        "demo",
        "docs",
        "doc",
        "test",
        "tests",
        "testing",
        "test_data",
        "testdata",
        "fixtures",
        "benchmarks",
        "benchmark",
        "__pycache__",
        "vendor",
        "third_party",
        "third-party",
        "node_modules",
    }
)


# ── Skip reasons ──────────────────────────────────────────────────────────────
#
#: All valid values for ``PipelineState.skip_reason``. The conductor checks
#: this field after each step and short-circuits the rest of the pipeline
#: when set. Adding a new reason? Update the Literal here and the conductor
#: will pick it up via type checking.
SkipReason = Literal[
    "ai_policy",  # repo's AI policy bans AI PRs
    "pr_permissions",  # repo restricts PRs to collaborators
    "no_findings",  # analyzer returned no findings (or pre-filter emptied them)
    "no_validated",  # dedup + LLM validation left nothing usable
    "no_contributions",  # generator returned no contributions
]


# ── Title similarity helper ───────────────────────────────────────────────────


def _titles_similar(title_a: str, title_b: str) -> bool:
    """Check if two finding/PR titles are similar enough to be duplicates.

    Uses keyword overlap: if >50% of significant words match, consider similar.
    Used by :func:`contribai.orchestrator.steps.validate_findings_step` for
    dedup against past PRs.
    """
    stop_words = {"a", "an", "the", "in", "on", "of", "for", "to", "and", "or", "is"}
    words_a = {w for w in title_a.lower().split() if w not in stop_words and len(w) > 2}
    words_b = {w for w in title_b.lower().split() if w not in stop_words and len(w) > 2}
    if not words_a or not words_b:
        return False
    overlap = len(words_a & words_b)
    smaller = min(len(words_a), len(words_b))
    return overlap / smaller > 0.5


__all__ = [
    "PROTECTED_META_FILES",
    "SKIP_DIRECTORIES",
    "SKIP_EXTENSIONS",
    "SkipReason",
    "_titles_similar",
]
