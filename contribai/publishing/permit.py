"""Permit and candidate contracts for the GitHub publishing boundary."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Protocol

from contribai.core.models import Contribution, Repository


class PublishPermitError(ValueError):
    """Raised when a publish permit is incomplete, expired, or mismatched."""


class PublishCandidateError(ValueError):
    """Raised when a candidate cannot be canonically bound for publishing."""


class PublishSideEffect(StrEnum):
    """Canonical reviewed side effects that a publish permit may authorize."""

    CREATE_PR = "create_pr"
    CREATE_ISSUE = "create_issue"


@dataclass(frozen=True)
class PublishPermit:
    """Proof bindings required before any GitHub publish side effect."""

    work_id: str
    repo: str
    base_sha: str
    patch_sha256: str
    verification_id: str
    review_id: str
    approved_side_effects: frozenset[PublishSideEffect]
    quota_reservation_id: str
    expires_at: datetime


class PublishCandidate(Protocol):
    """Minimal payload consumed by the publishing layer."""

    @property
    def contribution(self) -> Contribution: ...

    @property
    def target_repo(self) -> Repository: ...

    @property
    def base_sha(self) -> str: ...

    @property
    def patch_sha256(self) -> str: ...

    @property
    def guidelines(self) -> Any | None: ...

    @property
    def closes_issue(self) -> int | None: ...

    @property
    def repo(self) -> str: ...


@dataclass(frozen=True, slots=True, init=False)
class ContributionPublishCandidate:
    """Immutable adapter from today's Contribution to the publishing protocol."""

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
        if not base_sha or not base_sha.strip():
            raise PublishCandidateError("base_sha must be non-empty")
        if closes_issue is not None and (
            isinstance(closes_issue, bool) or not isinstance(closes_issue, int) or closes_issue <= 0
        ):
            raise PublishCandidateError("closes_issue must be a positive integer")

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

        all_changes = [*contribution_snapshot.changes, *contribution_snapshot.tests_added]
        seen_paths: set[str] = set()
        for change in all_changes:
            path = str(change.path).replace("\\", "/")
            pure = PurePosixPath(path)
            if not path or pure.is_absolute() or ".." in pure.parts:
                raise PublishCandidateError(f"file change path is outside the repository: {path}")
            if path in seen_paths:
                raise PublishCandidateError(f"duplicate file change path: {path}")
            seen_paths.add(path)
            if change.is_new_file and change.is_deleted:
                raise PublishCandidateError(f"file cannot be both new and deleted: {path}")

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

        object.__setattr__(self, "_contribution_json", contribution_snapshot.model_dump_json())
        object.__setattr__(self, "_target_repo_json", target_repo_snapshot.model_dump_json())
        object.__setattr__(self, "_base_sha", base_sha.strip())
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
        return Contribution.model_validate_json(self._contribution_json)

    @property
    def target_repo(self) -> Repository:
        return Repository.model_validate_json(self._target_repo_json)

    @property
    def base_sha(self) -> str:
        return self._base_sha

    @property
    def patch_sha256(self) -> str:
        return self._patch_sha256

    @property
    def guidelines(self) -> Any | None:
        return copy.deepcopy(self._guidelines)

    @property
    def closes_issue(self) -> int | None:
        return self._closes_issue

    @property
    def repo(self) -> str:
        return self._repo
