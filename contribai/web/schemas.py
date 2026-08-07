"""Request schemas for the dashboard API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from contribai.control.mode import ExecutionMode


class RunRequest(BaseModel):
    """Request an explicitly-scoped contribution run."""

    model_config = ConfigDict(extra="forbid")

    mode: ExecutionMode = ExecutionMode.SHADOW
    repo_url: str | None = None
