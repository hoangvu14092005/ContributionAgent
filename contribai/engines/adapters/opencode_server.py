"""OpenCode server driver using a small HTTP/session boundary."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterable, Callable, Iterable, Mapping
from typing import Any

from contribai.engines.adapters.base import (
    AdapterResult,
    AdapterUnavailableError,
    ExternalEngineDriver,
    invoke_callback,
    normalize_events,
    require_workspace,
    wait_with_cancel,
)
from contribai.engines.models import EngineRequest, EngineStatus, EngineUsage, ExecutionLease
from contribai.execution.workspaces.base import Workspace
from contribai.publishing.capability import Capability
from contribai.publishing.policy import PolicyDecision


class OpenCodeServerDriver(ExternalEngineDriver):
    """Drive OpenCode sessions without delegating policy or publishing authority."""

    engine_name = "opencode-server"
    engine_version = "opencode-server@http-1"

    def __init__(
        self,
        *,
        client: Any | None = None,
        server_factory: Callable[..., object] | None = None,
        server_url: str | None = None,
        engine_version: str = "opencode-server@http-1",
        timeout_sec: float | None = None,
        require_gateway: bool = True,
    ) -> None:
        super().__init__(require_gateway=require_gateway)
        self._client = client
        self._server_factory = server_factory
        self._server_url = server_url
        self.engine_version = engine_version
        self.timeout_sec = timeout_sec

    async def execute_runtime(
        self,
        request: EngineRequest,
        execution: ExecutionLease,
        workspace: Workspace,
        started: float,
    ) -> object:
        outer = require_workspace(execution)
        client, cleanup = await self._resolve_client(request, execution, outer)
        if client is None:
            raise AdapterUnavailableError("OpenCode server client is not configured")
        permissions = translate_opencode_permissions(request.capability_policy)
        session_id: str | None = None
        try:
            created = await _call_client(
                client,
                ("create_session", "session_create"),
                workspace_path=workspace.path,
                cwd=str(workspace.path),
                permissions=permissions,
                model_gateway_url=(
                    execution.credential_lease.endpoint if execution.credential_lease else None
                ),
                model_gateway_token=(
                    execution.credential_lease.token if execution.credential_lease else None
                ),
            )
            session_id = _session_id(created)
            if not session_id:
                raise AdapterUnavailableError("OpenCode did not return a session id")
            prompt = self._build_prompt(request)
            prompt_result = await wait_with_cancel(
                _call_client(
                    client,
                    ("prompt_async", "prompt"),
                    session_id=session_id,
                    prompt=prompt,
                    cwd=str(workspace.path),
                ),
                execution,
                timeout_sec=self.timeout_sec,
                on_cancel=lambda: _call_client(
                    client,
                    ("abort", "session_abort"),
                    session_id=session_id,
                ),
            )
            prompt_events = _events_from_value(prompt_result)
            prompt_status = _status_from_value(prompt_result)
            if prompt_status is EngineStatus.WAITING_FOR_APPROVAL:
                return AdapterResult(
                    status=prompt_status,
                    exit_reason="OpenCode requested permission; control plane must decide",
                    events=normalize_events(prompt_events),
                    metadata={"session_id": session_id, "permissions": permissions},
                )
            if prompt_status in {EngineStatus.FAILED, EngineStatus.CANCELLED}:
                return AdapterResult(
                    status=prompt_status,
                    exit_reason="OpenCode prompt did not start successfully",
                    events=normalize_events(prompt_events),
                    metadata={"session_id": session_id, "permissions": permissions},
                )

            stream_method = getattr(client, "events", None) or getattr(
                client,
                "stream_events",
                None,
            )
            streamed = []
            if stream_method is not None:
                streamed = await wait_with_cancel(
                    self._collect_events(stream_method, session_id),
                    execution,
                    timeout_sec=self.timeout_sec,
                    on_cancel=lambda: _call_client(
                        client,
                        ("abort", "session_abort"),
                        session_id=session_id,
                    ),
                )
            events = [*prompt_events, *streamed]
            status = _terminal_status(events) or EngineStatus.COMPLETED
            if status is EngineStatus.WAITING_FOR_APPROVAL:
                reason = "OpenCode requested permission; no implicit approval was granted"
            else:
                reason = (
                    "OpenCode session completed"
                    if status is EngineStatus.COMPLETED
                    else status.value
                )
            return AdapterResult(
                status=status,
                exit_reason=reason,
                events=normalize_events(events),
                usage=_usage_from_value(prompt_result),
                cost_usd=_cost_from_value(prompt_result),
                metadata={"session_id": session_id, "permissions": permissions},
            )
        finally:
            await _cleanup_client(client, cleanup)

    async def _resolve_client(
        self,
        request: EngineRequest,
        execution: ExecutionLease,
        workspace: Workspace,
    ) -> tuple[Any | None, object | None]:
        if self._client is not None:
            return self._client, None
        if self._server_factory is not None:
            server = await invoke_callback(
                self._server_factory,
                request=request,
                execution=execution,
                workspace=workspace,
                workspace_path=workspace.path,
                server_url=self._server_url,
            )
            if inspect.isawaitable(server):
                server = await server
            client = getattr(server, "client", None) or server
            return client, server
        if self._server_url:
            return _HttpOpenCodeClient(self._server_url), None
        return None, None

    async def _collect_events(
        self,
        method: Callable[..., object],
        session_id: str,
    ) -> list[object]:
        raw = await _call_client(
            self,
            ("_invoke_event_method",),
            method=method,
            session_id=session_id,
        )
        if isinstance(raw, AsyncIterable) or hasattr(raw, "__aiter__"):
            values = []
            async for item in raw:
                values.append(item)
            return values
        if inspect.isawaitable(raw):
            raw = await raw
        if raw is None:
            return []
        if isinstance(raw, Mapping):
            return [raw]
        return (
            list(raw) if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)) else [raw]
        )

    @staticmethod
    def _build_prompt(request: EngineRequest) -> str:
        return (
            "Work only in the outer ContribAI workspace. "
            "Never publish, push, or call GitHub write APIs.\n\n"
            f"Task: {request.task.query_text}\n\n{request.context.to_prompt()}"
        )


def translate_opencode_permissions(policy: Any, *, actor: str = "opencode") -> dict[str, str]:
    """Translate control-plane policy to OpenCode defense-in-depth permissions."""
    mapping = {
        "read": Capability.WORKSPACE_READ,
        "edit": Capability.WORKSPACE_WRITE,
        "bash": Capability.WORKSPACE_WRITE,
        "webfetch": Capability.NETWORK,
        "websearch": Capability.NETWORK,
        "external_directory": Capability.WORKSPACE_READ,
    }
    result: dict[str, str] = {}
    evaluator = getattr(policy, "rules", None)
    for name, capability in mapping.items():
        decision = PolicyDecision.DENY
        if evaluator:
            for rule in evaluator:
                if (
                    rule.actor == actor
                    and rule.capability == capability
                    and rule.resource in {"workspace", "workspace/*"}
                ):
                    decision = PolicyDecision(rule.decision)
                    break
        result[name] = decision.value
    return result


async def _call_client(client: Any, names: tuple[str, ...], **kwargs: object) -> object:
    if names == ("_invoke_event_method",):
        method = kwargs.pop("method")
        result = method(session_id=kwargs["session_id"])
    else:
        method = next(
            (getattr(client, name, None) for name in names if getattr(client, name, None)),
            None,
        )
        if method is None:
            raise AdapterUnavailableError(f"OpenCode client does not implement {names[0]}")
        result = _invoke_method(method, kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _invoke_method(method: Callable[..., object], values: Mapping[str, object]) -> object:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(**values)
    if any(parameter.kind is parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        return method(**values)
    accepted = {
        parameter.name: values[parameter.name]
        for parameter in signature.parameters.values()
        if parameter.name in values
        and parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
    }
    return method(**accepted)


def _session_id(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        candidate = value.get("id") or value.get("session_id")
        return str(candidate) if candidate else None
    return str(getattr(value, "id", "")) or None


def _events_from_value(value: object) -> list[object]:
    if isinstance(value, Mapping):
        events = value.get("events", ())
        if isinstance(events, (str, bytes)):
            return [events]
        return list(events) if isinstance(events, Iterable) else []
    return []


def _status_from_value(value: object) -> EngineStatus | None:
    if isinstance(value, Mapping):
        raw = str(value.get("status") or value.get("type") or "").lower()
        if "permission" in raw or raw in {
            "ask",
            "approval_required",
            "waiting_for_approval",
        }:
            return EngineStatus.WAITING_FOR_APPROVAL
        if raw in {"failed", "error", "session.error"}:
            return EngineStatus.FAILED
        if raw in {"cancelled", "canceled", "aborted"}:
            return EngineStatus.CANCELLED
    return None


def _terminal_status(events: Iterable[object]) -> EngineStatus | None:
    status: EngineStatus | None = None
    for event in events:
        current = _status_from_value(event)
        if current is not None:
            status = current
            continue
        if isinstance(event, Mapping):
            raw = str(event.get("type") or event.get("status") or "").lower()
            if raw in {"session.completed", "turn.completed", "completed", "done"}:
                status = EngineStatus.COMPLETED
            elif raw in {"permission.asked", "permission", "approval_required"}:
                status = EngineStatus.WAITING_FOR_APPROVAL
    return status


def _usage_from_value(value: object) -> EngineUsage:
    if isinstance(value, Mapping) and isinstance(value.get("usage"), Mapping):
        usage = value["usage"]
        return EngineUsage(
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            tool_calls=int(usage.get("tool_calls", 0)),
            duration_sec=float(usage.get("duration_sec", 0.0)),
        )
    return EngineUsage()


def _cost_from_value(value: object) -> float:
    return float(value.get("cost_usd", 0.0)) if isinstance(value, Mapping) else 0.0


async def _cleanup_client(client: Any, server: object | None) -> None:
    target = server if server is not None else client
    for name in ("stop", "close", "shutdown"):
        method = getattr(target, name, None)
        if method is None:
            continue
        result = method()
        if inspect.isawaitable(result):
            await result
        return


class _HttpOpenCodeClient:
    """Minimal HTTP client; deployments may inject a richer client instead."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._client: Any | None = None

    async def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=None)
        return self._client

    async def create_session(self, **kwargs: object) -> Mapping[str, object]:
        client = await self._http()
        response = await client.post("/session", json=kwargs)
        response.raise_for_status()
        return response.json()

    async def prompt(self, **kwargs: object) -> Mapping[str, object]:
        client = await self._http()
        session_id = kwargs.pop("session_id")
        response = await client.post(f"/session/{session_id}/prompt_async", json=kwargs)
        response.raise_for_status()
        return response.json() if response.content else {"status": "started"}

    async def events(self, **_kwargs: object) -> list[object]:
        client = await self._http()
        response = await client.get("/event")
        response.raise_for_status()
        return [line for line in response.text.splitlines() if line]

    async def abort(self, **kwargs: object) -> None:
        client = await self._http()
        session_id = kwargs["session_id"]
        response = await client.post(f"/session/{session_id}/abort")
        response.raise_for_status()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()


__all__ = ["OpenCodeServerDriver", "translate_opencode_permissions"]
