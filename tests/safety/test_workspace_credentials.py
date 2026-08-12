"""Workspace environment capability tests."""

from __future__ import annotations

from contribai.execution.resource_policy import ResourcePolicy


def test_resource_policy_scrubs_host_and_provider_credentials(monkeypatch) -> None:
    secrets = {
        "GITHUB_TOKEN": "github-secret",
        "SSH_AUTH_SOCK": "/tmp/agent.sock",
        "DOCKER_HOST": "unix:///var/run/docker.sock",
        "OPENAI_API_KEY": "openai-secret",
        "GEMINI_API_KEY": "gemini-secret",
        "ANTHROPIC_API_KEY": "anthropic-secret",
    }
    for key, value in secrets.items():
        monkeypatch.setenv(key, value)

    environment = ResourcePolicy().sanitized_environment()

    for key, value in secrets.items():
        assert key not in environment
        assert value not in environment.values()


def test_resource_policy_rejects_raw_secret_injected_as_extra_environment() -> None:
    import pytest

    with pytest.raises(ValueError, match="raw credential"):
        ResourcePolicy().sanitized_environment({"CUSTOM_API_KEY": "secret"})
