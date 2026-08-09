"""Tests for permit-gated, idempotent GitHub publishing."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from contribai.core.models import (
    Contribution,
    ContributionType,
    FileChange,
    Finding,
    Repository,
    Severity,
)
from contribai.github.client import GitHubClient, GitHubWriteAuthority
from contribai.github.guidelines import RepoGuidelines
from contribai.publishing import permit as permit_module
from contribai.publishing.capability import GITHUB_PUBLISHER_ACTOR, Capability
from contribai.publishing.github_publisher import GitHubPublisher, PublishPolicyError
from contribai.publishing.idempotency import InMemoryIdempotencyStore
from contribai.publishing.permit import (
    ContributionPublishCandidate,
    PublishPermit,
    PublishPermitError,
    PublishSideEffect,
)
from contribai.publishing.policy import CapabilityPolicy, PolicyDecision, PolicyEngine, PolicyRule

NOW = datetime(2026, 8, 7, 12, tzinfo=UTC)
BASE_SHA = "b" * 40


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
    )


@pytest.fixture
def permit(candidate: ContributionPublishCandidate) -> PublishPermit:
    return PublishPermit(
        work_id="work-123",
        repo=candidate.repo,
        base_sha=candidate.base_sha,
        patch_sha256=candidate.patch_sha256,
        publish_sha256=candidate.publish_sha256,
        verification_id="verification-123",
        review_id="review-123",
        approved_side_effects=frozenset({PublishSideEffect.CREATE_PR}),
        quota_reservation_id="quota-123",
        expires_at=NOW + timedelta(minutes=5),
    )


@pytest.fixture
def github(target_repo: Repository) -> AsyncMock:
    client = AsyncMock(spec=GitHubClient)
    client._GitHubClient__github_write_authority = None
    client._issue_write_authority.side_effect = lambda publisher: (
        GitHubClient._issue_write_authority(client, publisher)
    )
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


def make_publisher(
    github: AsyncMock,
    policy: PolicyEngine | None = None,
    *,
    clock=None,
) -> GitHubPublisher:
    return GitHubPublisher(
        github=github,
        policy_engine=policy or policy_engine(Capability.GITHUB_PUSH, Capability.GITHUB_CREATE_PR),
        idempotency_store=InMemoryIdempotencyStore(),
        clock=clock or (lambda: NOW),
    )


def test_candidate_computes_hash_from_exact_ordered_file_payload(
    contribution: Contribution,
    target_repo: Repository,
) -> None:
    test_change = FileChange(
        path="tests/test_main.py",
        original_content=None,
        new_content="def test_dead_code_removed():\n    assert True\n",
        is_new_file=True,
        is_deleted=False,
    )
    contribution.tests_added.append(test_change)

    candidate = ContributionPublishCandidate(
        contribution=contribution,
        target_repo=target_repo,
        base_sha=BASE_SHA,
    )
    payload = {
        "changes": [
            {
                "path": "src/main.py",
                "original_content": "if False:\n    pass\n",
                "new_content": "",
                "is_new_file": False,
                "is_deleted": False,
            }
        ],
        "tests_added": [
            {
                "path": "tests/test_main.py",
                "original_content": None,
                "new_content": "def test_dead_code_removed():\n    assert True\n",
                "is_new_file": True,
                "is_deleted": False,
            }
        ],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()

    assert candidate.patch_sha256 == hashlib.sha256(encoded).hexdigest()


def test_candidate_hash_binds_change_order_category_and_delete_flag(
    contribution: Contribution,
    target_repo: Repository,
) -> None:
    second = FileChange(
        path="src/obsolete.py",
        original_content="obsolete = True\n",
        new_content="",
        is_deleted=False,
    )
    contribution.changes.append(second)

    ordered = ContributionPublishCandidate(contribution, target_repo, BASE_SHA)

    reversed_changes = contribution.model_copy(deep=True)
    reversed_changes.changes.reverse()
    reversed_candidate = ContributionPublishCandidate(
        reversed_changes,
        target_repo,
        BASE_SHA,
    )

    recategorized = contribution.model_copy(deep=True)
    recategorized.tests_added = [recategorized.changes.pop()]
    recategorized_candidate = ContributionPublishCandidate(
        recategorized,
        target_repo,
        BASE_SHA,
    )

    deleted = contribution.model_copy(deep=True)
    deleted.changes[1].is_deleted = True
    deleted_candidate = ContributionPublishCandidate(deleted, target_repo, BASE_SHA)

    assert (
        len(
            {
                ordered.patch_sha256,
                reversed_candidate.patch_sha256,
                recategorized_candidate.patch_sha256,
                deleted_candidate.patch_sha256,
            }
        )
        == 4
    )


def test_candidate_publish_hash_binds_all_github_side_effect_content(
    contribution: Contribution,
    target_repo: Repository,
) -> None:
    original = ContributionPublishCandidate(
        contribution,
        target_repo,
        BASE_SHA,
        guidelines=RepoGuidelines(pr_template="## Summary\n"),
        closes_issue=17,
    )
    variants: list[ContributionPublishCandidate] = []

    for field_name, value in (
        ("title", "fix: unreviewed title"),
        ("description", "Unreviewed PR description"),
        ("commit_message", "fix: unreviewed commit"),
        ("branch_name", "fix/unreviewed-branch"),
    ):
        changed = contribution.model_copy(deep=True)
        setattr(changed, field_name, value)
        variants.append(
            ContributionPublishCandidate(
                changed,
                target_repo,
                BASE_SHA,
                guidelines=RepoGuidelines(pr_template="## Summary\n"),
                closes_issue=17,
            )
        )

    changed_finding = contribution.model_copy(deep=True)
    changed_finding.finding.description = "Unreviewed linked issue body"
    variants.extend(
        [
            ContributionPublishCandidate(
                changed_finding,
                target_repo,
                BASE_SHA,
                guidelines=RepoGuidelines(pr_template="## Summary\n"),
                closes_issue=17,
            ),
            ContributionPublishCandidate(
                contribution,
                target_repo,
                BASE_SHA,
                guidelines=RepoGuidelines(pr_template="## Unreviewed template\n"),
                closes_issue=17,
            ),
            ContributionPublishCandidate(
                contribution,
                target_repo,
                BASE_SHA,
                guidelines=RepoGuidelines(pr_template="## Summary\n"),
                closes_issue=18,
            ),
        ]
    )

    assert all(variant.patch_sha256 == original.patch_sha256 for variant in variants)
    assert all(variant.publish_sha256 != original.publish_sha256 for variant in variants)


def test_candidate_does_not_accept_a_caller_asserted_patch_hash(
    contribution: Contribution,
    target_repo: Repository,
) -> None:
    with pytest.raises(TypeError, match="patch_sha256"):
        ContributionPublishCandidate(
            contribution=contribution,
            target_repo=target_repo,
            base_sha=BASE_SHA,
            patch_sha256="caller-asserted",  # type: ignore[call-arg]
        )

    with pytest.raises(TypeError):
        ContributionPublishCandidate(
            contribution,
            target_repo,
            BASE_SHA,
            "legacy-positional-caller-hash",
        )


def test_candidate_snapshots_mutable_contribution_and_returns_defensive_copies(
    contribution: Contribution,
    target_repo: Repository,
) -> None:
    candidate = ContributionPublishCandidate(
        contribution=contribution,
        target_repo=target_repo,
        base_sha=BASE_SHA,
    )
    bound_hash = candidate.patch_sha256

    contribution.changes[0].new_content = "tampered original"
    exposed_copy = candidate.contribution
    exposed_copy.changes[0].new_content = "tampered returned copy"

    assert candidate.patch_sha256 == bound_hash
    assert candidate.contribution.changes[0].new_content == ""


def test_candidate_rejects_inconsistent_repository_identity(
    contribution: Contribution,
    target_repo: Repository,
) -> None:
    inconsistent_repo = target_repo.model_copy(update={"full_name": "mallory/widgets"})

    with pytest.raises(ValueError, match="full_name"):
        ContributionPublishCandidate(
            contribution=contribution,
            target_repo=inconsistent_repo,
            base_sha=BASE_SHA,
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
        authority=ANY,
    )
    github.create_or_update_file.assert_awaited_once()
    github.create_pull_request.assert_awaited_once()
    for write in (
        github.fork_repository,
        github.create_branch,
        github.create_or_update_file,
        github.create_pull_request,
    ):
        assert isinstance(write.await_args.kwargs["authority"], GitHubWriteAuthority)


@pytest.mark.asyncio
async def test_permit_rejects_candidate_with_unreviewed_publish_metadata(
    github: AsyncMock,
    permit: PublishPermit,
    candidate: ContributionPublishCandidate,
) -> None:
    changed = candidate.contribution
    changed.title = "fix: unreviewed title"
    unreviewed = ContributionPublishCandidate(
        changed,
        candidate.target_repo,
        candidate.base_sha,
        guidelines=candidate.guidelines,
        closes_issue=candidate.closes_issue,
    )
    assert unreviewed.patch_sha256 == candidate.patch_sha256
    assert unreviewed.publish_sha256 != candidate.publish_sha256

    with pytest.raises(PublishPermitError, match="publish hash"):
        await make_publisher(github).publish(permit, unreviewed)


@pytest.mark.asyncio
async def test_mutating_source_after_permit_creation_cannot_change_published_payload(
    github: AsyncMock,
    contribution: Contribution,
    permit: PublishPermit,
    candidate: ContributionPublishCandidate,
) -> None:
    contribution.changes[0].new_content = "tampered after permit"
    candidate.contribution.changes[0].new_content = "tampered defensive copy"

    await make_publisher(github).publish(permit, candidate)

    assert github.create_or_update_file.await_args.args[3] == ""
    assert candidate.patch_sha256 == permit.patch_sha256


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
    ("permit_change", "message"),
    [
        ({"repo": "acme/other"}, "repo"),
        ({"base_sha": "d" * 40}, "base SHA"),
        ({"patch_sha256": "e" * 64}, "patch hash"),
    ],
)
@pytest.mark.asyncio
async def test_permit_must_match_candidate_fingerprint(
    github: AsyncMock,
    permit: PublishPermit,
    candidate: ContributionPublishCandidate,
    permit_change: dict[str, str],
    message: str,
) -> None:
    publisher = make_publisher(github)

    with pytest.raises(PublishPermitError, match=message):
        await publisher.publish(replace(permit, **permit_change), candidate)

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
async def test_policy_is_revalidated_before_each_mutation(
    github: AsyncMock,
    permit: PublishPermit,
    candidate: ContributionPublishCandidate,
) -> None:
    class RevokingPolicyEngine:
        def __init__(self) -> None:
            self.push_evaluations = 0

        def evaluate(self, request) -> PolicyDecision:
            if request.capability is Capability.GITHUB_PUSH:
                self.push_evaluations += 1
                if self.push_evaluations >= 3:
                    return PolicyDecision.DENY
            return PolicyDecision.ALLOW

    policy = RevokingPolicyEngine()
    publisher = make_publisher(github, policy)  # type: ignore[arg-type]

    with pytest.raises(PublishPolicyError, match=r"github\.push"):
        await publisher.publish(permit, candidate)

    assert policy.push_evaluations == 3
    github.fork_repository.assert_awaited_once()
    github.create_branch.assert_not_awaited()
    github.create_or_update_file.assert_not_awaited()
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


@pytest.mark.asyncio
async def test_permit_expiring_during_pre_write_reads_blocks_first_mutation(
    github: AsyncMock,
    permit: PublishPermit,
    candidate: ContributionPublishCandidate,
) -> None:
    clock_values = iter([NOW, permit.expires_at])
    publisher = make_publisher(github, clock=lambda: next(clock_values))

    with pytest.raises(PublishPermitError, match="expired"):
        await publisher.publish(permit, candidate)

    github.get_authenticated_user.assert_awaited_once()
    github.get_repo_details.assert_awaited_once()
    github.fork_repository.assert_not_awaited()
    github.create_branch.assert_not_awaited()
    github.create_or_update_file.assert_not_awaited()
    github.create_pull_request.assert_not_awaited()


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("repo", "acme/other", "repo"),
        ("base_sha", "d" * 40, "base SHA"),
        ("patch_sha256", "e" * 64, "patch hash"),
        ("publish_sha256", "f" * 64, "publish hash"),
    ],
)
@pytest.mark.asyncio
async def test_candidate_bindings_are_revalidated_after_pre_write_reads(
    github: AsyncMock,
    permit: PublishPermit,
    candidate: ContributionPublishCandidate,
    field: str,
    replacement: str,
    message: str,
) -> None:
    mutable_candidate = SimpleNamespace(
        contribution=candidate.contribution,
        target_repo=candidate.target_repo,
        base_sha=candidate.base_sha,
        patch_sha256=candidate.patch_sha256,
        publish_sha256=candidate.publish_sha256,
        repo=candidate.repo,
        guidelines=candidate.guidelines,
        closes_issue=candidate.closes_issue,
    )

    async def mutate_binding_during_read(*args, **kwargs):
        setattr(mutable_candidate, field, replacement)
        raise RuntimeError("fork does not exist")

    github.get_repo_details.side_effect = mutate_binding_during_read
    publisher = make_publisher(github)

    with pytest.raises(PublishPermitError, match=message):
        await publisher.publish(permit, mutable_candidate)

    github.fork_repository.assert_not_awaited()
    github.create_branch.assert_not_awaited()
    github.create_or_update_file.assert_not_awaited()
    github.create_pull_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelling_one_duplicate_waiter_does_not_cancel_shared_publish(
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

    cancelled_waiter = asyncio.create_task(publisher.publish(permit, candidate))
    await create_pr_started.wait()
    surviving_waiter = asyncio.create_task(publisher.publish(permit, candidate))
    await asyncio.sleep(0)

    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter

    release_create_pr.set()
    result = await surviving_waiter

    assert result.pr_number == 73
    assert github.fork_repository.await_count == 1
    assert github.create_branch.await_count == 1
    assert github.create_or_update_file.await_count == 1
    assert github.create_pull_request.await_count == 1


def _permit_with_side_effects(
    permit: PublishPermit,
    *side_effect_names: str,
) -> PublishPermit:
    side_effect_type = getattr(permit_module, "PublishSideEffect", None)
    assert side_effect_type is not None, "publishing layer must define canonical side effects"
    return replace(
        permit,
        approved_side_effects=frozenset(
            getattr(side_effect_type, side_effect_name) for side_effect_name in side_effect_names
        ),
    )


@pytest.mark.asyncio
async def test_publish_requires_explicit_create_pr_review_approval_before_reads(
    github: AsyncMock,
    permit: PublishPermit,
    candidate: ContributionPublishCandidate,
) -> None:
    permit_without_pr_approval = _permit_with_side_effects(permit)

    with pytest.raises(PublishPermitError, match="create_pr"):
        await make_publisher(github).publish(permit_without_pr_approval, candidate)

    github.get_authenticated_user.assert_not_awaited()
    github.fork_repository.assert_not_awaited()
    github.create_pull_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_generic_review_id_cannot_approve_unreviewed_issue_creation(
    github: AsyncMock,
    permit: PublishPermit,
    contribution: Contribution,
    target_repo: Repository,
) -> None:
    candidate = ContributionPublishCandidate(
        contribution,
        target_repo,
        BASE_SHA,
        guidelines=RepoGuidelines(requires_issue_link=True),
    )
    pr_only_permit = replace(
        _permit_with_side_effects(permit, "CREATE_PR"),
        publish_sha256=candidate.publish_sha256,
    )
    publisher = make_publisher(
        github,
        policy_engine(
            Capability.GITHUB_PUSH,
            Capability.GITHUB_CREATE_PR,
            Capability.GITHUB_CREATE_ISSUE,
        ),
    )

    with pytest.raises(PublishPermitError, match="create_issue"):
        await publisher.publish(pr_only_permit, candidate)

    github.get_authenticated_user.assert_not_awaited()
    github.fork_repository.assert_not_awaited()
    github.create_issue.assert_not_awaited()
    github.create_pull_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_issue_and_pr_review_approvals_allow_issue_then_pr(
    github: AsyncMock,
    permit: PublishPermit,
    contribution: Contribution,
    target_repo: Repository,
) -> None:
    candidate = ContributionPublishCandidate(
        contribution,
        target_repo,
        BASE_SHA,
        guidelines=RepoGuidelines(requires_issue_link=True),
    )
    approved_permit = replace(
        _permit_with_side_effects(permit, "CREATE_PR", "CREATE_ISSUE"),
        publish_sha256=candidate.publish_sha256,
    )
    github.create_issue.return_value = {"number": 41}
    publisher = make_publisher(
        github,
        policy_engine(
            Capability.GITHUB_PUSH,
            Capability.GITHUB_CREATE_PR,
            Capability.GITHUB_CREATE_ISSUE,
        ),
    )

    result = await publisher.publish(approved_permit, candidate)

    assert result.pr_number == 73
    github.create_issue.assert_awaited_once()
    github.create_pull_request.assert_awaited_once()
