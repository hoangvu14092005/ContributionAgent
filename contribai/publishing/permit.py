"""Permit and candidate contracts for the GitHub publishing boundary."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from contribai.core.models import Contribution, Repository


class PublishPermitError(ValueError):
    """Raised when a publish permit is incomplete, expired, or mismatched."""


class PublishCandidateError(ValueError):
    """Raised when a candidate cannot be canonically bound for publishing."""


@dataclass(frozen=True)
class PublishPermit:
    """Proof bindings required before any GitHub publish side effect."""

    work_id: str
    repo: str
    base_sha: str
    patch_sha256: str
    verification_id: str
    review_id: str
    quota_reservation_id: str
    expires_at: datetime


class PublishCandidate(Protocol):
    """Minimal payload consumed by the publishing layer.

    Task 10C can adapt its future PatchCandidate to this protocol without making
    GitHubPublisher depend on engine or workspace internals.
    """

    @property
    def contribution(self) -> Contribution:
        """Return an isolated copy of the snapshotted contribution."""
        ...

    @property
    def target_repo(self) -> Repository:
        """Return an isolated copy of the canonically bound repository."""
        ...

    @property
    def base_sha(self) -> str:
        """Return the exact base commit bound to the candidate."""
        ...

    @property
    def patch_sha256(self) -> str:
        """Return the trusted hash derived from the ordered file-change payload."""
        ...

    @property
    def guidelines(self) -> Any | None:
        """Return isolated contribution guidelines, if any."""
        ...

    @property
    def closes_issue(self) -> int | None:
        """Return the already-linked issue number, if any."""
        ...

    @property
    def repo(self) -> str:
        """Return the canonical ``owner/name`` repository identity."""
        ...


@dataclass(frozen=True, slots=True, init=False)
class ContributionPublishCandidate:
    """Immutable adapter from today's Contribution to the publishing protocol.

    The adapter snapshots the current models and derives the fingerprint inside
    the publishing layer. Task 10C can implement the same PublishCandidate
    protocol from its future PatchCandidate without coupling this publisher to
    engine or workspace internals.
    """

    _contribution_json: str
    _target_repo_json: str
    _base_sha: str
    _patch_sha256: str
    _repo: str
    _guidelines: Any | None
    _closes_issue: int | None

    def __init__(
        self,
        contribution: Contribution,
        target_repo: Repository,
        base_sha: str,
        *,
        guidelines: Any | None = None,
        closes_issue: int | None = None,
    ) -> None:
        contribution_snapshot = contribution.model_copy(deep=True)
        target_repo_snapshot = target_repo.model_copy(deep=True)
        owner = target_repo_snapshot.owner
        name = target_repo_snapshot.name
        if not owner or not owner.strip() or not name or not name.strip():
            raise PublishCandidateError("target_repo owner and name must be non-empty")

        canonical_repo = f"{owner}/{name}"
        if target_repo_snapshot.full_name != canonical_repo:
            raise PublishCandidateError(
                "target_repo full_name must equal the canonical owner/name identity"
            )

        patch_payload = {
            "changes": [
                self._serialize_file_change(change) for change in contribution_snapshot.changes
            ],
            "tests_added": [
                self._serialize_file_change(change) for change in contribution_snapshot.tests_added
            ],
        }
        encoded_payload = json.dumps(
            patch_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

        object.__setattr__(
            self,
            "_contribution_json",
            contribution_snapshot.model_dump_json(),
        )
        object.__setattr__(
            self,
            "_target_repo_json",
            target_repo_snapshot.model_dump_json(),
        )
        object.__setattr__(self, "_base_sha", base_sha)
        object.__setattr__(self, "_patch_sha256", hashlib.sha256(encoded_payload).hexdigest())
        object.__setattr__(self, "_repo", canonical_repo)
        object.__setattr__(self, "_guidelines", copy.deepcopy(guidelines))
        object.__setattr__(self, "_closes_issue", closes_issue)

    @staticmethod
    def _serialize_file_change(change: Any) -> dict[str, Any]:
        """Return the exact ordered-file fingerprint fields."""
        return {
            "path": change.path,
            "original_content": change.original_content,
            "new_content": change.new_content,
            "is_new_file": change.is_new_file,
            "is_deleted": change.is_deleted,
        }

    @property
    def contribution(self) -> Contribution:
        """Return an isolated copy of the snapshotted contribution."""
        return Contribution.model_validate_json(self._contribution_json)

    @property
    def target_repo(self) -> Repository:
        """Return an isolated copy of the snapshotted target repository."""
        return Repository.model_validate_json(self._target_repo_json)

    @property
    def base_sha(self) -> str:
        """Return the exact base commit bound into the candidate."""
        return self._base_sha

    @property
    def patch_sha256(self) -> str:
        """Return SHA-256 of the canonical ordered file-change payload."""
        return self._patch_sha256

    @property
    def guidelines(self) -> Any | None:
        """Return an isolated copy of contribution guidelines."""
        return copy.deepcopy(self._guidelines)

    @property
    def closes_issue(self) -> int | None:
        """Return the already-linked issue number, if any."""
        return self._closes_issue

    @property
    def repo(self) -> str:
        """Return the target repository bound into the fingerprint."""
        return self._repo
