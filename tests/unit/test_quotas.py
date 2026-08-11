"""Concurrency-safe publication quota tests."""

from __future__ import annotations

import asyncio

from contribai.core.quotas import AsyncPRQuota


async def test_async_pr_quota_reserves_at_most_configured_slots() -> None:
    quota = AsyncPRQuota(3)

    reservations = await asyncio.gather(*(quota.try_acquire() for _ in range(10)))

    assert sum(reservations) == 3
    assert quota.remaining == 0

    await quota.release()
    assert quota.remaining == 1
    assert await quota.try_acquire() is True
    assert await quota.try_acquire() is False
