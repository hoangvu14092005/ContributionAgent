"""Architecture tests for the capability policy boundary."""

from __future__ import annotations

import pytest

from contribai.core.config import ContribAIConfig, load_config
from contribai.core.exceptions import ConfigError
from contribai.publishing.capability import Capability, CapabilityRequest
from contribai.publishing.policy import CapabilityPolicy, PolicyDecision, PolicyEngine, PolicyRule


def make_request(
    capability: Capability,
    resource: str = "repositories/acme/widgets",
) -> CapabilityRequest:
    """Create a policy request for the GitHub publisher authority."""
    return CapabilityRequest(
        actor="github_publisher",
        capability=capability,
        resource=resource,
        work_id="work-123",
    )


@pytest.mark.parametrize(
    ("decision", "capability"),
    [
        (PolicyDecision.ALLOW, Capability.WORKSPACE_READ),
        (PolicyDecision.ASK, Capability.NETWORK),
        (PolicyDecision.DENY, Capability.WORKSPACE_WRITE),
    ],
)
def test_evaluates_explicit_policy_decisions(
    decision: PolicyDecision,
    capability: Capability,
) -> None:
    engine = PolicyEngine(
        CapabilityPolicy(
            rules=[
                PolicyRule(
                    actor="github_publisher",
                    capability=capability,
                    resource="repositories/acme/widgets",
                    decision=decision,
                )
            ]
        )
    )

    assert engine.evaluate(make_request(capability)) is decision


def test_exact_resource_rule_wins_over_matching_pattern() -> None:
    engine = PolicyEngine(
        CapabilityPolicy(
            rules=[
                PolicyRule(
                    actor="github_publisher",
                    capability=Capability.GITHUB_CREATE_PR,
                    resource="repositories/acme/*",
                    decision=PolicyDecision.DENY,
                ),
                PolicyRule(
                    actor="github_publisher",
                    capability=Capability.GITHUB_CREATE_PR,
                    resource="repositories/acme/widgets",
                    decision=PolicyDecision.ALLOW,
                ),
            ]
        )
    )

    assert engine.evaluate(make_request(Capability.GITHUB_CREATE_PR)) is PolicyDecision.ALLOW


def test_wildcard_resource_rule_matches() -> None:
    engine = PolicyEngine(
        CapabilityPolicy(
            rules=[
                PolicyRule(
                    actor="github_publisher",
                    capability=Capability.GITHUB_COMMENT,
                    resource="repositories/acme/*",
                    decision=PolicyDecision.ASK,
                )
            ]
        )
    )

    assert (
        engine.evaluate(make_request(Capability.GITHUB_COMMENT, "repositories/acme/other"))
        is PolicyDecision.ASK
    )


@pytest.mark.parametrize(
    "capability",
    [
        Capability.GITHUB_COMMENT,
        Capability.GITHUB_PUSH,
        Capability.GITHUB_CREATE_ISSUE,
        Capability.GITHUB_CREATE_PR,
        Capability.GITHUB_CLOSE_PR,
    ],
)
def test_github_write_capabilities_default_to_deny(capability: Capability) -> None:
    engine = PolicyEngine(ContribAIConfig().capability_policy)

    assert engine.evaluate(make_request(capability)) is PolicyDecision.DENY


def test_invalid_capability_policy_stops_config_loading(tmp_path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
capability_policy:
  rules:
    - actor: github_publisher
      capability: github.create_pr
      resource: repositories/acme/widgets
      decision: approve
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(config_file)


def test_unknown_capability_policy_fields_stop_config_loading(tmp_path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
capability_policy:
  rules:
    - actor: github_publisher
      capability: github.create_pr
      resource: repositories/acme/widgets
      decision: allow
      unexpected: true
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(config_file)
