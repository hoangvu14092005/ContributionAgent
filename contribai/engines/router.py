"""Capability- and budget-aware routing across engine driver implementations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from contribai.engines.capabilities import EngineCapabilities, VersionPolicy
from contribai.engines.protocol import EngineDriver
from contribai.publishing.capability import Capability, CapabilityRequest
from contribai.publishing.policy import PolicyDecision, PolicyEngine


class RoutingMode(StrEnum):
    """Whether an engine selection may be used for a live run."""

    SHADOW = "shadow"
    REVIEW_ONLY = "review_only"
    LIVE = "live"


class EngineRoutingError(RuntimeError):
    """Raised when no registered driver satisfies the routing request."""


@dataclass(frozen=True, slots=True)
class EngineRoutingRequest:
    """Risk, complexity, cost and capability constraints for one route."""

    complexity: int = 1
    risk: int = 1
    expected_cost_usd: float = 0.0
    required_capabilities: frozenset[str] = frozenset()
    mode: RoutingMode = RoutingMode.SHADOW
    work_id: str = "router"
    resource: str = "workspace"

    def __post_init__(self) -> None:
        if self.complexity < 0 or self.risk < 0 or self.expected_cost_usd < 0:
            raise ValueError("routing constraints must be non-negative")
        object.__setattr__(
            self,
            "required_capabilities",
            frozenset(str(value) for value in self.required_capabilities),
        )
        object.__setattr__(self, "mode", RoutingMode(self.mode))


@dataclass(frozen=True, slots=True)
class EngineRegistration:
    """Static routing metadata for one driver."""

    name: str
    driver: EngineDriver
    max_complexity: int = 5
    max_risk: int = 5
    max_cost_usd: float = float("inf")
    capabilities: frozenset[str] = frozenset()
    policy_capabilities: frozenset[Capability] = frozenset()
    live_supported: bool = True
    priority: int = 0
    capability_snapshot: EngineCapabilities | None = None
    version_policy: VersionPolicy | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("engine registration name must not be empty")
        if min(self.max_complexity, self.max_risk, self.priority) < 0:
            raise ValueError("engine registration limits must be non-negative")
        if self.max_cost_usd < 0:
            raise ValueError("max_cost_usd must be non-negative")
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(
            self,
            "policy_capabilities",
            frozenset(Capability(value) for value in self.policy_capabilities),
        )


class EngineRouter:
    """Choose a driver without acquiring any workspace or GitHub write capability."""

    def __init__(
        self,
        *,
        policy_engine: PolicyEngine | None = None,
        require_pinned_live: bool = True,
    ) -> None:
        self._policy_engine = policy_engine
        self._require_pinned_live = require_pinned_live
        self._registrations: dict[str, EngineRegistration] = {}

    @property
    def registrations(self) -> tuple[EngineRegistration, ...]:
        return tuple(self._registrations[name] for name in sorted(self._registrations))

    def register(
        self,
        name: str,
        driver: EngineDriver,
        *,
        max_complexity: int = 5,
        max_risk: int = 5,
        max_cost_usd: float = float("inf"),
        capabilities: Iterable[str] = (),
        policy_capabilities: Iterable[Capability | str] = (),
        live_supported: bool = True,
        priority: int = 0,
        capability_snapshot: EngineCapabilities | None = None,
        version_policy: VersionPolicy | None = None,
    ) -> EngineRegistration:
        """Register or replace one driver descriptor."""
        registration = EngineRegistration(
            name=name,
            driver=driver,
            max_complexity=max_complexity,
            max_risk=max_risk,
            max_cost_usd=max_cost_usd,
            capabilities=frozenset(capabilities),
            policy_capabilities=frozenset(Capability(value) for value in policy_capabilities),
            live_supported=live_supported,
            priority=priority,
            capability_snapshot=capability_snapshot,
            version_policy=version_policy,
        )
        self._registrations[name] = registration
        return registration

    def route(
        self,
        request: EngineRoutingRequest | None = None,
        *,
        complexity: int = 1,
        risk: int = 1,
        expected_cost_usd: float = 0.0,
        required_capabilities: Iterable[str] = (),
        mode: RoutingMode = RoutingMode.SHADOW,
        work_id: str = "router",
        resource: str = "workspace",
        capability_snapshot: EngineCapabilities | None = None,
    ) -> EngineDriver:
        """Return the cheapest safe matching driver, or fail closed."""
        request = request or EngineRoutingRequest(
            complexity=complexity,
            risk=risk,
            expected_cost_usd=expected_cost_usd,
            required_capabilities=frozenset(required_capabilities),
            mode=mode,
            work_id=work_id,
            resource=resource,
        )
        eligible = [
            registration
            for registration in self._registrations.values()
            if self._matches(registration, request, capability_snapshot=capability_snapshot)
        ]
        if not eligible:
            raise EngineRoutingError(
                "no engine satisfies complexity, risk, cost and capability policy"
            )
        selected = min(
            eligible,
            key=lambda registration: (
                registration.max_cost_usd,
                -registration.priority,
                registration.name,
            ),
        )
        return selected.driver

    def route_registration(
        self,
        request: EngineRoutingRequest,
    ) -> EngineRegistration:
        """Return the selected descriptor for audit metadata and tests."""
        eligible = [
            registration
            for registration in self._registrations.values()
            if self._matches(registration, request)
        ]
        if not eligible:
            raise EngineRoutingError(
                "no engine satisfies complexity, risk, cost and capability policy"
            )
        return min(
            eligible,
            key=lambda registration: (
                registration.max_cost_usd,
                -registration.priority,
                registration.name,
            ),
        )

    def _matches(
        self,
        registration: EngineRegistration,
        request: EngineRoutingRequest,
        *,
        capability_snapshot: EngineCapabilities | None = None,
    ) -> bool:
        if request.complexity > registration.max_complexity:
            return False
        if request.risk > registration.max_risk:
            return False
        if request.expected_cost_usd > registration.max_cost_usd:
            return False
        if not request.required_capabilities.issubset(registration.capabilities):
            return False
        if request.mode == RoutingMode.LIVE and not registration.live_supported:
            return False
        snapshot = capability_snapshot or registration.capability_snapshot
        if (
            request.mode == RoutingMode.LIVE
            and self._require_pinned_live
            and (snapshot is None or registration.version_policy is None)
        ):
            return False
        if registration.version_policy and (
            snapshot is None
            or not registration.version_policy.accepts(
                snapshot, live=request.mode == RoutingMode.LIVE
            )
        ):
            return False
        if snapshot and any(
            not snapshot.supports(capability) for capability in request.required_capabilities
        ):
            return False
        if request.mode == RoutingMode.LIVE and snapshot and not snapshot.supports_live:
            return False
        if self._policy_engine:
            for capability in registration.policy_capabilities:
                decision = self._policy_engine.evaluate(
                    CapabilityRequest(
                        actor=registration.name,
                        capability=capability,
                        resource=request.resource,
                        work_id=request.work_id,
                    )
                )
                if decision != PolicyDecision.ALLOW:
                    return False
        return True


__all__ = [
    "EngineRegistration",
    "EngineRouter",
    "EngineRoutingError",
    "EngineRoutingRequest",
    "RoutingMode",
]
