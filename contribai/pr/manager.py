"""Read-only PR lifecycle helpers and PR description formatting."""

from __future__ import annotations

import logging

from contribai.core.models import Contribution, ContributionType, PRStatus
from contribai.github.client import GitHubClient
from contribai.publishing.permit import PublishPermitError

logger = logging.getLogger(__name__)


class PRManager:
    """Provide PR reads and formatting without owning GitHub write authority."""

    def __init__(self, github: GitHubClient):
        self._github = github

    async def create_pr(self, *args, **kwargs) -> None:
        """Reject the legacy publishing path; callers must use GitHubPublisher."""
        raise PublishPermitError(
            "Direct PRManager publishing is disabled; GitHubPublisher requires a valid permit"
        )

    @staticmethod
    def _build_signoff(user: dict) -> str | None:
        """Build a DCO ``Signed-off-by`` identity from GitHub user data."""
        name = user.get("name") or user.get("login", "")
        email = user.get("email")
        if not email:
            uid = user.get("id", "")
            login = user.get("login", "")
            email = f"{uid}+{login}@users.noreply.github.com"
        return f"{name} <{email}>" if name else None

    @staticmethod
    def _human_branch_name(contribution: Contribution) -> str:
        """Generate a natural-looking branch name without tool branding."""
        import re

        type_prefix = {
            ContributionType.SECURITY_FIX: "fix/security",
            ContributionType.CODE_QUALITY: "fix",
            ContributionType.DOCS_IMPROVE: "docs",
            ContributionType.UI_UX_FIX: "fix/ui",
            ContributionType.PERFORMANCE_OPT: "perf",
            ContributionType.FEATURE_ADD: "feat",
            ContributionType.REFACTOR: "refactor",
        }
        prefix = type_prefix.get(contribution.finding.type, "fix")
        slug = contribution.finding.title.lower()
        slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")[:50]
        return f"{prefix}/{slug}"

    def _generate_pr_body(self, contribution: Contribution) -> str:
        """Generate the existing default PR description."""
        finding = contribution.finding
        files_list = "\n".join(
            f"- `{change.path}` {'(new)' if change.is_new_file else '(modified)'}"
            for change in contribution.changes
        )
        breaking_change = (
            "- [ ] Breaking change (fix or feature that would cause existing functionality "
            "to not work as expected)"
        )

        return f"""## Description

{finding.description}

## Changes

{files_list}

## Type of Change

- [x] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
{breaking_change}
- [ ] Documentation update

## Testing

- [x] Code follows the style guidelines of this project
- [x] Self-review of code completed
- [x] Changes generate no new warnings
- [ ] Corresponding changes to documentation made (if applicable)

**Severity**: `{finding.severity.value}`
"""

    async def get_pr_status(self, owner: str, repo: str, pr_number: int) -> PRStatus:
        """Check the current status of a PR."""
        try:
            data = await self._github._get(f"/repos/{owner}/{repo}/pulls/{pr_number}")
            state = data.get("state", "open")
            merged = data.get("merged", False)

            if merged:
                return PRStatus.MERGED
            if state == "closed":
                return PRStatus.CLOSED
            if data.get("requested_reviewers"):
                return PRStatus.REVIEW_REQUESTED
            return PRStatus.OPEN
        except Exception:
            return PRStatus.PENDING
