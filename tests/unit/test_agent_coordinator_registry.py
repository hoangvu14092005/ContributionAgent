"""Tests for AgentCoordinator registry API (Layer B).

Covers:

- Default agent installation matches ``DEFAULT_AGENT_NAMES``
- ``register(name, agent)`` adds/replaces an agent
- ``unregister(name)`` removes an agent
- ``get(name)`` returns the registered agent
- ``list_agents()`` returns names in insertion order
- ``register_defaults=False`` produces an empty coordinator
"""

from __future__ import annotations

from unittest.mock import MagicMock

from contribai.llm.agents import (
    AgentCoordinator,
    AnalysisAgent,
    BaseAgent,
    CodeGenAgent,
    DocsAgent,
    PlannerAgent,
    ReviewAgent,
)


def _make_coordinator(**kwargs) -> AgentCoordinator:
    """Build a coordinator without touching the network."""
    llm = MagicMock()
    return AgentCoordinator(llm_provider=llm, **kwargs)


class TestDefaultRegistration:
    def test_default_agents_installed(self):
        coord = _make_coordinator()
        names = coord.list_agents()
        for expected in AgentCoordinator.DEFAULT_AGENT_NAMES:
            assert expected in names, f"Missing default agent: {expected}"

    def test_default_agents_have_expected_types(self):
        coord = _make_coordinator()
        assert isinstance(coord.get("analyzer"), AnalysisAgent)
        assert isinstance(coord.get("codegen"), CodeGenAgent)
        assert isinstance(coord.get("reviewer"), ReviewAgent)
        assert isinstance(coord.get("docs"), DocsAgent)
        assert isinstance(coord.get("planner"), PlannerAgent)

    def test_register_defaults_false_yields_empty(self):
        coord = _make_coordinator(register_defaults=False)
        assert coord.list_agents() == []
        assert coord.get("analyzer") is None


class TestRegisterReplace:
    def test_register_adds_new_agent(self):
        coord = _make_coordinator(register_defaults=False)
        sentinel = BaseAgent.__new__(BaseAgent)
        coord.register("custom", sentinel)  # type: ignore[arg-type]
        assert coord.get("custom") is sentinel
        assert "custom" in coord.list_agents()

    def test_register_replaces_existing(self):
        coord = _make_coordinator()
        original = coord.get("analyzer")
        new_agent = BaseAgent.__new__(BaseAgent)
        coord.register("analyzer", new_agent)  # type: ignore[arg-type]
        assert coord.get("analyzer") is new_agent
        assert coord.get("analyzer") is not original


class TestUnregister:
    def test_unregister_removes_named(self):
        coord = _make_coordinator()
        coord.unregister("analyzer")
        assert coord.get("analyzer") is None
        assert "analyzer" not in coord.list_agents()

    def test_unregister_unknown_is_noop(self):
        coord = _make_coordinator()
        before = coord.list_agents()
        coord.unregister("not-a-real-agent")
        assert coord.list_agents() == before


class TestListAgents:
    def test_returns_insertion_order(self):
        coord = _make_coordinator()
        # DEFAULT_AGENT_NAMES is the insertion order — verify list_agents
        # preserves it (dict insertion order is stable in CPython 3.7+).
        assert coord.list_agents() == list(AgentCoordinator.DEFAULT_AGENT_NAMES)

    def test_register_after_init_appends(self):
        coord = _make_coordinator()
        sentinel = BaseAgent.__new__(BaseAgent)
        coord.register("late", sentinel)  # type: ignore[arg-type]
        names = coord.list_agents()
        assert names[-1] == "late"
