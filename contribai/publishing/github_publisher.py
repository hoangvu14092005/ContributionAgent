"""The sole authority allowed to perform GitHub publishing writes."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from contribai.core.exceptions import PRCreationError
from contribai.core.models import Contribution, ContributionType, PRResult, PRStatus, Repository
from contribai.github.client import GitHubClient, _issue_github_write_authority
from contribai.github.guidelines import adapt_pr_body
from contribai.pr.manager import PRManager
from contribai.publishing.capability import GITHUB_PUBLISHER_ACTOR, Capability, CapabilityRequest
from contribai.publishing.idempotency import (
    IdempotencyKey,
    IdempotencyStore,
    InMemoryIdempotencyStore,
)
from contribai.publishing.permit import (
    PublishCandidate,
    PublishPermit,
    PublishPermitError,
    PublishSideEffect,
)
from contribai.publishing.policy import PolicyDecision, PolicyEngine

logger = logging.getLogger(__name__)


class PublishPolicyError(PermissionError):
    """Raised when policy does not explicitly allow a required GitHub write."""


class GitHubPublisher:
    """Validate proof bindings and publish a candidate at most once."""

    def __init__(
        self,
        github: GitHubClient,
        policy_engine: PolicyEngine,
        *,
        idempotency_store: IdempotencyStore | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._github = github
        self._policy_engine = policy_engine
        self._idempotency_store = idempotency_store or InMemoryIdempotencyStore()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._pr_manager = PRManager(github)
        self.__write_authority = _issue_github_write_authority(github, self)

    async def publish(self, permit: PublishPermit, candidate: PublishCandidate) -> PRResult:
        """Publish a candidate only after validating permit, policy, and identity."""
        self._validate_permit(permit, candidate)
        self._authorize_publish(permit, candidate)
        key = IdempotencyKey(
            work_id=permit.work_id,
            repo=candidate.repo,
            base_sha=candidate.base_sha,
            patch_sha256=candidate.patch_sha256,
        )
        return await self._idempotency_store.execute(
            key,
            lambda: self._publish_once(permit, candidate),
        )

    def _validate_permit(self, permit: PublishPermit, candidate: PublishCandidate) -> None:
        required_fields = {
            "work id": permit.work_id,
            "repo": permit.repo,
            "base sha": permit.base_sha,
            "patch sha256": permit.patch_sha256,
            "verification id": permit.verification_id,
            "review id": permit.review_id,
            "quota reservation id": permit.quota_reservation_id,
        }
        for field_name, value in required_fields.items():
            if not value or not value.strip():
                raise PublishPermitError(f"Publish permit requires non-empty {field_name}")

        if permit.expires_at.tzinfo is None or permit.expires_at.utcoffset() is None:
            raise PublishPermitError("Publish permit expiry must be timezone-aware")
        if permit.expires_at <= self._clock():
            raise PublishPermitError("Publish permit has expired")
        if permit.repo != candidate.repo:
            raise PublishPermitError("Publish permit repo does not match candidate repo")
        if permit.base_sha != candidate.base_sha:
            raise PublishPermitError("Publish permit base SHA does not match candidate base SHA")
        if permit.patch_sha256 != candidate.patch_sha256:
            raise PublishPermitError(
                "Publish permit patch hash does not match candidate patch hash"
            )

        if not isinstance(permit.approved_side_effects, frozenset):
            raise PublishPermitError("Publish permit approved side effects must be a frozenset")
        if any(
            not isinstance(side_effect, PublishSideEffect)
            for side_effect in permit.approved_side_effects
        ):
            raise PublishPermitError("Publish permit contains an unknown approved side effect")

        required_side_effects = {PublishSideEffect.CREATE_PR}
        if candidate.closes_issue is None and self._requires_linked_issue(candidate):
            required_side_effects.add(PublishSideEffect.CREATE_ISSUE)
        missing_side_effects = required_side_effects - permit.approved_side_effects
        if missing_side_effects:
            missing = ", ".join(sorted(side_effect.value for side_effect in missing_side_effects))
            raise PublishPermitError(
                f"Publish permit review did not approve required side effects: {missing}"
            )

    def _authorize_publish(self, permit: PublishPermit, candidate: PublishCandidate) -> None:
        capabilities = [Capability.GITHUB_PUSH, Capability.GITHUB_CREATE_PR]
        if candidate.closes_issue is None and self._requires_linked_issue(candidate):
            capabilities.append(Capability.GITHUB_CREATE_ISSUE)

        resource = f"repositories/{permit.repo}"
        for capability in capabilities:
            self._authorize_capability(permit, capability, resource=resource)

    def _authorize_capability(
        self,
        permit: PublishPermit,
        capability: Capability,
        *,
        resource: str | None = None,
    ) -> None:
        request = CapabilityRequest(
            actor=GITHUB_PUBLISHER_ACTOR,
            capability=capability,
            resource=resource or f"repositories/{permit.repo}",
            work_id=permit.work_id,
        )
        decision = self._policy_engine.evaluate(request)
        if decision is not PolicyDecision.ALLOW:
            raise PublishPolicyError(
                f"Policy decision {decision.value!r} blocks {capability.value} for {permit.repo}"
            )

    def _validate_write(
        self,
        permit: PublishPermit,
        candidate: PublishCandidate,
        capability: Capability,
    ) -> None:
        """Revalidate all permit bindings and policy immediately before a write."""
        self._validate_permit(permit, candidate)
        self._authorize_capability(permit, capability)

    async def _publish_once(
        self,
        permit: PublishPermit,
        candidate: PublishCandidate,
    ) -> PRResult:
        contribution = candidate.contribution
        target_repo = candidate.target_repo
        user = await self._github.get_authenticated_user()
        username = user["login"]
        signoff = self._pr_manager._build_signoff(user)

        try:
            fork = await self._fork_if_needed(username, target_repo, permit, candidate)
            branch = contribution.branch_name or self._pr_manager._human_branch_name(contribution)
            self._validate_write(permit, candidate, Capability.GITHUB_PUSH)
            await self._github.create_branch(
                fork.owner,
                fork.name,
                branch,
                base_sha=candidate.base_sha,
                authority=self.__write_authority,
            )

            for change in contribution.changes + contribution.tests_added:
                sha = None
                if not change.is_new_file:
                    try:
                        _, sha = await self._github.get_file_content_with_sha(
                            fork.owner,
                            fork.name,
                            change.path,
                            ref=branch,
                        )
                    except Exception:
                        sha = None

                self._validate_write(permit, candidate, Capability.GITHUB_PUSH)
                await self._github.create_or_update_file(
                    fork.owner,
                    fork.name,
                    change.path,
                    change.new_content,
                    contribution.commit_message,
                    branch,
                    authority=self.__write_authority,
                    sha=sha,
                    signoff=signoff,
                )

            issue_number = candidate.closes_issue
            if issue_number is None and self._requires_linked_issue(candidate):
                issue_number = await self._create_issue_for_finding(
                    permit,
                    candidate,
                    contribution,
                    target_repo,
                )

            pr_body = self._build_pr_body(candidate, issue_number)
            self._validate_write(permit, candidate, Capability.GITHUB_CREATE_PR)
            pr_data = await self._github.create_pull_request(
                target_repo.owner,
                target_repo.name,
                title=contribution.title,
                body=pr_body,
                head=f"{fork.owner}:{branch}",
                authority=self.__write_authority,
                base=target_repo.default_branch,
            )
            result = PRResult(
                repo=target_repo,
                contribution=contribution,
                pr_number=pr_data["number"],
                pr_url=pr_data["html_url"],
                status=PRStatus.OPEN,
                branch_name=branch,
                fork_full_name=fork.full_name,
            )
            logger.info("PR #%d created through publisher: %s", result.pr_number, result.pr_url)
            return result
        except (PublishPermitError, PublishPolicyError):
            raise
        except Exception as exc:
            if isinstance(exc, PRCreationError):
                raise
            error_message = str(exc).lower()
            if "422" in error_message and "collaborat" in error_message:
                raise PRCreationError(
                    f"{target_repo.full_name} restricts PRs to collaborators only. "
                    "Skipping — this repo cannot accept external contributions via fork."
                ) from exc
            raise PRCreationError(f"Failed to create PR: {exc}") from exc

    async def _fork_if_needed(
        self,
        username: str,
        repo: Repository,
        permit: PublishPermit,
        candidate: PublishCandidate,
    ) -> Repository:
        try:
            existing = await self._github.get_repo_details(username, repo.name)
            if existing.owner == username:
                logger.info("Fork already exists: %s/%s", username, repo.name)
                return existing
        except Exception:
            pass
        self._validate_write(permit, candidate, Capability.GITHUB_PUSH)
        return await self._github.fork_repository(
            repo.owner,
            repo.name,
            authority=self.__write_authority,
        )

    @staticmethod
    def _requires_linked_issue(candidate: PublishCandidate) -> bool:
        return bool(candidate.guidelines and candidate.guidelines.requires_issue_link)

    def _build_pr_body(self, candidate: PublishCandidate, issue_number: int | None) -> str:
        if candidate.guidelines and candidate.guidelines.has_guidelines:
            body = adapt_pr_body(candidate.contribution, candidate.guidelines)
        else:
            body = self._pr_manager._generate_pr_body(candidate.contribution)

        if issue_number:
            body = body.replace("Closes N/A", f"Closes #{issue_number}").replace(
                "Closes #\n", f"Closes #{issue_number}\n"
            )
            if f"#{issue_number}" not in body:
                body += f"\n\nCloses #{issue_number}"
        return body

    async def _create_issue_for_finding(
        self,
        permit: PublishPermit,
        candidate: PublishCandidate,
        contribution: Contribution,
        target_repo: Repository,
    ) -> int | None:
        finding = contribution.finding
        label_by_type = {
            ContributionType.SECURITY_FIX: "bug",
            ContributionType.CODE_QUALITY: "bug",
            ContributionType.DOCS_IMPROVE: "documentation",
            ContributionType.UI_UX_FIX: "bug",
            ContributionType.PERFORMANCE_OPT: "perf",
            ContributionType.FEATURE_ADD: "enhancement",
            ContributionType.REFACTOR: "enhancement",
        }
        prefix_by_type = {
            ContributionType.SECURITY_FIX: "fix",
            ContributionType.CODE_QUALITY: "fix",
            ContributionType.DOCS_IMPROVE: "docs",
            ContributionType.UI_UX_FIX: "fix",
            ContributionType.PERFORMANCE_OPT: "perf",
            ContributionType.FEATURE_ADD: "feat",
            ContributionType.REFACTOR: "refactor",
        }
        scope = ""
        if finding.file_path:
            parts = finding.file_path.split("/")
            if len(parts) >= 2 and parts[0] in ("packages", "apps", "libs", "src"):
                scope = parts[1]

        prefix = prefix_by_type.get(finding.type, "fix")
        issue_title = (
            f"{prefix}({scope}): {finding.title.lower()}"
            if scope
            else f"{prefix}: {finding.title.lower()}"
        )
        issue_body = (
            f"## Description\n\n{finding.description}\n\n"
            f"**Severity**: `{finding.severity.value}`\n"
            f"**File**: `{finding.file_path}`\n\n"
            "## Expected Behavior\n\n"
            "The code should handle this case properly to avoid unexpected errors or "
            "degraded quality."
        )

        try:
            try:
                self._validate_write(permit, candidate, Capability.GITHUB_CREATE_ISSUE)
                data = await self._github.create_issue(
                    target_repo.owner,
                    target_repo.name,
                    title=issue_title,
                    body=issue_body,
                    authority=self.__write_authority,
                    labels=[label_by_type.get(finding.type, "bug")],
                )
            except (PublishPermitError, PublishPolicyError):
                raise
            except Exception:
                self._validate_write(permit, candidate, Capability.GITHUB_CREATE_ISSUE)
                data = await self._github.create_issue(
                    target_repo.owner,
                    target_repo.name,
                    title=issue_title,
                    body=issue_body,
                    authority=self.__write_authority,
                )
            return data["number"]
        except (PublishPermitError, PublishPolicyError):
            raise
        except Exception as exc:
            logger.warning("Failed to create issue: %s", exc)
            return None
