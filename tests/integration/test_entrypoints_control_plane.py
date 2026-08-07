"""Command and entrypoint control-plane integration contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contribai.cli.main import _submit_control_command as submit_cli_command
from contribai.control.command_service import CommandService, CommandStateError
from contribai.control.mode import ExecutionMode
from contribai.core.config import ContribAIConfig, GitHubConfig, LLMConfig, StorageConfig
from contribai.domain.state import WorkState
from contribai.publishing.permit import PublishSideEffect
from contribai.review.models import ReviewStatus
from contribai.scheduler.scheduler import ContribScheduler
from contribai.verification.models import (
    VerificationEvidence,
    VerificationReport,
    VerificationStatus,
)


async def _advance(memory, item, *targets):
    for target in targets:
        item = await memory.work_items.transition(
            item.id,
            target,
            expected_version=item.version,
        )
    return item


def _passing_report(candidate_hash: str) -> VerificationReport:
    return VerificationReport(
        status=VerificationStatus.PASSED,
        baseline_passed=True,
        syntax_passed=True,
        tests_passed=True,
        lint_passed=True,
        typecheck_passed=True,
        security_passed=True,
        quality_score=1.0,
        tests_run=1,
        tests_failed=0,
        evidence=(
            VerificationEvidence("baseline", True, "VERIFIED", command="git status --porcelain"),
            VerificationEvidence("tests", True, "VERIFIED", command="pytest -q"),
        ),
        candidate_hash=candidate_hash,
    )


@pytest.mark.asyncio
async def test_commands_share_persistent_work_item_lifecycle(memory) -> None:
    commands = CommandService(memory)
    item = await commands.submit(
        "https://github.com/owner/repo",
        issue_number=7,
        mode=ExecutionMode.SHADOW,
        budget={"max_steps": 3},
        idempotency_key="entrypoint-7",
        metadata={"source": "webhook"},
    )
    duplicate = await commands.submit(
        "owner/repo",
        issue_number=7,
        mode=ExecutionMode.SHADOW,
        budget={"max_steps": 3},
        idempotency_key="entrypoint-7",
    )
    assert duplicate.id == item.id

    with pytest.raises(CommandStateError):
        await commands.submit(
            "owner/repo",
            issue_number=8,
            mode=ExecutionMode.SHADOW,
            budget={"max_steps": 3},
            idempotency_key="entrypoint-7",
        )
    with pytest.raises(CommandStateError):
        await commands.submit(
            "owner/repo",
            issue_number=7,
            mode=ExecutionMode.SHADOW,
            budget={"max_steps": 999},
            idempotency_key="entrypoint-7",
        )
    with pytest.raises(CommandStateError, match="verified"):
        await commands.request_review(item.id, "candidate-1")

    cancelled = await commands.cancel(item.id)
    assert cancelled.state is WorkState.CLOSED


@pytest.mark.asyncio
async def test_review_requires_current_verified_attempt(memory) -> None:
    commands = CommandService(memory)
    item = await commands.submit("owner/repo", issue_number=8)
    item = await _advance(
        memory,
        item,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.SOLVING,
        WorkState.PATCH_COLLECTING,
        WorkState.PATCHED,
        WorkState.VERIFYING,
    )
    item, _ = await commands.record_verification(item.id, _passing_report("candidate-2"))
    request = await commands.request_review(item.id, "candidate-2")
    assert request.status is ReviewStatus.PENDING
    rejected = await commands.reject(request.id, "candidate-2", reason="needs work")
    assert rejected.state is WorkState.NEEDS_FIX
    resumed = await commands.resume(item.id)
    assert resumed.state is WorkState.PREPARING
    assert resumed.attempt == 2


@pytest.mark.asyncio
async def test_live_approved_review_issues_proof_bound_publish_permit(memory) -> None:
    commands = CommandService(memory)
    item = await commands.submit(
        "owner/repo",
        issue_number=12,
        mode=ExecutionMode.LIVE,
    )
    item = await _advance(
        memory,
        item,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.SOLVING,
        WorkState.PATCH_COLLECTING,
        WorkState.PATCHED,
        WorkState.VERIFYING,
    )
    item, verification_id = await commands.record_verification(
        item.id,
        _passing_report("patch-12"),
    )
    request = await commands.request_review(
        item.id,
        "patch-12",
        required_side_effects=(PublishSideEffect.CREATE_PR,),
    )
    item = await commands.approve(request.id, "patch-12")
    quota_id = await commands.reserve_publish_quota(
        item.id,
        provider="github",
        amount={"pull_requests": 1},
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    permit = await commands.issue_publish_permit(
        item.id,
        request.id,
        base_sha="base-12",
        patch_sha256="patch-12",
        verification_id=verification_id,
        quota_reservation_id=quota_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    assert permit.review_id == request.id
    assert permit.approved_side_effects == frozenset({PublishSideEffect.CREATE_PR})
    current = await commands.get(item.id)
    assert current.state is WorkState.PUBLISH_RESERVED
    cursor = await memory.connection.execute("SELECT COUNT(*) FROM publish_permits")
    assert (await cursor.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_shadow_and_review_only_work_cannot_issue_publish_permit(memory) -> None:
    for mode in (ExecutionMode.SHADOW, ExecutionMode.REVIEW_ONLY):
        commands = CommandService(memory)
        item = await commands.submit(
            f"owner/{mode.value}",
            mode=mode,
            idempotency_key=f"mode-{mode.value}",
        )
        item = await _advance(
            memory,
            item,
            WorkState.QUALIFIED,
            WorkState.RESERVED,
            WorkState.PREPARING,
            WorkState.SOLVING,
            WorkState.PATCH_COLLECTING,
            WorkState.PATCHED,
            WorkState.VERIFYING,
        )
        item, verification_id = await commands.record_verification(
            item.id,
            _passing_report(f"patch-{mode.value}"),
        )
        request = await commands.request_review(
            item.id,
            f"patch-{mode.value}",
            required_side_effects=(PublishSideEffect.CREATE_PR,),
        )
        item = await commands.approve(request.id, f"patch-{mode.value}")
        with pytest.raises(CommandStateError, match="live"):
            await commands.issue_publish_permit(
                item.id,
                request.id,
                base_sha="base",
                patch_sha256=f"patch-{mode.value}",
                verification_id=verification_id,
                quota_reservation_id="quota",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )


@pytest.mark.asyncio
async def test_permit_cannot_widen_reviewed_side_effect_scope(memory) -> None:
    commands = CommandService(memory)
    item = await commands.submit("owner/repo-scope", mode=ExecutionMode.LIVE)
    item = await _advance(
        memory,
        item,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.SOLVING,
        WorkState.PATCH_COLLECTING,
        WorkState.PATCHED,
        WorkState.VERIFYING,
    )
    item, verification_id = await commands.record_verification(
        item.id,
        _passing_report("scope-patch"),
    )
    request = await commands.request_review(
        item.id,
        "scope-patch",
        required_side_effects=(PublishSideEffect.CREATE_PR,),
    )
    item = await commands.approve(request.id, "scope-patch")
    quota_id = await commands.reserve_publish_quota(
        item.id,
        provider="github",
        amount={"pull_requests": 1},
    )
    with pytest.raises(CommandStateError, match="widen"):
        await commands.issue_publish_permit(
            item.id,
            request.id,
            base_sha="base",
            patch_sha256="scope-patch",
            verification_id=verification_id,
            quota_reservation_id=quota_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            approved_side_effects=frozenset(
                {PublishSideEffect.CREATE_PR, PublishSideEffect.CREATE_ISSUE}
            ),
        )


@pytest.mark.asyncio
async def test_cli_and_mcp_entrypoints_submit_work(memory, tmp_path) -> None:
    config = ContribAIConfig(
        github=GitHubConfig(token="test"),
        llm=LLMConfig(provider="gemini", api_key="test"),
        storage=StorageConfig(db_path=str(tmp_path / "cli.db")),
    )
    cli_item = await submit_cli_command(
        config,
        "owner/repo",
        mode=ExecutionMode.SHADOW,
        source="cli.target",
        idempotency_key="cli-entrypoint",
    )
    assert cli_item.mode is ExecutionMode.SHADOW

    import contribai.mcp_server as mcp_server

    with patch.object(mcp_server, "get_memory", AsyncMock(return_value=memory)):
        result = await mcp_server._submit_work(
            {"repo": "owner/repo", "issue_number": 9, "mode": "shadow"}
        )
    payload = json.loads(result[0].text)
    assert payload["status"] == "queued"
    assert payload["repo"] == "owner/repo"


@pytest.mark.asyncio
async def test_webhook_scheduler_and_web_helpers_use_explicit_mode(tmp_path) -> None:
    config = ContribAIConfig(
        github=GitHubConfig(token="test"),
        llm=LLMConfig(provider="gemini", api_key="test"),
        storage=StorageConfig(db_path=str(tmp_path / "scheduler.db")),
    )
    config.scheduler.mode = ExecutionMode.SHADOW
    pipeline = MagicMock()
    pipeline.run = AsyncMock(return_value=MagicMock(repos_analyzed=1, prs_created=0, errors=[]))

    with patch("contribai.scheduler.scheduler.ContribPipeline", return_value=pipeline):
        scheduler = ContribScheduler(config)
        await scheduler._run_pipeline()
    pipeline.run.assert_awaited_once_with(dry_run=True)

    from contribai.web import server as web_server

    web_server._memory = None
    item = await web_server._submit_control_command(
        config,
        "https://github.com/owner/web",
        ExecutionMode.REVIEW_ONLY,
        source="web.command",
    )
    assert item is not None
    assert item.mode is ExecutionMode.REVIEW_ONLY


@pytest.mark.asyncio
async def test_scheduler_live_queues_without_legacy_publish(tmp_path) -> None:
    config = ContribAIConfig(
        github=GitHubConfig(token="test"),
        llm=LLMConfig(provider="gemini", api_key="test"),
        storage=StorageConfig(db_path=str(tmp_path / "scheduler-live.db")),
    )
    config.scheduler.mode = ExecutionMode.LIVE
    pipeline = MagicMock()
    pipeline.run = AsyncMock()

    with patch("contribai.scheduler.scheduler.ContribPipeline", return_value=pipeline):
        scheduler = ContribScheduler(config)
        await scheduler._run_pipeline()

    pipeline.run.assert_not_awaited()
