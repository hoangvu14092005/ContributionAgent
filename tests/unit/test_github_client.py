"""Tests for GitHub API client."""

import base64
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from contribai.core.exceptions import GitHubAPIError
from contribai.github.client import (
    GitHubClient,
    GitHubWriteAuthority,
    GitHubWriteAuthorityError,
    _issue_github_write_authority,
)
from contribai.publishing.github_publisher import GitHubPublisher
from contribai.publishing.policy import PolicyEngine


@pytest.fixture
def client():
    c = GitHubClient(token="ghp_test_token")
    yield c


def publisher_authority(client: GitHubClient) -> GitHubWriteAuthority:
    publisher = GitHubPublisher(client, PolicyEngine())
    return publisher._GitHubPublisher__write_authority


class TestParseRepo:
    def test_parse_full_repo(self):
        data = {
            "owner": {"login": "testowner"},
            "name": "testrepo",
            "full_name": "testowner/testrepo",
            "description": "A test repo",
            "language": "Python",
            "stargazers_count": 500,
            "forks_count": 50,
            "open_issues_count": 10,
            "topics": ["python"],
            "default_branch": "main",
            "html_url": "https://github.com/testowner/testrepo",
            "clone_url": "https://github.com/testowner/testrepo.git",
            "license": {"spdx_id": "MIT"},
        }
        repo = GitHubClient._parse_repo(data)
        assert repo.owner == "testowner"
        assert repo.name == "testrepo"
        assert repo.stars == 500
        assert repo.has_license is True

    def test_parse_minimal_repo(self):
        data = {
            "owner": {},
            "name": "x",
            "full_name": "a/x",
        }
        repo = GitHubClient._parse_repo(data)
        assert repo.owner == ""
        assert repo.stars == 0
        assert repo.has_license is False

    def test_parse_no_license(self):
        data = {
            "owner": {"login": "x"},
            "name": "y",
            "full_name": "x/y",
            "license": None,
        }
        repo = GitHubClient._parse_repo(data)
        assert repo.has_license is False


class TestClientHeaders:
    def test_auth_header(self, client):
        headers = client._client.headers
        assert "authorization" in {k.lower() for k in headers}


class TestContributingGuide:
    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, client):
        client.get_file_content = AsyncMock(
            side_effect=GitHubAPIError("Not found", status_code=404)
        )
        result = await client.get_contributing_guide("owner", "repo")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_content_when_found(self, client):
        client.get_file_content = AsyncMock(return_value="# Contributing\nPlease read...")
        result = await client.get_contributing_guide("owner", "repo")
        assert "Contributing" in result


class TestGetFileContentRef:
    @pytest.mark.asyncio
    async def test_get_file_content_with_ref(self, client):
        """ref param is passed as query param to GitHub API."""
        content_b64 = base64.b64encode(b"hello").decode()
        with respx.mock:
            respx.get(
                "https://api.github.com/repos/owner/repo/contents/file.py",
                params={"ref": "my-branch"},
            ).mock(
                return_value=httpx.Response(
                    200, json={"encoding": "base64", "content": content_b64}
                )
            )
            result = await client.get_file_content("owner", "repo", "file.py", ref="my-branch")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_get_file_content_without_ref(self, client):
        """ref param defaults to None — no query param sent."""
        content_b64 = base64.b64encode(b"world").decode()
        with respx.mock:
            respx.get(
                "https://api.github.com/repos/owner/repo/contents/file.py",
            ).mock(
                return_value=httpx.Response(
                    200, json={"encoding": "base64", "content": content_b64}
                )
            )
            result = await client.get_file_content("owner", "repo", "file.py")
        assert result == "world"


class TestGetFileContentWithSha:
    @pytest.mark.asyncio
    async def test_returns_content_and_sha(self, client):
        """get_file_content_with_sha returns (content, sha) tuple."""
        content_b64 = base64.b64encode(b"hello world").decode()
        with respx.mock:
            respx.get(
                "https://api.github.com/repos/owner/repo/contents/file.py",
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "encoding": "base64",
                        "content": content_b64,
                        "sha": "abc123def456",
                    },
                )
            )
            content, sha = await client.get_file_content_with_sha("owner", "repo", "file.py")
        assert content == "hello world"
        assert sha == "abc123def456"


class TestListUserForks:
    @pytest.mark.asyncio
    async def test_returns_fork_list(self, client):
        forks_data = [
            {"full_name": "me/forked-repo", "fork": True},
            {"full_name": "me/other-fork", "fork": True},
        ]
        with respx.mock:
            respx.get(
                "https://api.github.com/user/repos",
                params={"type": "fork", "per_page": "100"},
            ).mock(return_value=httpx.Response(200, json=forks_data))
            result = await client.list_user_forks()
        assert len(result) == 2
        assert result[0]["full_name"] == "me/forked-repo"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_forks(self, client):
        with respx.mock:
            respx.get(
                "https://api.github.com/user/repos",
                params={"type": "fork", "per_page": "100"},
            ).mock(return_value=httpx.Response(200, json=[]))
            result = await client.list_user_forks()
        assert result == []


class TestDeleteRepository:
    @pytest.mark.asyncio
    async def test_delete_success(self, client):
        authority = publisher_authority(client)
        with respx.mock:
            respx.delete("https://api.github.com/repos/me/forked-repo").mock(
                return_value=httpx.Response(204)
            )
            await client.delete_repository(
                "me", "forked-repo", authority=authority
            )  # Should not raise

    @pytest.mark.asyncio
    async def test_delete_raises_on_error(self, client):
        authority = publisher_authority(client)
        with respx.mock:
            respx.delete("https://api.github.com/repos/me/missing").mock(
                return_value=httpx.Response(404, json={"message": "Not Found"})
            )
            with pytest.raises(GitHubAPIError):
                await client.delete_repository("me", "missing", authority=authority)

    @pytest.mark.asyncio
    async def test_delete_requires_publisher_authority(self, client):
        with pytest.raises(TypeError, match="authority"):
            await client.delete_repository("me", "forked-repo")


class TestWriteAuthority:
    def test_non_publisher_cannot_obtain_authority(self, client):
        with pytest.raises(GitHubWriteAuthorityError, match="bound GitHubPublisher"):
            _issue_github_write_authority(client, object())

    @pytest.mark.asyncio
    async def test_authority_is_bound_to_the_issuing_client(self, client):
        other = GitHubClient(token="other-token")
        authority = publisher_authority(client)
        try:
            with pytest.raises(GitHubWriteAuthorityError, match="publisher authority"):
                await other._request("POST", "/forbidden", authority=authority)
        finally:
            await other.close()
