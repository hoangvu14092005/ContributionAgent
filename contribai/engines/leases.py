"""Factories and errors for scoped engine execution leases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from contribai.engines.models import EngineBoundaryError, ExecutionLease
from contribai.execution.budget import ExecutionBudget
from contribai.execution.credentials import CredentialLease
from contribai.execution.workspaces.base import Workspace


class LeaseExpiredError(EngineBoundaryError):
    """Raised when an engine attempts to run outside its execution lease."""


def create_execution_lease(
    *,
    work_id: str,
    attempt_id: str,
    workspace_ref: str,
    budget: ExecutionBudget,
    workspace: Workspace | None = None,
    credential_lease: CredentialLease | None = None,
    model_gateway: object | None = None,
    ttl_sec: float | None = None,
) -> ExecutionLease:
    """Create a lease with an optional wall-clock expiry."""
    if ttl_sec is not None and ttl_sec <= 0:
        raise ValueError("ttl_sec must be positive")
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_sec) if ttl_sec else None
    return ExecutionLease(
        work_id=work_id,
        attempt_id=attempt_id,
        workspace_ref=workspace_ref,
        budget=budget,
        credential_lease=credential_lease,
        model_gateway=model_gateway,
        expires_at=expires_at,
        workspace=workspace,
    )


__all__ = ["ExecutionLease", "LeaseExpiredError", "create_execution_lease"]
