"""Model gateway boundary that consumes scoped credential leases."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from contribai.execution.credentials import (
    CredentialBroker,
    CredentialDeniedError,
    CredentialLease,
)
from contribai.llm.models import LLMRequest

if TYPE_CHECKING:
    from contribai.core.config import LLMConfig
    from contribai.llm.provider import LLMProvider


class ModelGatewayError(RuntimeError):
    """Raised when a model call cannot cross the gateway boundary."""


class ModelGateway:
    """In-process reference gateway; raw provider keys stay on this side."""

    def __init__(
        self,
        broker: CredentialBroker,
        configs: Mapping[str, LLMConfig],
        *,
        provider_factory: Callable[[LLMConfig], LLMProvider] | None = None,
    ) -> None:
        self._broker = broker
        self._configs = dict(configs)
        self._provider_factory = provider_factory

    async def complete_request(self, request: LLMRequest, lease: CredentialLease) -> str:
        if request.credential_scope != lease.lease_id:
            raise CredentialDeniedError("request credential scope does not match the lease")
        if request.provider not in self._configs:
            raise ModelGatewayError(f"no control-plane config for provider: {request.provider}")
        resolver = getattr(self._broker, "resolve_upstream", None)
        if resolver is None:
            raise ModelGatewayError("credential broker does not support gateway resolution")
        try:
            credential = resolver(
                lease,
                work_id=lease.work_id,
                attempt_id=lease.attempt_id,
                provider=request.provider,
            )
        except CredentialDeniedError:
            raise
        except Exception as exc:
            raise ModelGatewayError("credential lease authorization failed") from exc

        config = self._configs[request.provider].model_copy(deep=True)
        config.provider = request.provider
        config.model = request.model
        config.api_key = credential.api_key
        config.base_url = credential.endpoint
        config.custom_base_url = credential.endpoint
        config.model_gateway_required = False
        factory = self._provider_factory
        if factory is None:
            from contribai.llm.provider import create_llm_provider

            factory = create_llm_provider
        provider = factory(config)
        try:
            return await provider.complete_request(request)
        finally:
            await provider.close()

    async def complete(self, request: LLMRequest, lease: CredentialLease) -> str:
        """Convenience alias for callers using the shorter gateway verb."""
        return await self.complete_request(request, lease)

    def redact(self, value: Any) -> str:
        """Delegate redaction without exposing broker internals to an engine."""
        redact = getattr(self._broker, "redact", None)
        return redact(value) if redact else str(value)


class InProcessModelGateway(ModelGateway):
    """Explicit name for the reference implementation."""


BrokeredModelGateway = InProcessModelGateway

__all__ = [
    "BrokeredModelGateway",
    "InProcessModelGateway",
    "ModelGateway",
    "ModelGatewayError",
]
