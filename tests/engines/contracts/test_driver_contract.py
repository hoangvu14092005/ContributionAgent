"""Shared contract checks for every optional external EngineDriver."""

from __future__ import annotations

import inspect

import pytest

from contribai.engines.adapters import (
    CodexAppServerDriver,
    CodexExecDriver,
    ExternalEngineDriver,
    MiniSWEInProcessDriver,
    OpenCodeServerDriver,
    OpenHandsSDKDriver,
)
from contribai.engines.models import EngineOutcome, EngineStatus, ExecutionLease
from contribai.execution.budget import ExecutionBudget

DRIVER_TYPES = (
    MiniSWEInProcessDriver,
    OpenHandsSDKDriver,
    OpenCodeServerDriver,
    CodexExecDriver,
    CodexAppServerDriver,
)


@pytest.mark.parametrize("driver_type", DRIVER_TYPES)
def test_external_driver_implements_only_engine_run(driver_type):
    driver = driver_type()
    assert isinstance(driver, ExternalEngineDriver)
    assert hasattr(driver, "run")
    assert not any(
        hasattr(driver, name)
        for name in (
            "create_pull_request",
            "publish",
            "push",
            "github_client",
            "publisher",
        )
    )
    assert not any(
        name in inspect.signature(driver.run).parameters for name in ("patch", "candidate")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("driver_type", DRIVER_TYPES)
async def test_external_driver_without_outer_lease_is_unsupported(
    driver_type,
    engine_request,
):
    execution = ExecutionLease(
        work_id="work-1",
        attempt_id="attempt-1",
        workspace_ref="snapshot-1",
        budget=ExecutionBudget(10, 1.0, 30, 2),
    )

    outcome = await driver_type().run(engine_request, execution)

    assert outcome.status is EngineStatus.UNSUPPORTED
    assert "patch" not in outcome.metadata


def test_shared_outcome_contract_does_not_grow_publish_authority():
    fields = set(EngineOutcome.__dataclass_fields__)

    assert fields.isdisjoint({"patch", "candidate", "publish_result", "github_client"})
