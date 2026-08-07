"""Hierarchical localization contract tests."""

from __future__ import annotations

import pytest

from contribai.context.builder import ContextBuilder
from contribai.core.models import Repository
from contribai.localization.edit_locations import find_edit_locations
from contribai.localization.localizer import Localizer
from contribai.localization.models import ContributionTask


def _repo() -> Repository:
    return Repository(owner="owner", name="repo", full_name="owner/repo", language="Python")


def _context():
    files = {
        "src/service.py": (
            "class Service:\n"
            "    def handle(self, value):\n"
            "        return value\n\n"
            "    def close(self):\n"
            "        return None\n"
        ),
        "src/client.ts": "export function fetchData(value) { return value; }\n",
        "cmd/main.go": "package main\n\nfunc main() { run() }\nfunc run() {}\n",
        "src/lib.rs": "pub fn parse_value(value: &str) -> &str { value }\n",
        "tests/test_service.py": "def test_handle():\n    Service().handle('x')\n",
    }
    return ContextBuilder(max_context_tokens=500).build(_repo(), files=files)


@pytest.mark.asyncio
async def test_localizer_returns_ranked_file_symbol_and_exact_location_candidates() -> None:
    context = _context()
    task = ContributionTask(
        title="Fix Service.handle returning stale values",
        description="Update the handle method in src/service.py.",
        file_path="src/service.py",
        symbol="handle",
    )

    result = await Localizer(max_candidates=8).locate(task, context)

    assert result.candidates
    assert result.candidates[0].path == "src/service.py"
    assert result.candidates[0].symbol in {"Service.handle", "handle"}
    assert result.candidates[0].line_start == 2
    assert result.candidates[0].line_end >= result.candidates[0].line_start
    assert result.candidates[0].evidence
    assert result.recall_at(1, "src/service.py", symbol="handle") == 1.0
    assert result.recall_at(3, "src/service.py", symbol="handle") == 1.0
    assert result.recall_at(5, "src/service.py", symbol="handle") == 1.0


@pytest.mark.asyncio
async def test_localizer_handles_js_go_and_rust_without_picking_one_file_early() -> None:
    context = _context()

    js = await Localizer().locate(
        ContributionTask(
            title="Fix fetchData client handling", description="The TypeScript fetchData function."
        ),
        context,
    )
    go = await Localizer().locate(
        ContributionTask(title="Improve main startup", description="The Go main entrypoint."),
        context,
    )
    rust = await Localizer().locate(
        ContributionTask(title="Fix parse_value", description="Parse input safely in Rust."),
        context,
    )

    assert js.recall_at(5, "src/client.ts", symbol="fetchData") == 1.0
    assert go.recall_at(5, "cmd/main.go", symbol="main") == 1.0
    assert rust.recall_at(5, "src/lib.rs", symbol="parse_value") == 1.0
    assert len(js.candidates) > 1
    assert all(candidate.evidence for candidate in js.candidates)


def test_edit_location_falls_back_to_relevant_line_when_symbol_is_unknown() -> None:
    context = _context()
    task = ContributionTask(
        title="Handle stale value in service",
        description="Change the return value handling.",
        file_path="src/service.py",
        line_start=3,
        line_end=3,
    )

    locations = find_edit_locations(task, context, "src/service.py")

    assert locations
    assert locations[0].line_start == 3
    assert locations[0].line_end == 3
    assert locations[0].evidence
