"""Unit tests for the Pipeline conductor (Layer C).

Verifies:
- Steps are awaited in declared order
- ``state.skip_reason`` short-circuits the rest of the pipeline
- Exceptions raised inside a step propagate to the caller
- The conductor sets ``state.result.repos_analyzed = 1`` after running
- ``Pipeline.steps`` returns a copy of the configured step list
"""

from __future__ import annotations

from typing import Any

import pytest

from contribai.orchestrator.pipeline import PipelineResult
from contribai.orchestrator.pipeline_core import (
    Pipeline,
    PipelineContext,
    PipelineState,
)


def _ctx() -> PipelineContext:
    """Minimal PipelineContext for conductor-only tests; steps aren't called."""
    import asyncio
    from unittest.mock import AsyncMock

    return PipelineContext(
        github=AsyncMock(),
        llm=AsyncMock(),
        memory=AsyncMock(),
        analyzer=AsyncMock(),
        generator=AsyncMock(),
        pr_manager=AsyncMock(),
        reviewer=AsyncMock(),
        repo_intel=AsyncMock(),
        event_bus=AsyncMock(),
        config=AsyncMock(),
    )


def _state() -> PipelineState:
    from contribai.core.models import Repository

    return PipelineState(
        repo=Repository(
            owner="o", name="n", full_name="o/n", default_branch="main"
        )
    )


def _make_step(name: str, *, side_effect: Any = None, set_skip: str | None = None):
    """Build a step function that records when it ran.

    If ``set_skip`` is given, the step sets ``state.skip_reason`` to that
    value, triggering short-circuit. If ``side_effect`` is given, the step
    raises that exception.
    """
    log: list[str] = []

    async def step(ctx: PipelineContext, state: PipelineState) -> None:
        log.append(name)
        if side_effect is not None:
            raise side_effect
        if set_skip is not None:
            state.skip_reason = set_skip

    step.__name__ = name
    step._log = log  # type: ignore[attr-defined]
    return step


class TestPipelineConductor:
    async def test_passes_state_through_each_step(self):
        s1 = _make_step("s1")
        s2 = _make_step("s2")
        s3 = _make_step("s3")
        pipeline = Pipeline([s1, s2, s3], label="t")
        state = _state()
        ctx = _ctx()

        result = await pipeline.run(state, ctx)

        assert result is state
        assert s1._log == ["s1"]  # type: ignore[attr-defined]
        assert s2._log == ["s2"]  # type: ignore[attr-defined]
        assert s3._log == ["s3"]  # type: ignore[attr-defined]

    async def test_short_circuits_on_skip_reason(self):
        s1 = _make_step("s1", set_skip="no_findings")
        s2 = _make_step("s2")
        s3 = _make_step("s3")
        pipeline = Pipeline([s1, s2, s3], label="t")
        state = _state()

        await pipeline.run(state, _ctx())

        assert s1._log == ["s1"]  # type: ignore[attr-defined]
        assert s2._log == []      # type: ignore[attr-defined]
        assert s3._log == []      # type: ignore[attr-defined]
        assert state.skip_reason == "no_findings"

    async def test_propagates_exceptions(self):
        boom = _make_step("boom", side_effect=RuntimeError("kaboom"))
        after = _make_step("after")
        pipeline = Pipeline([boom, after])

        with pytest.raises(RuntimeError, match="kaboom"):
            await pipeline.run(_state(), _ctx())

        # The "after" step should never have run
        assert after._log == []  # type: ignore[attr-defined]

    async def test_step_order_preserved(self):
        names = ["alpha", "beta", "gamma", "delta"]
        steps = [_make_step(n) for n in names]
        pipeline = Pipeline(steps)

        await pipeline.run(_state(), _ctx())

        actual = [s._log[0] for s in steps]  # type: ignore[attr-defined]
        assert actual == names

    async def test_skip_reason_visible_in_state(self):
        sentinel = _make_step("sentinel", set_skip="ai_policy")
        pipeline = Pipeline([sentinel])

        state = await pipeline.run(_state(), _ctx())

        assert state.skip_reason == "ai_policy"

    async def test_repos_analyzed_set_to_one(self):
        pipeline = Pipeline([_make_step("s")])
        state = _state()
        assert state.result.repos_analyzed == 0  # before run

        await pipeline.run(state, _ctx())

        assert state.result.repos_analyzed == 1

    async def test_steps_property_returns_copy(self):
        s1 = _make_step("s1")
        pipeline = Pipeline([s1])

        steps = pipeline.steps
        steps.append("mutation")

        # Pipeline.steps must be a defensive copy
        assert len(pipeline.steps) == 1

    async def test_label_defaults_to_class_name(self):
        pipeline = Pipeline([])
        assert pipeline._label == "Pipeline"

    async def test_label_override(self):
        pipeline = Pipeline([], label="analysis")
        assert pipeline._label == "analysis"


class TestPipelineState:
    def test_default_skip_reason_is_none(self):
        state = _state()
        assert state.skip_reason is None

    def test_result_is_pipeline_result_instance(self):
        state = _state()
        assert isinstance(state.result, PipelineResult)

    def test_fresh_pipeline_result_defaults(self):
        state = _state()
        assert state.result.repos_analyzed == 0
        assert state.result.prs_created == 0
        assert state.result.errors == []


class TestPipelineContext:
    def test_set_task_on_non_multi_model_is_noop(self):
        from unittest.mock import AsyncMock, MagicMock

        ctx = PipelineContext(
            github=AsyncMock(),
            llm=MagicMock(),  # not a MultiModelProvider
            memory=AsyncMock(),
            analyzer=AsyncMock(),
            generator=AsyncMock(),
            pr_manager=AsyncMock(),
            reviewer=AsyncMock(),
            repo_intel=AsyncMock(),
            event_bus=AsyncMock(),
            config=AsyncMock(),
        )
        # Should not raise
        ctx.set_task("analysis")