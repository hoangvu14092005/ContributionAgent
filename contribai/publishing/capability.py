"""Capability types used to authorize control-plane operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Capability(StrEnum):
    """Operations that may be authorized by the control plane."""

    WORKSPACE_READ = "workspace.read"
    WORKSPACE_WRITE = "workspace.write"
    NETWORK = "network"
    GITHUB_READ = "github.read"
    GITHUB_COMMENT = "github.comment"
    GITHUB_PUSH = "github.push"
    GITHUB_CREATE_ISSUE = "github.create_issue"
    GITHUB_CREATE_PR = "github.create_pr"
    GITHUB_CLOSE_PR = "github.close_pr"


@dataclass(frozen=True)
class CapabilityRequest:
    """An actor's request to use a capability for one work item."""

    actor: str
    capability: Capability
    resource: str
    work_id: str
