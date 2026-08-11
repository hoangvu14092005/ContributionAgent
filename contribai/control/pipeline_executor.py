"""Native live executor that binds the legacy generator to the control plane."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from contribai.context.builder import ContextBuilder
from contribai.control.command_service import CommandService
from contribai.core.config import ContribAIConfig
from contribai.core.models import Contribution, PRResult, Repository
from contribai.domain.state import WorkState, allowed_transitions
from contribai.domain.work_item import WorkItem
from contribai.engines.candidates import PatchCandidate
from contribai.engines.leases import create_execution_lease
from contribai.engines.models import EngineRequest, EngineStatus
from contribai.engines.native import NativeEngineDriver, NativeExecutionResult
from contribai.engines.patch_collector import PatchCollector
from contribai.execution.budget import ExecutionBudget
from contribai.execution.resource_policy import ResourcePolicy
from contribai.execution.workspaces.base import PatchCandidate as WorkspacePatch
from contribai.execution.workspaces.manager import WorkspaceManager
from contribai.github.client import GitHubClient
from contribai.localization.models import ContributionTask
from contribai.orchestrator.memory import Memory
from contribai.publishing.github_publisher import GitHubPublisher
from contribai.publishing.idempotency import SQLiteIdempotencyStore
from contribai.publishing.permit import (
    ContributionPublishCandidate,
    PublishSideEffect,
)
from contribai.publishing.policy import PolicyEngine
from contribai.verification.engine import VerificationEngine
from contribai.verification.models import VerificationEvidence

if TYPE_CHECKING:
    from contribai.orchestrator.pipeline import ContribPipeline

logger = logging.getLogger(__name__)

_DEFAULT_BUDGET = {
    "max_steps": 100,
    "max_cost_usd": 10.0,
    "max_wall_time_sec": 900.0,
    "max_tool_failures": 5,
}
_SECRET_PATTERN = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]+|gh[pousr]_[a-z0-9_-]+|bearer\s+[a-z0-9._~+/=-]+)\b"
)


class PipelineWorkItemExecutor:
    """Execute one WorkItem through analysis, isolated patching and publishing."""

    def __init__(self, config: ContribAIConfig) -> None:
        self._config = config

    async def execute(self, item: WorkItem, commands: CommandService) -> WorkItem:
        if not self._config.sandbox.enabled:
            return await commands.cancel(
                item.id,
                reason="live execution requires sandbox.enabled=true",
            )

        pipeline = _new_pipeline(self._config)
        handler_box: dict[str, ControlledPublishHandler] = {}

        def factory(
            initialized: ContribPipeline,
        ):
            handler = ControlledPublishHandler(
                config=self._config,
                item=item,
                commands=commands,
                github=initialized._github,
                memory=initialized._memory,
            )
            handler_box["handler"] = handler
            return handler.publish

        try:
            if item.repo == "contribai/discovery":
                result = await pipeline.run(
                    dry_run=False,
                    publish_handler_factory=factory,
                )
            else:
                result = await pipeline.run_controlled(
                    item.repo,
                    publish_handler_factory=factory,
                    issue_number=item.issue_number,
                    max_prs=1,
                )
            current = await commands.get(item.id)
            if current.state in {
                WorkState.SOLVING,
                WorkState.PATCHED,
                WorkState.VERIFYING,
                WorkState.VERIFIED,
                WorkState.APPROVED,
            }:
                reason = "pipeline produced no publishable contribution"
                if result.errors:
                    reason = f"pipeline did not publish: {result.errors[-1]}"
                return await _close_if_allowed(commands, current, reason=reason)
            return current
        finally:
            handler = handler_box.get("handler")
            if handler is not None:
                await handler.close()


class ControlledPublishHandler:
    """Turn one generated Contribution into a permit-bearing PR side effect."""

    def __init__(
        self,
        *,
        config: ContribAIConfig,
        item: WorkItem,
        commands: CommandService,
        github: GitHubClient,
        memory: Memory,
    ) -> None:
        if github is None or memory is None:
            raise RuntimeError("controlled publisher requires initialized pipeline components")
        self._config = config
        self._handled = False
        self._item = item
        self._commands = commands
        self._github = github
        self._memory = memory
        self._root = Path(tempfile.mkdtemp(prefix=f"contribai-live-{item.id}-"))
        self._sources: dict[str, tuple[Path, str, WorkspaceManager]] = {}
        store = SQLiteIdempotencyStore[PRResult](
            memory.connection,
            serializer=lambda value: value.model_dump_json(),
            deserializer=PRResult.model_validate_json,
        )
        self._publisher = GitHubPublisher(
            github,
            PolicyEngine(config.capability_policy),
            idempotency_store=store,
        )

    async def publish(
        self,
        contribution: Contribution,
        finding,
        repo: Repository,
        guidelines,
        *,
        closes_issue: int | None = None,
    ) -> PRResult | None:
        """Verify and publish one contribution under the current WorkItem."""
        if self._handled:
            return None
        self._handled = True
        try:
            base_sha, _manager = await self._ensure_workspace_context(repo)
            candidate = ContributionPublishCandidate(
                contribution,
                repo,
                base_sha,
                guidelines=guidelines,
                closes_issue=closes_issue,
            )
            await self._transition(WorkState.PATCH_COLLECTING, "collecting engine patch")
            patch_candidate = await self._execute_and_collect(
                contribution,
                finding,
                repo,
                candidate,
            )
            await self._transition(WorkState.PATCHED, "patch collected")
            await self._transition(WorkState.VERIFYING, "verifying isolated patch")
            report = await self._verify_candidate(patch_candidate, candidate, repo)
            if not report.publishable:
                await self._commands.record_verification(self._item.id, report)
                return None

            _updated, verification_id = await self._commands.record_verification(
                self._item.id,
                report,
            )
            side_effects = [PublishSideEffect.CREATE_PR]
            if closes_issue is None and guidelines.requires_issue_link:
                side_effects.append(PublishSideEffect.CREATE_ISSUE)
            review = await self._commands.request_review(
                self._item.id,
                candidate.publish_sha256,
                required_side_effects=side_effects,
            )
            if self._config.pipeline.human_review:
                return None
            await self._commands.approve(review.id, candidate.publish_sha256)
            quota_id = await self._commands.reserve_publish_quota(
                self._item.id,
                provider="github",
                amount={"pull_requests": 1},
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
            )
            permit = await self._commands.issue_publish_permit(
                self._item.id,
                review.id,
                base_sha=candidate.base_sha,
                patch_sha256=patch_candidate.patch_sha256,
                publish_sha256=candidate.publish_sha256,
                verification_id=verification_id,
                quota_reservation_id=quota_id,
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
                approved_side_effects=frozenset(side_effects),
            )
            result = await self._publisher.publish(permit, candidate)
            current = await self._commands.get(self._item.id)
            await self._memory.work_items.record_side_effect(
                self._item.id,
                expected_version=current.version,
                effect_type=PublishSideEffect.CREATE_PR.value,
                target=result.pr_url,
                external_id=str(result.pr_number),
                external_url=result.pr_url,
                created_by_contribai=True,
                auto_close=False,
            )
            await self._memory.work_items.transition(
                self._item.id,
                WorkState.PUBLISHED,
                expected_version=current.version,
                reason="GitHubPublisher completed through PublishPermit",
                payload={"pr_number": result.pr_number, "pr_url": result.pr_url},
            )
            return result
        except Exception as exc:
            await self._abort(
                f"controlled publish failed: {type(exc).__name__}: {_safe_error(exc)}"
            )
            raise

    async def _execute_and_collect(
        self,
        contribution: Contribution,
        finding,
        repo: Repository,
        candidate: ContributionPublishCandidate,
    ) -> PatchCandidate:
        del repo
        base_sha, manager = await self._ensure_workspace_context(candidate.target_repo)
        if base_sha != candidate.base_sha:
            raise RuntimeError("source base SHA changed while preparing the workspace")
        budget = _budget_for(self._item)
        files = {
            change.path: change.original_content or change.new_content
            for change in (*contribution.changes, *contribution.tests_added)
        }
        context = ContextBuilder().build(
            candidate.target_repo,
            files=files,
            base_sha=candidate.base_sha,
            budget=budget,
        )
        attempt_id = f"attempt-{self._item.attempt}-native"
        workspace = await manager.create_attempt(
            self._item.id,
            candidate.base_sha,
            attempt_id,
            ResourcePolicy(network="deny"),
        )
        try:
            await _validate_before(workspace, contribution)
            lease = create_execution_lease(
                work_id=self._item.id,
                attempt_id=attempt_id,
                workspace_ref=workspace.snapshot_id,
                budget=budget,
                workspace=workspace,
                ttl_sec=budget.max_wall_time_sec,
            )

            async def apply_changes(_request, execution):
                bound_workspace = execution.workspace
                if bound_workspace is None:
                    raise RuntimeError("native execution lease has no workspace")
                for change in (*contribution.changes, *contribution.tests_added):
                    if change.is_deleted:
                        await bound_workspace.apply_patch(
                            WorkspacePatch(path=change.path, content=None, is_deleted=True)
                        )
                    else:
                        await bound_workspace.write_file(change.path, change.new_content)
                return NativeExecutionResult(
                    metadata={"changed_files": contribution.total_files_changed}
                )

            request = EngineRequest(
                work_id=self._item.id,
                attempt_id=attempt_id,
                task=ContributionTask.from_finding(finding),
                context=context,
                repo_rules=context.repo_rules,
                budget=budget,
                capability_policy=self._config.capability_policy,
                engine_config={"source": "contribai-generator"},
            )
            before = await PatchCollector().capture_before(workspace)
            outcome = await NativeEngineDriver(operation=apply_changes).run(request, lease)
            if outcome.status is not EngineStatus.COMPLETED:
                raise RuntimeError(f"native engine ended with {outcome.status.value}")
            await _validate_after(workspace, contribution)
            patch_candidate = await PatchCollector().collect(workspace, before, outcome)
            expected_paths = {
                change.path.replace("\\", "/")
                for change in (*contribution.changes, *contribution.tests_added)
            }
            if set(patch_candidate.changed_files) != expected_paths:
                raise RuntimeError("workspace diff does not match generated contribution files")
            return patch_candidate
        finally:
            await manager.destroy_attempt(workspace.snapshot_id)

    async def _verify_candidate(
        self,
        patch_candidate: PatchCandidate,
        publish_candidate: ContributionPublishCandidate,
        repo: Repository,
    ):
        _base_sha, manager = await self._ensure_workspace_context(repo)
        attempt_id = f"attempt-{self._item.attempt}-verify"
        workspace = await manager.create_attempt(
            self._item.id,
            publish_candidate.base_sha,
            attempt_id,
            ResourcePolicy(network="deny"),
        )
        try:
            report = await VerificationEngine().verify(patch_candidate, workspace)
            evidence = VerificationEvidence(
                check="publish_binding",
                passed=True,
                status="VERIFIED",
                output=(
                    f"repo={repo.full_name}; base_sha={publish_candidate.base_sha}; "
                    f"patch_sha256={patch_candidate.patch_sha256}; "
                    f"publish_sha256={publish_candidate.publish_sha256}"
                ),
            )
            return replace(
                report,
                candidate_hash=publish_candidate.publish_sha256,
                evidence=(*report.evidence, evidence),
            )
        finally:
            await manager.destroy_attempt(workspace.snapshot_id)

    async def _ensure_workspace_context(self, repo: Repository) -> tuple[str, WorkspaceManager]:
        existing = self._sources.get(repo.full_name)
        if existing is not None:
            return existing[1], existing[2]
        clone_url = repo.clone_url or f"https://github.com/{repo.full_name}.git"
        if not clone_url.startswith("https://github.com/"):
            raise RuntimeError("live source checkout requires an https://github.com clone URL")
        safe_repo = repo.full_name.replace("/", "-")
        source = self._root / safe_repo / "source"
        source.parent.mkdir(parents=True, exist_ok=True)
        result = await _run_process(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                repo.default_branch,
                clone_url,
                str(source),
            ],
            cwd=source.parent,
            env=ResourcePolicy(network="allow").sanitized_environment(),
        )
        if result.returncode != 0:
            raise RuntimeError(f"source checkout failed: {_safe_error(result.stderr)}")
        remote_result = await _run_process(["git", "remote", "remove", "origin"], cwd=source)
        if remote_result.returncode != 0:
            raise RuntimeError("source checkout retained a remote and cannot be used for execution")
        result = await _run_process(["git", "rev-parse", "HEAD"], cwd=source)
        if result.returncode != 0 or not result.stdout.strip():
            raise RuntimeError("source checkout did not produce a base SHA")
        base_sha = result.stdout.strip()
        workspaces = WorkspaceManager(
            source,
            workspace_root=source.parent / "attempts",
            backend="docker" if self._config.sandbox.enabled else "local",
            docker_image=self._config.sandbox.docker_image or "python:3.12-slim",
        )
        self._sources[repo.full_name] = (source, base_sha, workspaces)
        return base_sha, workspaces

    async def _transition(self, target: WorkState, reason: str) -> None:
        current = await self._commands.get(self._item.id)
        if current.state is target:
            return
        if target not in allowed_transitions(current.state):
            raise RuntimeError(f"cannot transition {current.state.value} -> {target.value}")
        await self._memory.work_items.transition(
            self._item.id,
            target,
            expected_version=current.version,
            reason=reason,
        )

    async def _abort(self, reason: str) -> None:
        current = await self._commands.get(self._item.id)
        if WorkState.CLOSED in allowed_transitions(current.state):
            with contextlib.suppress(Exception):
                await self._commands.cancel(self._item.id, reason=reason[:500])

    async def close(self) -> None:
        for _source, _base_sha, workspaces in self._sources.values():
            await workspaces.destroy_all()
        await asyncio.to_thread(shutil.rmtree, self._root, True)


async def _validate_before(workspace, contribution: Contribution) -> None:
    for change in (*contribution.changes, *contribution.tests_added):
        if change.is_new_file:
            with contextlib.suppress(FileNotFoundError):
                await workspace.read_file(change.path)
                raise RuntimeError(f"new file already exists in base: {change.path}")
            continue
        actual = await workspace.read_file(change.path)
        if change.original_content is not None and actual != change.original_content:
            raise RuntimeError(f"base content changed for {change.path}")


async def _validate_after(workspace, contribution: Contribution) -> None:
    for change in (*contribution.changes, *contribution.tests_added):
        if change.is_deleted:
            with contextlib.suppress(FileNotFoundError):
                await workspace.read_file(change.path)
                raise RuntimeError(f"deleted file still exists after engine run: {change.path}")
            continue
        actual = await workspace.read_file(change.path)
        if actual != change.new_content:
            raise RuntimeError(f"workspace content differs from contribution: {change.path}")


def _budget_for(item: WorkItem) -> ExecutionBudget:
    values = {**_DEFAULT_BUDGET, **item.budget.to_mapping()}
    return ExecutionBudget(
        max_steps=max(1, int(values["max_steps"])),
        max_cost_usd=max(0.0, float(values["max_cost_usd"])),
        max_wall_time_sec=max(1.0, float(values["max_wall_time_sec"])),
        max_tool_failures=max(0, int(values["max_tool_failures"])),
    )


def _safe_error(value: object) -> str:
    """Bound error text before it reaches logs or durable WorkItem events."""
    return _SECRET_PATTERN.sub("[REDACTED]", str(value))[:400]


async def _close_if_allowed(
    commands: CommandService,
    item: WorkItem,
    *,
    reason: str,
) -> WorkItem:
    if WorkState.CLOSED in allowed_transitions(item.state):
        return await commands.cancel(item.id, reason=reason[:500])
    return item


async def _run_process(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
):
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return _ProcessResult(
        returncode=process.returncode,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
    )


@dataclass(frozen=True, slots=True)
class _ProcessResult:
    returncode: int | None
    stdout: str
    stderr: str


def _new_pipeline(config: ContribAIConfig) -> ContribPipeline:
    from contribai.orchestrator.pipeline import ContribPipeline

    return ContribPipeline(config)


__all__ = ["ControlledPublishHandler", "PipelineWorkItemExecutor"]
