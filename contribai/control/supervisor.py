"""Durable execution boundary for live WorkItems."""

from __future__ import annotations

import logging
import re
from typing import Protocol

from contribai.control.command_service import CommandService
from contribai.control.mode import ExecutionMode
from contribai.domain.state import WorkState, allowed_transitions
from contribai.domain.work_item import WorkItem
from contribai.orchestrator.memory import Memory
from contribai.storage.work_items import StaleWorkItemError

logger = logging.getLogger(__name__)

_RUNNABLE_STATES = frozenset(
    {
        WorkState.DISCOVERED,
        WorkState.QUALIFIED,
        WorkState.RESERVED,
        WorkState.PREPARING,
        WorkState.SOLVING,
    }
)
_CLAIM_TRANSITIONS = {
    WorkState.DISCOVERED: WorkState.QUALIFIED,
    WorkState.QUALIFIED: WorkState.RESERVED,
    WorkState.RESERVED: WorkState.PREPARING,
    WorkState.PREPARING: WorkState.SOLVING,
}
_SECRET_PATTERN = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]+|gh[pousr]_[a-z0-9_-]+|bearer\s+[a-z0-9._~+/=-]+)\b"
)


class WorkItemExecutor(Protocol):
    """Application-specific execution implementation behind the supervisor."""

    async def execute(self, item: WorkItem, commands: CommandService) -> WorkItem | None:
        """Execute one item after the supervisor has reached ``SOLVING``."""
        ...


class ExecutionSupervisor:
    """Advance and execute live work through one durable command boundary.

    The supervisor is deliberately independent from a coding engine.  It owns
    lifecycle claiming and failure handling; an executor owns analysis,
    workspace, verification and publishing details.  Optimistic versions in
    ``WorkItemRepository`` make a second worker lose the claim instead of
    running the same WorkItem concurrently.
    """

    def __init__(
        self,
        memory: Memory,
        *,
        executor: WorkItemExecutor,
        commands: CommandService | None = None,
    ) -> None:
        self._memory = memory
        self._commands = commands or CommandService(memory)
        self._executor = executor

    @property
    def commands(self) -> CommandService:
        """Return the command service used by this worker."""
        return self._commands

    async def run_once(self, work_id: str) -> WorkItem:
        """Claim and execute one live WorkItem, returning its latest snapshot."""
        item = await self._commands.get(work_id)
        if item.mode is not ExecutionMode.LIVE:
            await self._memory.work_items.append_event(
                item.id,
                "execution_skipped",
                expected_version=item.version,
                payload={"reason": "only live WorkItems are executable"},
            )
            return item
        if item.state not in _RUNNABLE_STATES:
            return item

        try:
            claimed = await self._claim_for_execution(item)
        except StaleWorkItemError:
            # Another worker won the optimistic claim.  Never retry the engine
            # from this process; return the winner's durable snapshot.
            latest = await self._commands.get(work_id)
            logger.info("WorkItem %s was claimed by another worker", work_id)
            return latest

        try:
            result = await self._executor.execute(claimed, self._commands)
        except Exception as exc:
            logger.error(
                "Live WorkItem %s executor failed: %s",
                work_id,
                _safe_error(exc),
            )
            return await self._fail_closed(work_id, exc)

        if isinstance(result, WorkItem):
            return result
        return await self._commands.get(work_id)

    async def run_pending(self, work_ids: tuple[str, ...] | list[str]) -> list[WorkItem]:
        """Run a bounded explicit list of queued WorkItems.

        Queue discovery stays outside this class so callers can choose their
        scheduler/back-pressure policy without weakening the claim boundary.
        """
        results: list[WorkItem] = []
        for work_id in work_ids:
            results.append(await self.run_once(work_id))
        return results

    async def _claim_for_execution(self, item: WorkItem) -> WorkItem:
        current = item
        while current.state in _CLAIM_TRANSITIONS:
            target = _CLAIM_TRANSITIONS[current.state]
            current = await self._memory.work_items.transition(
                current.id,
                target,
                expected_version=current.version,
                reason="execution supervisor claimed WorkItem",
            )
        if current.state is not WorkState.SOLVING:
            raise RuntimeError(f"WorkItem {current.id} cannot enter solving from {current.state}")
        return current

    async def _fail_closed(self, work_id: str, exc: Exception) -> WorkItem:
        current = await self._commands.get(work_id)
        reason = f"execution failed: {type(exc).__name__}: {_safe_error(exc)}"
        if WorkState.CLOSED in allowed_transitions(current.state):
            try:
                return await self._commands.cancel(work_id, reason=reason[:500])
            except StaleWorkItemError:
                return await self._commands.get(work_id)
        try:
            await self._memory.work_items.append_event(
                work_id,
                "execution_failed",
                expected_version=current.version,
                payload={"reason": reason[:500], "state": current.state.value},
            )
        except StaleWorkItemError:
            return await self._commands.get(work_id)
        return await self._commands.get(work_id)


def _safe_error(exc: Exception) -> str:
    """Bound exception text before it becomes durable event metadata."""
    return _SECRET_PATTERN.sub("[REDACTED]", str(exc))[:400]


__all__ = ["ExecutionSupervisor", "WorkItemExecutor"]
