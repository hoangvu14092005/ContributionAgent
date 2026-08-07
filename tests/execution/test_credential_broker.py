"""Scoped credential lease and model gateway tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from contribai.execution.budget import ExecutionBudget
from contribai.execution.credentials import (
    CredentialDeniedError,
    InMemoryCredentialBroker,
    ProviderCredential,
)
from contribai.execution.model_gateway import ModelGateway
from contribai.llm.models import LLMRequest, TaskType


def _budget() -> ExecutionBudget:
    return ExecutionBudget(
        max_steps=10,
        max_cost_usd=2.0,
        max_wall_time_sec=60,
        max_tool_failures=2,
    )


@pytest.mark.asyncio
async def test_lease_is_scoped_expiring_revivocation_and_cost_bounded() -> None:
    now = [datetime(2026, 1, 1, tzinfo=UTC).timestamp()]
    broker = InMemoryCredentialBroker(
        {"custom": ProviderCredential("https://gateway.invalid", "raw-provider-key")},
        lease_ttl_sec=10,
        clock=lambda: now[0],
    )
    lease = await broker.issue_model_lease("work-1", "attempt-1", "custom", _budget())

    assert "raw-provider-key" not in lease.token
    assert "raw-provider-key" not in repr(lease)
    assert broker.redact(f"token={lease.token} key=raw-provider-key") == (
        "token=[REDACTED] key=[REDACTED]"
    )
    assert (
        broker.authorize(
            lease,
            work_id="work-1",
            attempt_id="attempt-1",
            provider="custom",
            cost_usd=2.0,
        ).api_key
        == "raw-provider-key"
    )

    with pytest.raises(CredentialDeniedError, match="scope"):
        broker.authorize(
            lease,
            work_id="work-2",
            attempt_id="attempt-1",
            provider="custom",
        )
    with pytest.raises(CredentialDeniedError, match="cost"):
        broker.authorize(
            lease,
            work_id="work-1",
            attempt_id="attempt-1",
            provider="custom",
            cost_usd=2.01,
        )

    now[0] += 11
    with pytest.raises(CredentialDeniedError, match="expired"):
        broker.authorize(
            lease,
            work_id="work-1",
            attempt_id="attempt-1",
            provider="custom",
        )

    now[0] = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    await broker.revoke(lease.lease_id)
    with pytest.raises(CredentialDeniedError, match="revoked"):
        broker.authorize(
            lease,
            work_id="work-1",
            attempt_id="attempt-1",
            provider="custom",
        )


@pytest.mark.asyncio
async def test_broker_denies_unallowlisted_provider_and_exhausted_budget() -> None:
    broker = InMemoryCredentialBroker(
        {"custom": {"endpoint": "https://gateway.invalid", "api_key": "secret"}},
        allowed_providers={"custom"},
    )
    with pytest.raises(CredentialDeniedError, match="not allowed"):
        await broker.issue_model_lease("work", "attempt", "openai", _budget())

    exhausted = _budget()
    await exhausted.consume_step()
    exhausted.max_steps = 1
    with pytest.raises(CredentialDeniedError):
        await broker.issue_model_lease("work", "attempt", "custom", exhausted)


@pytest.mark.asyncio
async def test_model_gateway_is_the_only_boundary_that_receives_raw_key() -> None:
    from contribai.core.config import LLMConfig

    broker = InMemoryCredentialBroker(
        {"custom": ProviderCredential("https://model-gateway.invalid", "raw-secret")}
    )
    config = LLMConfig(
        provider="custom",
        model="gateway-model",
        api_key="raw-secret",
        model_gateway_required=True,
    )
    from contribai.core.exceptions import LLMError
    from contribai.llm.provider import create_llm_provider

    with pytest.raises(LLMError, match="model gateway is required"):
        create_llm_provider(config)

    lease = await broker.issue_model_lease("work-1", "attempt-1", "custom", _budget())
    request = LLMRequest(
        task=TaskType.CODE_GEN,
        provider="custom",
        model="gateway-model",
        messages=({"role": "user", "content": "hello"},),
        timeout_sec=5,
        max_tokens=20,
        credential_scope=lease.lease_id,
    )

    captured: list[tuple[str, str]] = []

    class StubProvider:
        async def complete_request(self, incoming: LLMRequest) -> str:
            captured.append((incoming.provider, incoming.credential_scope or ""))
            return "ok"

        async def close(self) -> None:
            return None

    def factory(gateway_config):
        assert gateway_config.api_key == "raw-secret"
        assert gateway_config.model_gateway_required is False
        return StubProvider()

    gateway = ModelGateway(broker, {"custom": config}, provider_factory=factory)
    assert await gateway.complete_request(request, lease) == "ok"
    assert captured == [("custom", lease.lease_id)]
    assert broker.redact(lease.token) == "[REDACTED]"

    with pytest.raises(CredentialDeniedError, match="scope"):
        await gateway.complete_request(replace(request, credential_scope=None), lease)
    with pytest.raises(CredentialDeniedError, match="scope"):
        await gateway.complete_request(replace(request, credential_scope="other-lease"), lease)
