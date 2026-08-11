"""Scoped model credentials for isolated execution attempts."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from contribai.execution.budget import BudgetExceededError, ExecutionBudget


class CredentialError(RuntimeError):
    """Base error for credential issuance and authorization failures."""


class CredentialDeniedError(CredentialError):
    """Raised when a lease is outside its configured scope or policy."""


@dataclass(frozen=True, slots=True)
class CredentialLease:
    """Short-lived gateway token scoped to one work item and attempt."""

    work_id: str
    attempt_id: str
    provider: str
    endpoint: str
    token: str = field(repr=False)
    expires_at: datetime
    max_cost_usd: float
    lease_id: str = ""

    def __post_init__(self) -> None:
        if not self.work_id.strip() or not self.attempt_id.strip():
            raise ValueError("credential lease scope must not be empty")
        if not self.provider.strip() or not self.endpoint.strip():
            raise ValueError("credential lease provider and endpoint are required")
        if not self.token.strip():
            raise ValueError("credential lease token must not be empty")
        if self.expires_at.tzinfo is None:
            raise ValueError("credential lease expiry must be timezone-aware")
        if self.max_cost_usd < 0:
            raise ValueError("credential lease max_cost_usd must be non-negative")


@runtime_checkable
class CredentialBroker(Protocol):
    """Control-plane contract for scoped model access."""

    async def issue_model_lease(
        self,
        work_id: str,
        attempt_id: str,
        provider: str,
        budget: ExecutionBudget,
    ) -> CredentialLease: ...

    async def revoke(self, lease_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ProviderCredential:
    """Raw upstream credential retained only by the control-plane broker."""

    endpoint: str
    api_key: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _LeaseRecord:
    lease: CredentialLease
    token_digest: str
    raw_api_key: str


class InMemoryCredentialBroker:
    """Reference broker for one process; replace storage with a service later."""

    def __init__(
        self,
        provider_credentials: Mapping[str, ProviderCredential | Mapping[str, str]],
        *,
        allowed_providers: set[str] | frozenset[str] | None = None,
        lease_ttl_sec: float = 300.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if lease_ttl_sec <= 0:
            raise ValueError("lease_ttl_sec must be positive")
        self._credentials = {
            provider: self._normalize_credential(value)
            for provider, value in provider_credentials.items()
        }
        self._allowed_providers = frozenset(allowed_providers or self._credentials)
        self._lease_ttl_sec = lease_ttl_sec
        self._clock = clock
        self._leases: dict[str, _LeaseRecord] = {}
        self._revoked: set[str] = set()

    @staticmethod
    def _normalize_credential(
        value: ProviderCredential | Mapping[str, str],
    ) -> ProviderCredential:
        if isinstance(value, ProviderCredential):
            return value
        try:
            return ProviderCredential(endpoint=value["endpoint"], api_key=value["api_key"])
        except (KeyError, TypeError) as exc:
            raise ValueError("provider credentials require endpoint and api_key") from exc

    @classmethod
    def from_llm_config(cls, config, *, lease_ttl_sec: float = 300.0):
        """Build a broker for one configured provider in the control plane."""
        endpoint = config.base_url or config.custom_base_url or "in-process://model-gateway"
        return cls(
            {config.provider: ProviderCredential(endpoint=endpoint, api_key=config.api_key)},
            allowed_providers=set(config.allowed_model_providers) or None,
            lease_ttl_sec=lease_ttl_sec,
        )

    async def issue_model_lease(
        self,
        work_id: str,
        attempt_id: str,
        provider: str,
        budget: ExecutionBudget,
    ) -> CredentialLease:
        try:
            await budget.ensure_publish_allowed()
        except BudgetExceededError as exc:
            raise CredentialDeniedError("cannot issue a lease for an exhausted budget") from exc
        if provider not in self._allowed_providers or provider not in self._credentials:
            raise CredentialDeniedError(f"provider is not allowed: {provider}")
        credential = self._credentials[provider]
        now = datetime.fromtimestamp(self._clock(), UTC)
        lease_id = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        lease = CredentialLease(
            work_id=work_id,
            attempt_id=attempt_id,
            provider=provider,
            endpoint=credential.endpoint,
            token=token,
            expires_at=now + timedelta(seconds=self._lease_ttl_sec),
            max_cost_usd=max(0.0, budget.max_cost_usd - budget.cost_usd),
            lease_id=lease_id,
        )
        self._leases[lease_id] = _LeaseRecord(
            lease=lease,
            token_digest=self._digest(token),
            raw_api_key=credential.api_key,
        )
        return lease

    async def revoke(self, lease_id: str) -> None:
        if not lease_id.strip():
            return
        self._revoked.add(lease_id)

    def authorize(
        self,
        lease: CredentialLease,
        *,
        work_id: str,
        attempt_id: str,
        provider: str,
        cost_usd: float = 0.0,
    ) -> ProviderCredential:
        """Authorize a gateway call and return raw credentials internally."""
        if cost_usd < 0:
            raise ValueError("cost_usd must be non-negative")
        record = self._leases.get(lease.lease_id)
        if record is None or lease.lease_id in self._revoked:
            raise CredentialDeniedError("credential lease is unknown or revoked")
        if not hmac.compare_digest(record.token_digest, self._digest(lease.token)):
            raise CredentialDeniedError("credential lease token is invalid")
        stored = record.lease
        if (
            stored.work_id != work_id
            or stored.attempt_id != attempt_id
            or stored.provider != provider
            or lease.work_id != work_id
            or lease.attempt_id != attempt_id
            or lease.provider != provider
        ):
            raise CredentialDeniedError("credential lease scope mismatch")
        now = datetime.fromtimestamp(self._clock(), UTC)
        if now >= stored.expires_at:
            raise CredentialDeniedError("credential lease is expired")
        if cost_usd > stored.max_cost_usd:
            raise CredentialDeniedError("credential lease cost budget exceeded")
        return ProviderCredential(endpoint=stored.endpoint, api_key=record.raw_api_key)

    def redact(self, value: object) -> str:
        """Redact lease and upstream secrets from logs or trajectory metadata."""
        text = str(value)
        secrets_to_hide = [record.raw_api_key for record in self._leases.values()]
        secrets_to_hide.extend(record.lease.token for record in self._leases.values())
        for secret in secrets_to_hide:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text

    def resolve_upstream(
        self,
        lease: CredentialLease,
        *,
        work_id: str,
        attempt_id: str,
        provider: str,
        cost_usd: float = 0.0,
    ) -> ProviderCredential:
        """Alias used by the in-process gateway at the control-plane boundary."""
        return self.authorize(
            lease,
            work_id=work_id,
            attempt_id=attempt_id,
            provider=provider,
            cost_usd=cost_usd,
        )

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()
