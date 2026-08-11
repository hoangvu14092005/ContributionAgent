"""Capability probe tests."""

from __future__ import annotations

import sys

import pytest

from contribai.engines.capabilities import CapabilityProbeError, EngineCapabilities
from contribai.engines.probes import BinaryEngineProbe, ServerEngineProbe, StaticEngineProbe


@pytest.mark.asyncio
async def test_static_and_server_probes_return_immutable_capability_snapshots() -> None:
    snapshot = EngineCapabilities(
        engine="codex",
        version="1.2.3",
        interface_version="jsonrpc-1",
        cancellation=True,
        model_gateway=True,
        sandbox=True,
    )

    assert await StaticEngineProbe(snapshot).probe() == snapshot
    server = ServerEngineProbe(
        lambda: {
            "version": "1.2.3",
            "cancellation": True,
            "model_gateway": True,
        },
        engine="codex",
        interface_version="jsonrpc-1",
    )
    assert (await server.probe()).supports_live is True


@pytest.mark.asyncio
async def test_binary_probe_is_bounded_and_parses_version() -> None:
    probe = BinaryEngineProbe(
        sys.executable,
        engine="python",
        interface_version="cli-1",
        version_args=("--version",),
    )

    snapshot = await probe.probe()

    assert snapshot.engine == "python"
    assert snapshot.version.split(".")[0].isdigit()


@pytest.mark.asyncio
async def test_probe_errors_when_version_or_response_is_invalid() -> None:
    with pytest.raises(CapabilityProbeError):
        await BinaryEngineProbe(
            sys.executable,
            engine="python",
            interface_version="cli-1",
            version_args=("-c", "print('no version')"),
        ).probe()

    with pytest.raises(CapabilityProbeError):
        await ServerEngineProbe(
            lambda: ["not", "an", "object"],
            engine="server",
            interface_version="http-1",
        ).probe()
