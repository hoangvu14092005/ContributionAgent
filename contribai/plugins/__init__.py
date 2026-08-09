"""Plugin system for extensible analyzers and generators.

Layer B public API:

- :class:`AnalyzerPlugin` / :class:`GeneratorPlugin` — base classes for plugins
  (``contribai.plugins.base``).
- :class:`PluginRegistry` — discovery + manual registration, lives in
  ``contribai.plugins.base``.
- :func:`get_plugin_registry` — process-wide singleton accessor.
- :func:`discover` — run entry-point discovery on the singleton.

Plugins are discovered via Python entry points:

    [project.entry-points."contribai.analyzers"]
    my_analyzer = "my_package:MyAnalyzer"
"""

from __future__ import annotations

import logging

from contribai.plugins.base import (
    AnalyzerPlugin,
    GeneratorPlugin,
    PluginRegistry,
)

__all__ = [
    "AnalyzerPlugin",
    "GeneratorPlugin",
    "PluginRegistry",
    "discover",
    "get_plugin_registry",
    "reset_plugin_registry",
]

logger = logging.getLogger(__name__)


# ── Singleton ─────────────────────────────────────────────────────────────────
#
# A process-wide PluginRegistry keeps the API symmetric with the LLM provider
# registry (``contribai.llm.make_provider``) and avoids re-discovering entry
# points on every call to ``get_analyzers()``. Tests can call
# :func:`reset_plugin_registry` to force a fresh instance.

_DEFAULT_REGISTRY: PluginRegistry | None = None


def get_plugin_registry() -> PluginRegistry:
    """Return the process-wide :class:`PluginRegistry` instance.

    The first call lazily constructs the registry; subsequent calls return the
    same instance until :func:`reset_plugin_registry` is called (used by tests
    to avoid leakage between cases).
    """
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = PluginRegistry()
    return _DEFAULT_REGISTRY


def discover() -> PluginRegistry:
    """Run entry-point discovery on the singleton and return it.

    Convenience wrapper around :meth:`PluginRegistry.discover` for callers
    who want a one-liner. Subsequent calls are no-ops (the registry caches
    its discovery result).
    """
    registry = get_plugin_registry()
    registry.discover()
    return registry


def reset_plugin_registry() -> None:
    """Drop the cached singleton. Intended for tests only."""
    global _DEFAULT_REGISTRY
    _DEFAULT_REGISTRY = None
