"""Command and entrypoint control-plane integration contracts."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contribai.cli.main import _submit_control_command as submit_cli_command
from contribai.control.command_service import CommandService, CommandStateError
from contribai.control.mode import ExecutionMode
from contribai.core.config import ContribAIConfig, GitHubConfig, LLMConfig, StorageConfig
from contribai.domain.state import WorkState
from contribai.review.models import ReviewStatus
from contribai.scheduler.scheduler import ContribScheduler


@pytest.mark.asyncio
async def test_commands_share_persistent_work_item_and_review_lifecycle(memory) -> None:
    commands = CommandService(memory)
    item = await commands.submit(
        "https://github.com/owner/repo",
        issue_number=7,
        mode=ExecutionMode.SHADOW,
        idempotency_key="entrypoint-7",
        metadata={"source": "webhook"},
    )
    duplicate = await commands.submit(
        "owner/repo",
        issue_number=7,
        mode=ExecutionMode.SHADOW,
        idempotency_key="entrypoint-7",
    )
    assert duplicate.id == item.id
    with pytest.raises(CommandStateError):
        await commands.submit(
            "owner/repo",
            issue_number=8,
            mode=ExecutionMode.SHADOW,
            idempotency_key="entrypoint-7",
        )

    request = await commands.request_review(item.id, "candidate-1")
    assert request.status is ReviewStatus.PENDING
    approved = await commands.approve(request.id, "candidate-1")
    assert approved.state is WorkState.DISCOVERED

    cancelled = await commands.cancel(item.id)
    assert cancelled.state is WorkState.CLOSED
    events = await memory.work_items.list_events(item.id)
    assert [event.event_type for event in events] == [
        "created",
        "command_submitted",
        "review_requested",
        "review_approved",
        "transition",
    ]


@pytest.mark.asyncio
async def test_resume_and_reject_follow_fail_closed_state_edges(memory) -> None:
    commands = CommandService(memory)
    item = await commands.submit("owner/repo", issue_number=8)
    request = await commands.request_review(item.id, "candidate-2")
    rejected = await commands.reject(request.id, "candidate-2", reason="needs work")

    assert rejected.state is WorkState.DISCOVERED
    resumed = await commands.resume(item.id)
    assert resumed.state is WorkState.DISCOVERED


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
