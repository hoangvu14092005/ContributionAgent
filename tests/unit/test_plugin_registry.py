"""Tests for the plugin registry singleton (Layer B).

Covers:

- :func:`get_plugin_registry` returns a singleton
- :func:`discover` triggers entry-point discovery and returns the registry
- :func:`reset_plugin_registry` drops the cache (test isolation)
- :class:`PluginRegistry` is importable from the package root
- Manual ``register_analyzer`` works on the singleton

We do NOT cover entry-point mechanics themselves — that's an
``importlib.metadata`` concern. We only verify the singleton wiring and
that it delegates to ``PluginRegistry`` correctly.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from contribai.core.models import (
    ContributionType,
    Finding,
    RepoContext,
    Repository,
    Severity,
)
from contribai.plugins import (
    AnalyzerPlugin,
    PluginRegistry,
    discover,
    get_plugin_registry,
    reset_plugin_registry,
)


@pytest.fixture(autouse=True)
def _clean_singleton() -> Iterator[None]:
    """Make every test start with a fresh plugin registry singleton."""
    reset_plugin_registry()
    yield
    reset_plugin_registry()


def _dummy_repo() -> Repository:
    return Repository(
        owner="o",
        name="n",
        full_name="o/n",
        default_branch="main",
        html_url="https://github.com/o/n",
        clone_url="https://github.com/o/n.git",
    )


def _dummy_context() -> RepoContext:
    return RepoContext(repo=_dummy_repo())


# ── Singleton wiring ─────────────────────────────────────────────────────────


class TestSingleton:
    def test_get_plugin_registry_returns_instance(self):
        registry = get_plugin_registry()
        assert isinstance(registry, PluginRegistry)

    def test_get_plugin_registry_returns_same_instance(self):
        a = get_plugin_registry()
        b = get_plugin_registry()
        assert a is b

    def test_reset_plugin_registry_drops_cache(self):
        first = get_plugin_registry()
        reset_plugin_registry()
        second = get_plugin_registry()
        assert first is not second

    def test_discover_returns_registry(self):
        registry = discover()
        assert isinstance(registry, PluginRegistry)
        assert registry is get_plugin_registry()

    def test_singleton_uses_plugin_registry_class(self):
        registry = get_plugin_registry()
        # The singleton should expose the public surface used elsewhere.
        assert hasattr(registry, "discover")
        assert hasattr(registry, "analyzers")
        assert hasattr(registry, "generators")
        assert hasattr(registry, "register_analyzer")


# ── Manual registration on the singleton ─────────────────────────────────────


class _CountingAnalyzer(AnalyzerPlugin):
    """A minimal analyzer plugin that returns no findings."""

    def __init__(self, label: str = "counting"):
        self._label = label

    @property
    def name(self) -> str:
        return self._label

    async def analyze(self, context: RepoContext) -> list[Finding]:
        return []


class _FindingAnalyzer(AnalyzerPlugin):
    """An analyzer plugin that returns one canned finding."""

    @property
    def name(self) -> str:
        return "finding"

    async def analyze(self, context: RepoContext) -> list[Finding]:
        return [
            Finding(
                id="p1",
                type=ContributionType.CODE_QUALITY,
                severity=Severity.LOW,
                title="plugin finding",
                description="from plugin",
                file_path="x.py",
            )
        ]


class TestManualRegistration:
    def test_register_analyzer_is_visible_on_singleton(self):
        registry = get_plugin_registry()
        analyzer = _CountingAnalyzer("alpha")
        registry.register_analyzer(analyzer)
        assert analyzer in registry.analyzers

    def test_multiple_analyzers_coexist(self):
        registry = get_plugin_registry()
        a = _CountingAnalyzer("a")
        b = _CountingAnalyzer("b")
        registry.register_analyzer(a)
        registry.register_analyzer(b)
        names = [p.name for p in registry.analyzers]
        assert "a" in names
        assert "b" in names

    async def test_finding_analyzer_runs(self):
        """Analyzers registered on the singleton must be usable in `analyze()`."""
        registry = get_plugin_registry()
        registry.register_analyzer(_FindingAnalyzer())
        results: list[Finding] = []
        for analyzer in registry.analyzers:
            results.extend(await analyzer.analyze(_dummy_context()))
        assert len(results) == 1
        assert results[0].title == "plugin finding"