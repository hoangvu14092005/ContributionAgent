"""Shared fixtures for engine contract and adapter tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from contribai.context.builder import ContextBuilder
from contribai.context.rules import RepoRules
from contribai.core.models import Repository
from contribai.engines.models import EngineRequest, ExecutionLease
from contribai.execution.budget import ExecutionBudget
from contribai.execution.credentials import CredentialLease
from contribai.execution.resource_policy import ResourcePolicy
from contribai.localization import ContributionTask
from contribai.publishing.policy import CapabilityPolicy


@pytest.fixture
def engine_request(tmp_path: Path) -> EngineRequest:
    repo = Repository(owner="owner", name="repo", full_name="owner/repo", language="Python")
    context = ContextBuilder(max_context_tokens=500).build(
        repo,
        files={"src/service.py": "def handle(value):\n    return value\n"},
    )
    return EngineRequest(
        work_id="work-1",
        attempt_id="attempt-1",
        task=ContributionTask(title="Fix handle", description="Return the transformed value"),
        context=context,
        repo_rules=RepoRules(),
        budget=ExecutionBudget(20, 2.0, 30, 3),
        capability_policy=CapabilityPolicy(),
        engine_config={"model": "gateway-model"},
    )


@pytest.fixture
def execution_lease(tmp_path: Path) -> ExecutionLease:
    return ExecutionLease(
        work_id="work-1",
        attempt_id="attempt-1",
        workspace_ref="snapshot-1",
        budget=ExecutionBudget(20, 2.0, 30, 3),
        workspace=SimpleNamespace(
            path=tmp_path,
            policy=ResourcePolicy(),
            snapshot_id="snapshot-1",
            attempt_id="attempt-1",
        ),
        credential_lease=CredentialLease(
            work_id="work-1",
            attempt_id="attempt-1",
            provider="gateway",
            endpoint="https://gateway.invalid/v1",
            token="scoped-model-token",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            max_cost_usd=2.0,
            lease_id="lease-1",
        ),
        model_gateway=object(),
        cancel_event=asyncio.Event(),
    )
