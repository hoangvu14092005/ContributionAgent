"""Engine capability snapshots and production version policies."""

from __future__ import annotations

import re
from dataclasses import dataclass


class CapabilityProbeError(RuntimeError):
    """Raised when an engine cannot prove its runtime capabilities."""


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    """Immutable capability snapshot recorded before an engine run."""

    engine: str
    version: str
    interface_version: str
    streaming: bool = False
    resume: bool = False
    cancellation: bool = False
    diff_events: bool = False
    approvals: bool = False
    model_gateway: bool = False
    sandbox: bool = True
    digest: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.engine.strip()
            or not self.version.strip()
            or not self.interface_version.strip()
        ):
            raise ValueError("engine capability identity is required")

    @property
    def supports_live(self) -> bool:
        """Whether required safety/runtime primitives are present."""
        return self.cancellation and self.model_gateway and self.sandbox

    def supports(self, capability: str) -> bool:
        """Resolve a capability name without exposing mutable adapter state."""
        aliases = {
            "streaming": "streaming",
            "resume": "resume",
            "cancellation": "cancellation",
            "diff_events": "diff_events",
            "approvals": "approvals",
            "model_gateway": "model_gateway",
            "sandbox": "sandbox",
        }
        attribute = aliases.get(capability.lower().replace("-", "_"))
        return bool(attribute and getattr(self, attribute, False))


@dataclass(frozen=True, slots=True)
class VersionPolicy:
    """Pinned version range/digest accepted for one engine adapter."""

    engine: str
    min_version: str = "0.0.0"
    max_version: str | None = None
    digest: str | None = None
    interface_version: str | None = None
    require_live_capabilities: bool = True

    def accepts(self, snapshot: EngineCapabilities, *, live: bool = False) -> bool:
        if snapshot.engine != self.engine:
            return False
        if self.interface_version and snapshot.interface_version != self.interface_version:
            return False
        if self.digest and snapshot.digest != self.digest:
            return False
        if not _version_in_range(snapshot.version, self.min_version, self.max_version):
            return False
        return not (live and self.require_live_capabilities and not snapshot.supports_live)

    def assert_accepts(self, snapshot: EngineCapabilities, *, live: bool = False) -> None:
        if not self.accepts(snapshot, live=live):
            raise CapabilityProbeError(
                f"engine {snapshot.engine}@{snapshot.version} does not satisfy pinned policy"
            )


def _version_in_range(version: str, minimum: str, maximum: str | None) -> bool:
    actual = _parse_version(version)
    lower = _parse_version(minimum)
    if actual is None or lower is None or actual < lower:
        return False
    if maximum is not None:
        upper = _parse_version(maximum)
        if upper is None or actual > upper:
            return False
    return True


def _parse_version(value: str) -> tuple[int, int, int, str] | None:
    if value.lower() in {"latest", "unknown", ""}:
        return None
    match = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+](.+))?", value.strip())
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2) or 0),
        int(match.group(3) or 0),
        match.group(4) or "",
    )


__all__ = ["CapabilityProbeError", "EngineCapabilities", "VersionPolicy"]
