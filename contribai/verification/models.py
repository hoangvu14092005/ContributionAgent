"""Immutable verification evidence and report contracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class VerificationStatus(StrEnum):
    """Verification result states used by PublishGate."""

    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


ValidatorStatus = Literal["VERIFIED", "UNVERIFIED", "INCONCLUSIVE"]


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    """Bounded proof for one verification check."""

    check: str
    passed: bool
    status: ValidatorStatus
    command: str = ""
    exit_code: int | None = None
    output: str = ""
    duration_sec: float = 0.0
    recoverable: bool = False

    def __post_init__(self) -> None:
        if not self.check.strip():
            raise ValueError("verification evidence check is required")
        if self.duration_sec < 0:
            raise ValueError("verification evidence duration must be non-negative")
        object.__setattr__(self, "output", _redact(self.output)[:8_000])
        object.__setattr__(self, "command", _redact(self.command)[:2_000])


@dataclass(frozen=True, slots=True)
class FailureContext:
    """Actionable failure details for a recoverable repair retry."""

    command: str
    exit_code: int | None
    traceback: str = ""
    changed_symbol: str | None = None
    surrounding_lines: str = ""
    recoverable: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "command", _redact(self.command)[:2_000])
        object.__setattr__(self, "traceback", _redact(self.traceback)[:8_000])
        object.__setattr__(self, "surrounding_lines", _redact(self.surrounding_lines)[:8_000])


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """Full evidence report for one candidate attempt."""

    status: VerificationStatus
    baseline_passed: bool
    syntax_passed: bool
    tests_passed: bool
    lint_passed: bool
    typecheck_passed: bool
    security_passed: bool
    quality_score: float
    tests_run: int
    tests_failed: int
    evidence: tuple[VerificationEvidence, ...]
    candidate_hash: str = ""
    failure_context: FailureContext | None = None
    regression_safety_score: float = 0.0
    minimality_score: float = 0.0
    cost_usd: float = 0.0

    def __post_init__(self) -> None:
        status = VerificationStatus(self.status)
        if not 0.0 <= self.quality_score <= 1.0:
            raise ValueError("quality_score must be between 0 and 1")
        if not 0.0 <= self.regression_safety_score <= 1.0:
            raise ValueError("regression_safety_score must be between 0 and 1")
        if not 0.0 <= self.minimality_score <= 1.0:
            raise ValueError("minimality_score must be between 0 and 1")
        if self.tests_run < 0 or self.tests_failed < 0 or self.tests_failed > self.tests_run:
            raise ValueError("invalid test counters")
        if self.cost_usd < 0:
            raise ValueError("cost_usd must be non-negative")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "evidence", tuple(self.evidence))

    @property
    def publishable(self) -> bool:
        """INCONCLUSIVE and failed reports are never publishable."""
        return self.status is VerificationStatus.PASSED and self.baseline_passed

    @property
    def verification_id(self) -> str:
        """Stable evidence identifier for PublishPermit binding."""
        payload = "\x00".join(
            (
                self.candidate_hash,
                self.status.value,
                *(f"{item.check}:{item.passed}:{item.exit_code}" for item in self.evidence),
            )
        )
        return hashlib.sha256(payload.encode()).hexdigest()


def _redact(value: str) -> str:
    return re.sub(
        r"(?i)\b(?:sk-[a-z0-9_-]+|gh[pousr]_[a-z0-9_-]+|bearer\s+[a-z0-9._~+/=-]+)\b",
        "[REDACTED]",
        str(value),
    )


__all__ = [
    "FailureContext",
    "VerificationEvidence",
    "VerificationReport",
    "VerificationStatus",
]
