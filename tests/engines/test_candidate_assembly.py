"""Candidate assembly tests."""

from __future__ import annotations

from contribai.engines.candidates import CandidateSet, PatchCandidate


def _candidate(attempt_id: str, patch: str = "patch") -> PatchCandidate:
    return PatchCandidate.from_patch(
        attempt_id=attempt_id,
        base_sha="base-1",
        patch=patch,
        changed_files=("src/service.py",),
    )


def test_candidate_set_preserves_independent_attempts_and_deduplicates_hashes() -> None:
    first = _candidate("attempt-a")
    duplicate = _candidate("attempt-b")
    second = _candidate("attempt-c", "different patch")

    candidates = CandidateSet.from_candidates((first, duplicate, second))

    assert len(candidates) == 2
    assert candidates.best is first
    assert candidates.attempt_ids == ("attempt-a", "attempt-c")
    assert candidates.patch_hashes == tuple(item.patch_sha256 for item in candidates)


def test_candidate_hash_is_stable_for_same_base_patch_and_files() -> None:
    first = _candidate("attempt-a")
    second = _candidate("attempt-b")

    assert first.patch_sha256 == second.patch_sha256
    assert first.candidate_id == second.candidate_id
