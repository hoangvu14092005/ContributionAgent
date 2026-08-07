"""Tests for permit-gated, idempotent GitHub publishing."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from contribai.core.models import (
    Contribution,
    ContributionType,
    FileChange,
    Finding,
    Repository,
    Severity,
)
from contribai.publishing.capability import GITHUB_PUBLISHER_ACTOR, Capability
from contribai.publishing.github_publisher import GitHubPublisher, PublishPolicyError
from contribai.publishing.idempotency import InMemoryIdempotencyStore
from contribai.publishing.permit import (
    ContributionPublishCandidate,
    PublishPermit,
    PublishPermitError,
)
from contribai.publishing.policy import CapabilityPolicy, PolicyDecision, PolicyEngine, PolicyRule

NOW = datetime(2026, 8, 7, 12, tzinfo=UTC)
BASE_SHA = "b" * 40
PATCH_SHA256 = "c" * 64


@pytest.fixture
def target_repo() -> Repository:
    return Repository(
        owner="acme",
        name="widgets",
        full_name="acme/widgets",
        default_branch="main",
    )


@pytest.fixture
def contribution() -> Contribution:
    finding = Finding(
        id="finding-1",
        type=ContributionType.CODE_QUALITY,
        severity=Severity.MEDIUM,
        title="Remove dead code",
        description="An unused branch can be removed.",
        file_path="src/main.py",
    )
    return Contribution(
        finding=finding,
        contribution_type=ContributionType.CODE_QUALITY,
        title="refactor: remove dead code",
        description="Removed the unused branch.",
        changes=[
            FileChange(
                path="src/main.py",
                original_content="if False:\n    pass\n",
                new_content="",
            )
        ],
        commit_message="refactor: remove dead code",
        branch_name="refactor/remove-dead-code",
    )


@pytest.fixture
def candidate(
    contribution: Contribution,
    target_repo: Repository,
) -> ContributionPublishCandidate:
    return ContributionPublishCandidate(
        contribution=contribution,
        target_repo=target_repo,
        base_sha=BASE_SHA,
        patch_sha256=PATCH_SHA256,
    )


@pytest.fixture
def permit() -> PublishPermit:
    return PublishPermit(
        work_id="work-123",
        repo="acme/widgets",
        base_sha=BASE_SHA,
        patch_sha256=PATCH_SHA256,
        verification_id="verification-123",
        review_id="review-123",
        quota_reservation_id="quota-123",
        expires_at=NOW + timedelta(minutes=5),
    )


@pytest.fixture
def github(target_repo: Repository) -> AsyncMock:
    client = AsyncMock()
    client.get_authenticated_user.return_value = {
        "login": "contribai-bot",
        "id": 42,
        "name": "ContribAI Bot",
        "email": None,
    }
    client.get_repo_details.side_effect = RuntimeError("fork does not exist")
    client.fork_repository.return_value = Repository(
        owner="contribai-bot",
        name=target_repo.name,
        full_name=f"contribai-bot/{target_repo.name}",
        default_branch=target_repo.default_branch,
    )
    client.get_file_content_with_sha.return_value = ("old content", "existing-file-sha")
    client.create_pull_request.return_value = {
        "number": 73,
        "html_url": "https://github.com/acme/widgets/pull/73",
    }
    return client


def policy_engine(*capabilities: Capability) -> PolicyEngine:
    return PolicyEngine(
        CapabilityPolicy(
            rules=[
                PolicyRule(
                    actor=GITHUB_PUBLISHER_ACTOR,
                    capability=capability,
                    resource="repositories/acme/widgets",
                    decision=PolicyDecision.ALLOW,
                )
                for capability in capabilities
            ]
        )
    )


def make_publisher(github: AsyncMock, policy: PolicyEngine | None = None) -> GitHubPublisher:
    return GitHubPublisher(
        github=github,
        policy_engine=policy or policy_engine(Capability.GITHUB_PUSH, Capability.GITHUB_CREATE_PR),
        idempotency_store=InMemoryIdempotencyStore(),
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_valid_permit_publishes_candidate_at_bound_base_sha(
    github: AsyncMock,
    permit: PublishPermit,
    candidate: ContributionPublishCandidate,
) -> None:
    publisher = make_publisher(github)

    result = await publisher.publish(permit, candidate)

    assert result.pr_number == 73
    github.create_branch.assert_awaited_once_with(
        "contribai-bot",
        "widgets",
        "refactor/remove-dead-code",
        base_sha=BASE_SHA,
    )
    github.create_or_update_file.assert_awaited_once()
    github.create_pull_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_permit_is_rejected_before_any_write(
    github: AsyncMock,
    permit: PublishPermit,
    candidate: ContributionPublishCandidate,
) -> None:
    publisher = make_publisher(github)

    with pytest.raises(PublishPermitError, match="expired"):
        await publisher.publish(replace(permit, expires_at=NOW), candidate)

    github.fork_repository.assert_not_awaited()
    github.create_pull_request.assert_not_awaited()


@pytest.mark.parametrize(
    ("permit_change", "candidate_change", "message"),
    [
        ({"repo": "acme/other"}, {}, "repo"),
        ({"base_sha": "d" * 40}, {}, "base SHA"),
        ({"patch_sha256": "e" * 64}, {}, "patch hash"),
    ],
)
@pytest.mark.asyncio
async def test_permit_must_match_candidate_fingerprint(
    github: AsyncMock,
    permit: PublishPermit,
    candidate: ContributionPublishCandidate,
    permit_change: dict[str, str],
    candidate_change: dict[str, str],
    message: str,
) -> None:
    publisher = make_publisher(github)

    with pytest.raises(PublishPermitError, match=message):
        await publisher.publish(
            replace(permit, **permit_change),
            replace(candidate, **candidate_change),
        )

    github.fork_repository.assert_not_awaited()
    github.create_pull_request.assert_not_awaited()


@pytest.mark.parametrize(
    "field",
    [
        "work_id",
        "repo",
        "base_sha",
        "patch_sha256",
        "verification_id",
        "review_id",
        "quota_reservation_id",
    ],
)
@pytest.mark.asyncio
async def test_permit_requires_every_binding_and_proof_id(
    github: AsyncMock,
    permit: PublishPermit,
    candidate: ContributionPublishCandidate,
    field: str,
) -> None:
    publisher = make_publisher(github)

    with pytest.raises(PublishPermitError, match=field.replace("_", " ")):
        await publisher.publish(replace(permit, **{field: "   "}), candidate)

    github.fork_repository.assert_not_awaited()
    github.create_pull_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_policy_denial_blocks_publish_before_any_write(
    github: AsyncMock,
    permit: PublishPermit,
    candidate: ContributionPublishCandidate,
) -> None:
    publisher = make_publisher(github, PolicyEngine())

    with pytest.raises(PublishPolicyError, match=r"github\.push"):
        await publisher.publish(permit, candidate)

    github.fork_repository.assert_not_awaited()
    github.create_pull_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_duplicate_publish_reuses_first_result_and_creates_one_pr(
    github: AsyncMock,
    permit: PublishPermit,
    candidate: ContributionPublishCandidate,
) -> None:
    create_pr_started = asyncio.Event()
    release_create_pr = asyncio.Event()

    async def create_pull_request(*args, **kwargs):
        create_pr_started.set()
        await release_create_pr.wait()
        return {"number": 73, "html_url": "https://github.com/acme/widgets/pull/73"}

    github.create_pull_request.side_effect = create_pull_request
    publisher = make_publisher(github)

    first = asyncio.create_task(publisher.publish(permit, candidate))
    await create_pr_started.wait()
    duplicate = asyncio.create_task(publisher.publish(permit, candidate))
    await asyncio.sleep(0)
    release_create_pr.set()

    first_result, duplicate_result = await asyncio.gather(first, duplicate)

    assert duplicate_result is first_result
    assert github.fork_repository.await_count == 1
    assert github.create_branch.await_count == 1
    assert github.create_or_update_file.await_count == 1
    assert github.create_pull_request.await_count == 1
