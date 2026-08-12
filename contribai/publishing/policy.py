"""Fail-closed capability policy evaluation."""

from __future__ import annotations

from enum import StrEnum
from fnmatch import fnmatchcase

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contribai.publishing.capability import GITHUB_PUBLISHER_ACTOR, Capability, CapabilityRequest

_GITHUB_WRITE_CAPABILITIES = frozenset(
    {
        Capability.GITHUB_COMMENT,
        Capability.GITHUB_PUSH,
        Capability.GITHUB_CREATE_ISSUE,
        Capability.GITHUB_CREATE_PR,
        Capability.GITHUB_CLOSE_PR,
    }
)


class PolicyDecision(StrEnum):
    """Outcomes returned by the policy engine."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PolicyRule(BaseModel):
    """A capability decision scoped to one actor and resource pattern."""

    model_config = ConfigDict(extra="forbid")

    actor: str
    capability: Capability
    resource: str
    decision: PolicyDecision

    @model_validator(mode="after")
    def validate_github_write_actor(self) -> PolicyRule:
        """Reserve GitHub write capabilities for the publisher authority."""
        if self.capability in _GITHUB_WRITE_CAPABILITIES and self.actor != GITHUB_PUBLISHER_ACTOR:
            raise ValueError(f"{self.capability} rules require actor {GITHUB_PUBLISHER_ACTOR!r}")
        return self


class CapabilityPolicy(BaseModel):
    """Configured capability rules, with an implicit deny default."""

    model_config = ConfigDict(extra="forbid")

    rules: list[PolicyRule] = Field(default_factory=list)


class PolicyEngine:
    """Evaluate capability requests deterministically and fail closed."""

    def __init__(self, policy: CapabilityPolicy | None = None) -> None:
        self._policy = policy or CapabilityPolicy()

    def evaluate(self, request: CapabilityRequest) -> PolicyDecision:
        """Return the exact rule decision, then a pattern decision, or deny."""
        applicable_rules = [
            rule
            for rule in self._policy.rules
            if rule.actor == request.actor and rule.capability == request.capability
        ]

        for rule in applicable_rules:
            if rule.resource == request.resource:
                return rule.decision

        for rule in applicable_rules:
            if fnmatchcase(request.resource, rule.resource):
                return rule.decision

        return PolicyDecision.DENY
