"""Optional OpenHands SDK driver constrained by the outer workspace.

Third-party SDK code is never auto-imported into the control-plane process.
Deployments must inject a reviewed runner/factory explicitly; otherwise this
adapter fails closed instead of inheriting host filesystem/network credentials.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any

from contribai.engines.adapters.base import (
    AdapterUnavailableError,
    ExternalEngineDriver,
    invoke_callback,
    wait_with_cancel,
)
from contribai.engines.models import EngineRequest, ExecutionLease
from contribai.execution.workspaces.base import Workspace


class OpenHandsSDKDriver(ExternalEngineDriver):
    """Use only an explicitly injected OpenHands binding."""

    engine_name = "openhands-sdk"
    engine_version = "openhands-sdk@optional"

    def __init__(
        self,
        *,
        runner: Callable[..., object] | None = None,
        agent_factory: Callable[..., object] | None = None,
        sdk_module: Any | None = None,
        engine_version: str = "openhands-sdk@optional",
        timeout_sec: float | None = None,
        require_gateway: bool = True,
    ) -> None:
        super().__init__(require_gateway=require_gateway)
        self._runner = runner
        self._agent_factory = agent_factory
        self._sdk_module = sdk_module
        self.engine_version = engine_version
        self.timeout_sec = timeout_sec

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
                timeout_sec=self.timeout_sec,
            )

        factory = self._agent_factory or self._factory_from_explicit_module()
        if factory is None:
            raise AdapterUnavailableError(
                "OpenHands SDK requires an explicitly injected reviewed runner or factory"
            )
        agent = await invoke_callback(
            factory,
            request=request,
            execution=execution,
            workspace=workspace,
            workspace_path=workspace.path,
            prompt=prompt,
            workspace_backend="local",
            sandbox_mode="outer",
            use_docker=False,
        )
        if isinstance(agent, Mapping):
            return agent
        if agent is None:
            return None
        run_method = getattr(agent, "run", None)
        if run_method is None:
            if inspect.isawaitable(agent):
                return await agent
            return agent
        cancel_method = getattr(agent, "interrupt", None) or getattr(agent, "cancel", None)
        return await wait_with_cancel(
            invoke_callback(
                run_method,
                request=request,
                execution=execution,
                workspace=workspace,
                workspace_path=workspace.path,
                prompt=prompt,
            ),
            execution,
            timeout_sec=self.timeout_sec,
            on_cancel=cancel_method,
        )

    @staticmethod
    def _build_prompt(request: EngineRequest) -> str:
        return (
            "Work only inside the supplied outer ContribAI workspace. "
            "Do not create Docker containers, mount host paths, or publish to GitHub.\n\n"
            f"Task: {request.task.query_text}\n\n{request.context.to_prompt()}"
        )

    def _factory_from_explicit_module(self) -> Callable[..., object] | None:
        """Build a factory only from a module explicitly supplied by deployment code."""
        module = self._sdk_module
        if module is None:
            return None
        local_workspace = getattr(module, "LocalWorkspace", None)
        agent = getattr(module, "Agent", None) or getattr(module, "CodeActAgent", None)
        if local_workspace is None or agent is None:
            return None

        def factory(**kwargs: object) -> object:
            path = kwargs["workspace_path"]
            try:
                local = local_workspace(root=str(path))
            except TypeError:
                local = local_workspace(path=str(path))
            return agent(workspace=local, model_gateway=kwargs.get("model_gateway"))

        return factory


__all__ = ["OpenHandsSDKDriver"]
