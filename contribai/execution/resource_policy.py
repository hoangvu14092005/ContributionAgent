"""Resource and capability defaults for an execution workspace."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

NetworkMode = Literal["deny", "ask", "allow"]

_SECRET_ENV_NAMES = frozenset(
    {
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "DOCKER_HOST",
        "DOCKER_CERT_PATH",
        "DOCKER_TLS_VERIFY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    }
)


@dataclass(frozen=True, slots=True)
class ResourcePolicy:
    """Explicit resource policy used by local and Docker workspaces.

    ``deny`` is the only safe default for network access.  ``ask`` is retained
    as a control-plane value, but a non-interactive workspace treats it as
    denied until a higher-level policy turns it into ``allow``.
    """

    network: NetworkMode = "deny"
    cpu_limit: float = 1.0
    memory_mb: int = 512
    pids_limit: int = 128

    def __post_init__(self) -> None:
        if self.network not in {"deny", "ask", "allow"}:
            raise ValueError("network must be deny, ask, or allow")
        if self.cpu_limit <= 0:
            raise ValueError("cpu_limit must be positive")
        if self.memory_mb <= 0:
            raise ValueError("memory_mb must be positive")
        if self.pids_limit <= 0:
            raise ValueError("pids_limit must be positive")

    @property
    def effective_network(self) -> Literal["none", "bridge"]:
        """Return the non-interactive Docker network mode."""
        return "bridge" if self.network == "allow" else "none"

    def sanitized_environment(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Return an environment without host credentials or socket handles."""
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in _SECRET_ENV_NAMES
            and not key.endswith("_TOKEN")
            and not key.endswith("_API_KEY")
        }
        if extra:
            for key, value in extra.items():
                if key in _SECRET_ENV_NAMES or key.endswith("_API_KEY"):
                    raise ValueError(f"raw credential environment is not allowed: {key}")
                environment[key] = value
        return environment
