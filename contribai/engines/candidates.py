"""Control-plane patch candidate and N-best assembly models."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from contribai.execution.workspaces.base import compute_diff_hash


@dataclass(frozen=True, slots=True)
class PatchCandidate:
    """Patch evidence collected from one isolated engine attempt."""

    attempt_id: str
    base_sha: str
    patch: str
    changed_files: tuple[str, ...]
    added_files: tuple[str, ...] = ()
    deleted_files: tuple[str, ...] = ()
    patch_sha256: str = ""
    candidate_id: str = ""
    binary_files: tuple[str, ...] = ()
    unreadable_files: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.attempt_id.strip() or not self.base_sha.strip():
            raise ValueError("patch candidate requires attempt and base identifiers")
        if not self.patch:
            raise ValueError("patch candidate cannot be empty")
        changed = tuple(dict.fromkeys(sorted(self.changed_files)))
        added = tuple(dict.fromkeys(sorted(self.added_files)))
        deleted = tuple(dict.fromkeys(sorted(self.deleted_files)))
        object.__setattr__(self, "changed_files", changed)
        object.__setattr__(self, "added_files", added)
        object.__setattr__(self, "deleted_files", deleted)
        object.__setattr__(self, "binary_files", tuple(sorted(set(self.binary_files))))
        object.__setattr__(self, "unreadable_files", tuple(sorted(set(self.unreadable_files))))
        expected_hash = self.compute_hash(
            base_sha=self.base_sha,
            patch=self.patch,
            changed_files=changed,
            added_files=added,
            deleted_files=deleted,
        )
        if self.patch_sha256 and self.patch_sha256 != expected_hash:
            raise ValueError("patch candidate hash is not deterministic")
        object.__setattr__(self, "patch_sha256", expected_hash)
        object.__setattr__(self, "candidate_id", self.candidate_id or expected_hash)

    @classmethod
    def from_patch(
        cls,
        *,
        attempt_id: str,
        base_sha: str,
        patch: str,
        changed_files: Iterable[str],
        added_files: Iterable[str] = (),
        deleted_files: Iterable[str] = (),
        binary_files: Iterable[str] = (),
        unreadable_files: Iterable[str] = (),
    ) -> PatchCandidate:
        return cls(
            attempt_id=attempt_id,
            base_sha=base_sha,
            patch=patch,
            changed_files=tuple(changed_files),
            added_files=tuple(added_files),
            deleted_files=tuple(deleted_files),
            binary_files=tuple(binary_files),
            unreadable_files=tuple(unreadable_files),
        )

    @staticmethod
    def compute_hash(
        *,
        base_sha: str,
        patch: str,
        changed_files: Iterable[str],
        added_files: Iterable[str] = (),
        deleted_files: Iterable[str] = (),
    ) -> str:
        """Compute a canonical hash independent of attempt identity or ordering."""
        return compute_diff_hash(
            base_sha=base_sha,
            patch=patch,
            changed_files=changed_files,
            added_files=added_files,
            deleted_files=deleted_files,
        )


@dataclass(frozen=True, slots=True)
class CandidateSet:
    """N-best candidates from independent workspace attempts."""

    candidates: tuple[PatchCandidate, ...] = ()

    def __post_init__(self) -> None:
        unique: dict[str, PatchCandidate] = {}
        for candidate in self.candidates:
            unique.setdefault(candidate.patch_sha256, candidate)
        object.__setattr__(self, "candidates", tuple(unique.values()))

    @classmethod
    def from_candidates(cls, candidates: Iterable[PatchCandidate]) -> CandidateSet:
        return cls(tuple(candidates))

    def __iter__(self):
        return iter(self.candidates)

    def __len__(self) -> int:
        return len(self.candidates)

    @property
    def best(self) -> PatchCandidate | None:
        """Return the first control-plane candidate before verification ranking."""
        return self.candidates[0] if self.candidates else None

    @property
    def attempt_ids(self) -> tuple[str, ...]:
        return tuple(candidate.attempt_id for candidate in self.candidates)

    @property
    def patch_hashes(self) -> tuple[str, ...]:
        return tuple(candidate.patch_sha256 for candidate in self.candidates)


__all__ = ["CandidateSet", "PatchCandidate"]
