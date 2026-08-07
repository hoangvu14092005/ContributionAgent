"""Immutable runtime contracts for coding-engine adapters."""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from contribai.context.context import ContributionContext
from contribai.context.rules import RepoRules, ResolvedRepoRules
from contribai.execution.budget import ExecutionBudget
from contribai.execution.credentials import CredentialLease
from contribai.execution.trajectory import ExecutionEvent
from contribai.execution.workspaces.base import Workspace
from contribai.localization.models import ContributionTask
from contribai.publishing.policy import CapabilityPolicy

RepairTask = ContributionTask

_MAX_EVENTS = 1_000
_MAX_METADATA_ITEMS = 64
_MAX_VALUE_CHARS = 2_000
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "password",
        "private_key",
        "secret",
        "token",
    }
)
_FORBIDDEN_CONFIG_KEYS = _SENSITIVE_KEYS | frozenset(
    {"github_client", "publisher", "github_token", "ssh_auth_sock", "docker_socket"}
)
_SECRET_PATTERN = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]+|gh[pousr]_[a-z0-9_-]+|bearer\s+[a-z0-9._~+/=-]+)\b"
)


class EngineBoundaryError(ValueError):
    """Raised when a request attempts to cross the engine safety boundary."""


class EngineStatus(StrEnum):
    """Terminal and resumable outcomes reported by an engine driver."""

    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class EngineUsage:
    """Bounded usage accounting emitted by an engine runtime."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0
    duration_sec: float = 0.0

    def __post_init__(self) -> None:
        if min(self.prompt_tokens, self.completion_tokens, self.tool_calls) < 0:
            raise ValueError("engine usage counters must be non-negative")
        if self.duration_sec < 0 or not math.isfinite(self.duration_sec):
            raise ValueError("engine usage duration must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class ExecutionLease:
    """Scoped handles an engine may use during one isolated attempt."""

    work_id: str
    attempt_id: str
    workspace_ref: str
    budget: ExecutionBudget
    credential_lease: CredentialLease | None = field(default=None, repr=False)
    expires_at: datetime | None = None
    workspace: Workspace | None = field(default=None, repr=False, compare=False)
    model_gateway: object | None = field(default=None, repr=False, compare=False)
    cancel_event: asyncio.Event | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.work_id.strip() or not self.attempt_id.strip():
            raise ValueError("execution lease scope must not be empty")
        if not self.workspace_ref.strip():
            raise ValueError("execution lease requires a workspace reference")
        if self.expires_at and self.expires_at.tzinfo is None:
            raise ValueError("execution lease expiry must be timezone-aware")

    def is_expired(self, now: datetime | None = None) -> bool:
        """Return whether this lease can no longer be used."""
        if self.expires_at is None:
            return False
        current = now or datetime.now(self.expires_at.tzinfo)
        return current >= self.expires_at

    def assert_active(self) -> None:
        """Fail closed when the lease or its budget is no longer usable."""
        if self.is_expired():
            raise EngineBoundaryError("execution lease has expired")
        if self.budget.exhausted:
            raise EngineBoundaryError("execution budget is exhausted")

    @property
    def cancelled(self) -> bool:
        """Return the cooperative cancellation flag, when one is supplied."""
        return self.cancel_event.is_set() if self.cancel_event else False

    def scoped_model_environment(self) -> dict[str, str]:
        """Return only short-lived gateway handles safe for an engine process.

        The returned token is a lease token, never an upstream provider key.  A
        driver must pass this mapping through ``ResourcePolicy`` with the
        scoped-credential allow-list enabled; arbitrary credential environment
        variables remain rejected.
        """
        lease = self.credential_lease
        if lease is None:
            return {}
        return {
            "CONTRIBAI_MODEL_GATEWAY_URL": lease.endpoint,
            "CONTRIBAI_MODEL_GATEWAY_PROVIDER": lease.provider,
            "CONTRIBAI_MODEL_GATEWAY_TOKEN": lease.token,
            "CONTRIBAI_MODEL_GATEWAY_LEASE_ID": lease.lease_id,
        }


@dataclass(frozen=True, slots=True)
class EngineRequest:
    """All data an engine needs, excluding patch and publish authority."""

    work_id: str
    attempt_id: str
    task: RepairTask
    context: ContributionContext
    repo_rules: RepoRules | ResolvedRepoRules
    budget: ExecutionBudget
    capability_policy: CapabilityPolicy
    engine_config: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.work_id.strip() or not self.attempt_id.strip():
            raise ValueError("engine request scope must not be empty")
        if not isinstance(self.task, ContributionTask):
            raise TypeError("engine request task must be a RepairTask")
        _validate_config_keys(self.engine_config)
        frozen_config = _freeze_mapping(self.engine_config, reject_forbidden=True)
        object.__setattr__(self, "engine_config", frozen_config)


@dataclass(frozen=True, slots=True)
class EngineOutcome:
    """Bounded engine execution evidence; patch authority stays in PatchCollector."""

    status: EngineStatus
    exit_reason: str
    events: tuple[ExecutionEvent, ...]
    usage: EngineUsage
    cost_usd: float
    trajectory_id: str
    engine_version: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        status = EngineStatus(self.status)
        if self.cost_usd < 0 or not math.isfinite(self.cost_usd):
            raise ValueError("engine outcome cost must be finite and non-negative")
        if not self.trajectory_id.strip() or not self.engine_version.strip():
            raise ValueError("engine outcome requires trajectory and version identifiers")
        events = tuple(self.events)[-_MAX_EVENTS:]
        normalized_events = tuple(
            ExecutionEvent(
                event.kind,
                _redact_value(event.data),
                event.timestamp,
            )
            for event in events
        )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "events", normalized_events)
        object.__setattr__(
            self,
            "metadata",
            _freeze_mapping(self.metadata, limit=_MAX_METADATA_ITEMS),
        )
        object.__setattr__(self, "exit_reason", _redact_text(self.exit_reason)[:_MAX_VALUE_CHARS])

    @property
    def terminal(self) -> bool:
        """Whether the driver has finished and PatchCollector may inspect the workspace."""
        return self.status in {
            EngineStatus.COMPLETED,
            EngineStatus.FAILED,
            EngineStatus.TIMED_OUT,
            EngineStatus.CANCELLED,
            EngineStatus.UNSUPPORTED,
        }


def _freeze_mapping(
    value: Mapping[str, object],
    *,
    reject_forbidden: bool = False,
    limit: int | None = None,
) -> MappingProxyType:
    if not isinstance(value, Mapping):
        raise TypeError("engine metadata/config must be a mapping")
    items = sorted(value.items(), key=lambda item: str(item[0]))
    if limit is not None:
        items = items[:limit]
    frozen: dict[str, object] = {}
    for key, item in items:
        normalized_key = str(key).lower().replace("-", "_")
        if reject_forbidden and _is_forbidden_key(normalized_key):
            raise EngineBoundaryError(f"engine config contains forbidden capability: {key}")
        frozen[str(key)] = _redact_value(item, key=str(key))
    return MappingProxyType(frozen)


def _redact_value(value: Any, *, key: str = "") -> Any:
    normalized_key = key.lower().replace("-", "_")
    if normalized_key in _SENSITIVE_KEYS or any(
        part in normalized_key for part in ("api_key", "secret", "password", "token")
    ):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda item: str(item[0]))[:_MAX_METADATA_ITEMS]
        return MappingProxyType(
            {
                str(item_key): _redact_value(item_value, key=str(item_key))
                for item_key, item_value in items
            }
        )
    if isinstance(value, list | tuple | set | frozenset):
        return tuple(_redact_value(item) for item in value)[:_MAX_METADATA_ITEMS]
    if isinstance(value, str):
        return _redact_text(value)[:_MAX_VALUE_CHARS]
    if isinstance(value, int | float | bool) or value is None:
        return value
    return _redact_text(repr(value))[:_MAX_VALUE_CHARS]


def _redact_text(value: str) -> str:
    return _SECRET_PATTERN.sub("[REDACTED]", str(value))


def _is_forbidden_key(key: str) -> bool:
    return key in _FORBIDDEN_CONFIG_KEYS or any(
        part in key for part in ("github", "publisher", "api_key", "secret", "password", "token")
    )


def _validate_config_keys(value: object) -> None:
    if not isinstance(value, Mapping):
        raise TypeError("engine metadata/config must be a mapping")
    for key, item in value.items():
        normalized_key = str(key).lower().replace("-", "_")
        if _is_forbidden_key(normalized_key):
            raise EngineBoundaryError(f"engine config contains forbidden capability: {key}")
        if isinstance(item, Mapping):
            _validate_config_keys(item)
        elif isinstance(item, list | tuple | set | frozenset):
            for nested in item:
                if isinstance(nested, Mapping):
                    _validate_config_keys(nested)


__all__ = [
    "EngineBoundaryError",
    "EngineOutcome",
    "EngineRequest",
    "EngineStatus",
    "EngineUsage",
    "ExecutionLease",
    "RepairTask",
]
