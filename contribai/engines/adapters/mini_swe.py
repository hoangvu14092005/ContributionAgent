"""Optional in-process mini-SWE-agent driver.

The adapter intentionally accepts an injected runner/agent factory.  This
keeps the core package independent from mini-SWE's optional release cadence
and lets deployments prove the exact binding version before enabling it.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from contribai.engines.adapters.base import (
    AdapterUnavailableError,
    ExternalEngineDriver,
    invoke_callback,
    wait_with_cancel,
)
from contribai.engines.models import EngineRequest, ExecutionLease
from contribai.execution.workspaces.base import Workspace


class MiniSWEInProcessDriver(ExternalEngineDriver):
    """Run mini-SWE inside the outer ContribAI workspace boundary."""

    engine_name = "mini-swe"
    engine_version = "mini-swe@optional"

    def __init__(
        self,
        *,
        runner: Callable[..., object] | None = None,
        agent_factory: Callable[..., object] | None = None,
        binding_module: Any | None = None,
        engine_version: str = "mini-swe@optional",
        require_gateway: bool = True,
    ) -> None:
        super().__init__(require_gateway=require_gateway)
        self._runner = runner
        self._agent_factory = agent_factory
        self._binding_module = binding_module
        self.engine_version = engine_version

    async def execute_runtime(
        self,
        request: EngineRequest,
        execution: ExecutionLease,
        workspace: Workspace,
        started: float,
    ) -> object:
        prompt = self._build_prompt(request)
        if self._runner is not None:
            return await wait_with_cancel(
                invoke_callback(
                    self._runner,
                    request=request,
                    execution=execution,
                    workspace=workspace,
                    prompt=prompt,
                ),
                execution,
            )
        factory = self._agent_factory
        if factory is None:
            factory = self._discover_agent_factory()
        if factory is None:
            raise AdapterUnavailableError(
                "mini-SWE binding is unavailable or does not expose a safe agent factory"
            )
        agent = await invoke_callback(
            factory,
            request=request,
            execution=execution,
            workspace=workspace,
            prompt=prompt,
        )
        if isinstance(agent, (dict,)) or agent is None:
            return agent
        run_method = getattr(agent, "run", None)
        if run_method is None:
            if callable(agent):
                return await wait_with_cancel(
                    invoke_callback(
                        agent,
                        request=request,
                        execution=execution,
                        workspace=workspace,
                        prompt=prompt,
                    ),
                    execution,
                )
            raise AdapterUnavailableError("mini-SWE agent factory returned no run method")
        cancel_method = getattr(agent, "interrupt", None) or getattr(agent, "cancel", None)
        return await wait_with_cancel(
            invoke_callback(
                run_method,
                request=request,
                execution=execution,
                workspace=workspace,
                prompt=prompt,
            ),
            execution,
            on_cancel=cancel_method,
        )

    @staticmethod
    def _build_prompt(request: EngineRequest) -> str:
        return (
            "You are a coding engine inside an isolated ContribAI workspace. "
            "Modify only the workspace, never publish or access GitHub.\n\n"
            f"Task: {request.task.query_text}\n\n"
            f"{request.context.to_prompt()}"
        )

    def _discover_agent_factory(self) -> Callable[..., object] | None:
        module = self._binding_module
        if module is None:
            for name in ("minisweagent", "mini_swe_agent"):
                try:
                    module = importlib.import_module(name)
                except ImportError:
                    continue
                break
        if module is None:
            return None
        for name in ("create_agent", "DefaultAgent", "Agent"):
            candidate = getattr(module, name, None)
            if callable(candidate):
                return candidate
        return None


__all__ = ["MiniSWEInProcessDriver"]
