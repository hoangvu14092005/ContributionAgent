"""Bounded binary and server probes for engine capability snapshots."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from contribai.engines.capabilities import CapabilityProbeError, EngineCapabilities

_MAX_PROBE_OUTPUT = 16_000


class EngineProbe(Protocol):
    """Adapter contract for capability discovery."""

    async def probe(self) -> EngineCapabilities: ...


class StaticEngineProbe:
    """Useful for SDK adapters that already expose a verified snapshot."""

    def __init__(self, snapshot: EngineCapabilities) -> None:
        self.snapshot = snapshot

    async def probe(self) -> EngineCapabilities:
        return self.snapshot


class BinaryEngineProbe:
    """Run a version command with bounded output and timeout."""

    def __init__(
        self,
        binary: str,
        *,
        engine: str,
        interface_version: str,
        version_args: tuple[str, ...] = ("--version",),
        timeout_sec: float = 10.0,
        parser: Callable[[str], str] | None = None,
        capabilities: Mapping[str, bool] | None = None,
    ) -> None:
        if timeout_sec <= 0:
            raise ValueError("probe timeout must be positive")
        self.binary = binary
        self.engine = engine
        self.interface_version = interface_version
        self.version_args = version_args
        self.timeout_sec = timeout_sec
        self.parser = parser or _first_version
        self.capabilities = dict(capabilities or {})

    async def probe(self) -> EngineCapabilities:
        try:
            process = await asyncio.create_subprocess_exec(
                self.binary,
                *self.version_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), self.timeout_sec)
        except (OSError, TimeoutError) as exc:
            raise CapabilityProbeError(f"unable to probe engine binary {self.binary}") from exc
        output = stdout.decode(errors="replace")[:_MAX_PROBE_OUTPUT]
        version = self.parser(output)
        return EngineCapabilities(
            engine=self.engine,
            version=version,
            interface_version=self.interface_version,
            **self.capabilities,
        )


class ServerEngineProbe:
    """Probe a JSON capability endpoint using an injected async fetcher."""

    def __init__(
        self,
        fetch: Callable[[], Any],
        *,
        engine: str,
        interface_version: str,
    ) -> None:
        self.fetch = fetch
        self.engine = engine
        self.interface_version = interface_version

    async def probe(self) -> EngineCapabilities:
        raw = self.fetch()
        payload = await raw if hasattr(raw, "__await__") else raw
        if not isinstance(payload, Mapping):
            raise CapabilityProbeError("engine capability endpoint did not return an object")
        try:
            values = dict(payload)
            values.setdefault("engine", self.engine)
            values.setdefault("interface_version", self.interface_version)
            values.pop("digest", None)
            return EngineCapabilities(**values)
        except (TypeError, ValueError) as exc:
            raise CapabilityProbeError("invalid engine capability response") from exc


def _first_version(output: str) -> str:
    for token in output.replace("\n", " ").split():
        if token.startswith("v") and token[1:2].isdigit():
            return token[1:].strip(" ,;)")
        if token[:1].isdigit() and "." in token:
            return token.strip(" ,;)")
    raise CapabilityProbeError("engine version was not found in probe output")


__all__ = [
    "BinaryEngineProbe",
    "EngineProbe",
    "ServerEngineProbe",
    "StaticEngineProbe",
]
