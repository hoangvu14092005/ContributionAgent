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
_SCOPED_MODEL_ENV_NAMES = frozenset(
    {
        "CONTRIBAI_MODEL_GATEWAY_URL",
        "CONTRIBAI_MODEL_GATEWAY_PROVIDER",
        "CONTRIBAI_MODEL_GATEWAY_TOKEN",
        "CONTRIBAI_MODEL_GATEWAY_LEASE_ID",
    }
)
_SECRET_SUFFIXES = ("_TOKEN", "_API_KEY", "_SECRET", "_PASSWORD")


def _looks_secret(name: str) -> bool:
    upper = name.upper()
    return upper in _SECRET_ENV_NAMES or upper.endswith(_SECRET_SUFFIXES)


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

    def sanitized_environment(
        self,
        extra: dict[str, str] | None = None,
        *,
        allow_scoped_model_credentials: bool = False,
    ) -> dict[str, str]:
        """Return an environment without host credentials or socket handles.

        ``allow_scoped_model_credentials`` permits only the exact
        ``CONTRIBAI_MODEL_GATEWAY_*`` names issued by an execution lease. It
        never permits raw provider API keys or arbitrary ``*_TOKEN``,
        ``*_SECRET`` or ``*_PASSWORD`` variables.
        """
        environment = {
            key: value
            for key, value in os.environ.items()
            if not _looks_secret(key)
        }
        if extra:
            for key, value in extra.items():
                scoped = key in _SCOPED_MODEL_ENV_NAMES
                if scoped:
                    if not allow_scoped_model_credentials:
                        raise ValueError(f"scoped model credential is not allowed: {key}")
                elif _looks_secret(key):
                    raise ValueError(f"raw credential environment is not allowed: {key}")
                environment[key] = value
        return environment
