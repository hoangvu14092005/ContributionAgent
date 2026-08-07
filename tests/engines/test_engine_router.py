"""Engine routing policy tests."""

from __future__ import annotations

import pytest

from contribai.engines.models import EngineOutcome
from contribai.engines.router import (
    EngineRouter,
    EngineRoutingError,
    EngineRoutingRequest,
    RoutingMode,
)
from contribai.publishing.capability import Capability
from contribai.publishing.policy import CapabilityPolicy, PolicyDecision, PolicyEngine, PolicyRule


class StubDriver:
    async def run(self, request, execution) -> EngineOutcome:
        raise NotImplementedError


def test_router_selects_cheapest_driver_that_meets_complexity_and_capability() -> None:
    cheap = StubDriver()
    capable = StubDriver()
    router = EngineRouter()
    router.register(
        "cheap",
        cheap,
        max_complexity=2,
        max_risk=2,
        max_cost_usd=0.2,
        capabilities={"streaming"},
    )
    router.register(
        "capable",
        capable,
        max_complexity=5,
        max_risk=3,
        max_cost_usd=0.8,
        capabilities={"streaming", "resume"},
    )

    assert router.route(complexity=1, risk=1, required_capabilities={"streaming"}) is cheap
    assert router.route(complexity=4, risk=1, required_capabilities={"resume"}) is capable


def test_router_applies_control_plane_policy_and_denies_ask_or_missing_rules() -> None:
    driver = StubDriver()
    policy = PolicyEngine(
        CapabilityPolicy(
            rules=[
                PolicyRule(
                    actor="native",
                    capability=Capability.WORKSPACE_READ,
                    resource="repo/*",
                    decision=PolicyDecision.ALLOW,
                )
            ]
        )
    )
    router = EngineRouter(policy_engine=policy)
    router.register(
        "native",
        driver,
        policy_capabilities={Capability.WORKSPACE_READ},
        capabilities={"workspace.read"},
    )

    assert (
        router.route(
            EngineRoutingRequest(
                required_capabilities=frozenset({"workspace.read"}),
                work_id="work-1",
                resource="repo/main",
            )
        )
        is driver
    )

    with pytest.raises(EngineRoutingError):
        router.route(
            EngineRoutingRequest(
                required_capabilities=frozenset({"workspace.read"}),
                work_id="work-1",
                resource="other/repo",
            )
        )


def test_router_denies_live_unsupported_and_no_match() -> None:
    router = EngineRouter()
    router.register("shadow-only", StubDriver(), live_supported=False)

    with pytest.raises(EngineRoutingError):
        router.route(mode=RoutingMode.LIVE)


def test_optional_registration_does_not_change_default_live_path() -> None:
    router = EngineRouter()
    registration = router.register_optional("codex", StubDriver())

    assert registration.live_supported is False
    with pytest.raises(EngineRoutingError):
        router.route(mode=RoutingMode.LIVE)
