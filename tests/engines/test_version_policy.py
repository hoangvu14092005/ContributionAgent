"""Engine version pinning tests."""

from __future__ import annotations

import pytest

from contribai.engines.capabilities import CapabilityProbeError, EngineCapabilities, VersionPolicy
from contribai.engines.router import EngineRouter, EngineRoutingError, RoutingMode


def _snapshot(**overrides) -> EngineCapabilities:
    values = {
        "engine": "codex",
        "version": "1.2.3",
        "interface_version": "app-1",
        "cancellation": True,
        "model_gateway": True,
        "sandbox": True,
    }
    values.update(overrides)
    return EngineCapabilities(**values)


def test_version_policy_accepts_range_and_rejects_latest_or_wrong_interface() -> None:
    policy = VersionPolicy(
        engine="codex",
        min_version="1.0.0",
        max_version="1.5.0",
        interface_version="app-1",
    )

    assert policy.accepts(_snapshot()) is True
    assert policy.accepts(_snapshot(version="1.6.0")) is False
    assert policy.accepts(_snapshot(version="latest")) is False
    assert policy.accepts(_snapshot(interface_version="app-2")) is False
    with pytest.raises(CapabilityProbeError):
        policy.assert_accepts(_snapshot(version="2.0.0"))


def test_live_policy_requires_cancellation_gateway_and_sandbox() -> None:
    policy = VersionPolicy(engine="codex")

    assert policy.accepts(_snapshot(cancellation=False), live=False) is True
    assert policy.accepts(_snapshot(cancellation=False), live=True) is False


def test_router_denies_unpinned_or_unsupported_live_backend() -> None:
    class Driver:
        async def run(self, request, execution):
            raise NotImplementedError

    router = EngineRouter()
    router.register(
        "codex",
        Driver(),
        capability_snapshot=_snapshot(),
        version_policy=VersionPolicy(engine="codex", min_version="1.0.0"),
    )
    assert router.route(mode=RoutingMode.LIVE) is not None

    router.register(
        "old",
        Driver(),
        capability_snapshot=_snapshot(version="0.5.0"),
        version_policy=VersionPolicy(engine="old", min_version="1.0.0"),
    )
    with pytest.raises(EngineRoutingError):
        EngineRouter().route(mode=RoutingMode.LIVE)
