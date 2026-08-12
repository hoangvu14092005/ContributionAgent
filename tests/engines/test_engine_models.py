"""Engine runtime contract tests."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta

import pytest

from contribai.context.builder import ContextBuilder
from contribai.core.models import Repository
from contribai.engines.models import (
    EngineOutcome,
    EngineRequest,
    EngineStatus,
    EngineUsage,
    ExecutionLease,
)
from contribai.engines.protocol import EngineBoundaryError
from contribai.execution.budget import ExecutionBudget
from contribai.execution.credentials import CredentialLease
from contribai.execution.trajectory import ExecutionEvent
from contribai.localization import ContributionTask
from contribai.publishing.policy import CapabilityPolicy


def _context():
    repo = Repository(owner="owner", name="repo", full_name="owner/repo", language="Python")
    return ContextBuilder(max_context_tokens=200).build(
        repo,
        files={"src/service.py": "def handle(value):\n    return value\n"},
    )


def test_engine_outcome_is_terminal_audit_data_without_patch_authority() -> None:
    field_names = {field.name for field in fields(EngineOutcome)}

    assert "patch" not in field_names
    assert "candidate" not in field_names
    assert "publish_result" not in field_names

    outcome = EngineOutcome(
        status=EngineStatus.COMPLETED,
        exit_reason="completed",
        events=(
            ExecutionEvent(
                "tool",
                {"api_key": "sk-secret", "output": "x"},
            ),
        ),
        usage=EngineUsage(tool_calls=1),
        cost_usd=0.25,
        trajectory_id="trajectory-1",
        engine_version="native@1",
        metadata={"token": "ghp-secret", "safe": "ok"},
    )

    assert outcome.terminal is True
    assert "sk-secret" not in str(outcome.events[0].data)
    assert "ghp-secret" not in str(outcome.metadata)
    assert outcome.metadata["safe"] == "ok"


def test_engine_outcome_bounds_events_and_metadata() -> None:
    events = tuple(ExecutionEvent("event", {"i": index}) for index in range(1_100))
    metadata = {str(index): "x" * 10_000 for index in range(100)}

    outcome = EngineOutcome(
        status=EngineStatus.FAILED,
        exit_reason="bounded",
        events=events,
        usage=EngineUsage(),
        cost_usd=0,
        trajectory_id="trajectory-1",
        engine_version="engine@1",
        metadata=metadata,
    )

    assert len(outcome.events) <= 1_000
    assert len(outcome.metadata) <= 64
    assert all(len(value) <= 2_000 for value in outcome.metadata.values() if isinstance(value, str))


def test_engine_request_freezes_config_and_rejects_capability_bypass() -> None:
    request = EngineRequest(
        work_id="work-1",
        attempt_id="attempt-1",
        task=ContributionTask(title="Fix handle"),
        context=_context(),
        repo_rules=(),
        budget=ExecutionBudget(10, 1, 60, 2),
        capability_policy=CapabilityPolicy(),
        engine_config={"model": "safe-model", "temperature": 0.1},
    )

    with pytest.raises(TypeError):
        request.engine_config["model"] = "other"  # type: ignore[index]

    with pytest.raises(EngineBoundaryError):
        EngineRequest(
            work_id="work-1",
            attempt_id="attempt-1",
            task=ContributionTask(title="Fix handle"),
            context=_context(),
            repo_rules=(),
            budget=ExecutionBudget(10, 1, 60, 2),
            capability_policy=CapabilityPolicy(),
            engine_config={"github_client": object()},
        )


def test_execution_lease_only_carries_scoped_runtime_handles() -> None:
    lease = ExecutionLease(
        work_id="work-1",
        attempt_id="attempt-1",
        workspace_ref="snapshot-1",
        budget=ExecutionBudget(10, 1, 60, 2),
        credential_lease=CredentialLease(
            work_id="work-1",
            attempt_id="attempt-1",
            provider="openai",
            endpoint="https://gateway.invalid",
            token="scoped-token",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            max_cost_usd=1,
            lease_id="lease-1",
        ),
    )

    assert lease.work_id == "work-1"
    assert lease.workspace_ref == "snapshot-1"
    assert not hasattr(lease, "github_client")
    assert not hasattr(lease, "publisher")
