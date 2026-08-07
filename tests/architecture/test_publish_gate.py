"""Architecture tests for the single GitHub publishing authority."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from contribai.github.client import (
    GitHubClient,
    GitHubWriteAuthority,
    GitHubWriteAuthorityError,
)

_PROJECT_ROOT = Path(__file__).parents[2]
_PRODUCTION_ROOT = _PROJECT_ROOT / "contribai"
_PUBLISHER_PATH = _PRODUCTION_ROOT / "publishing" / "github_publisher.py"
_CLIENT_PATH = _PRODUCTION_ROOT / "github" / "client.py"
_AUTHORITY_ISSUER = "_issue_github_write_authority"
_CLIENT_AUTHORITY_ISSUER = "_issue_write_authority"
_GITHUB_CLIENT_SENSITIVE_ATTRIBUTES = frozenset(
    {
        "__github_token",
        "__github_transport",
        "__github_write_authority",
        "_GitHubClient__github_token",
        "_GitHubClient__github_transport",
        "_GitHubClient__github_write_authority",
    }
)
_GITHUB_WRITE_METHODS = frozenset(
    {
        "close_issue",
        "close_pull_request",
        "create_branch",
        "create_issue",
        "create_or_update_file",
        "delete_file",
        "create_pr_comment",
        "create_pr_review_comment_reply",
        "create_pull_request",
        "delete_repository",
        "fork_repository",
        "update_pull_request",
    }
)


def test_only_github_publisher_calls_github_write_methods() -> None:
    violations: list[str] = []

    for path in sorted(_PRODUCTION_ROOT.rglob("*.py")):
        if path == _PUBLISHER_PATH:
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _GITHUB_WRITE_METHODS
            ):
                relative_path = path.relative_to(_PROJECT_ROOT)
                violations.append(f"{relative_path}:{node.lineno}:{node.func.attr}")

    assert violations == [], "GitHub writes bypass GitHubPublisher:\n" + "\n".join(violations)


def test_every_github_client_write_method_requires_keyword_only_authority() -> None:
    violations: list[str] = []

    for method_name in sorted(_GITHUB_WRITE_METHODS):
        parameter = inspect.signature(getattr(GitHubClient, method_name)).parameters.get(
            "authority"
        )
        if parameter is None:
            violations.append(f"{method_name}:missing")
        elif parameter.kind is not inspect.Parameter.KEYWORD_ONLY:
            violations.append(f"{method_name}:not-keyword-only")
        elif parameter.default is not inspect.Parameter.empty:
            violations.append(f"{method_name}:optional")

    assert violations == [], "GitHub writes lack mandatory authority:\n" + "\n".join(violations)


def test_low_level_write_helpers_require_keyword_only_authority() -> None:
    violations: list[str] = []

    for method_name in ("_post", "_put", "_delete"):
        parameter = inspect.signature(getattr(GitHubClient, method_name)).parameters.get(
            "authority"
        )
        if parameter is None:
            violations.append(f"{method_name}:missing")
        elif parameter.kind is not inspect.Parameter.KEYWORD_ONLY:
            violations.append(f"{method_name}:not-keyword-only")
        elif parameter.default is not inspect.Parameter.empty:
            violations.append(f"{method_name}:optional")

    assert violations == [], "Low-level writes lack mandatory authority:\n" + "\n".join(violations)


def test_only_github_publisher_obtains_write_authority() -> None:
    publisher_issuers: list[str] = []
    client_issuers: list[str] = []

    for path in sorted(_PRODUCTION_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called_name = None
            if isinstance(node.func, ast.Name):
                called_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                called_name = node.func.attr
            if called_name == _AUTHORITY_ISSUER:
                publisher_issuers.append(f"{path.relative_to(_PROJECT_ROOT)}:{node.lineno}")
            elif called_name == _CLIENT_AUTHORITY_ISSUER:
                client_issuers.append(f"{path.relative_to(_PROJECT_ROOT)}:{node.lineno}")

    assert len(publisher_issuers) == 1
    assert publisher_issuers[0].startswith("contribai/publishing/github_publisher.py:")
    assert len(client_issuers) == 1
    assert client_issuers[0].startswith("contribai/github/client.py:")


def test_no_production_code_bypasses_client_write_methods_through_low_level_http() -> None:
    violations: list[str] = []
    mutation_verbs = {"POST", "PUT", "PATCH", "DELETE"}

    for path in sorted(_PRODUCTION_ROOT.rglob("*.py")):
        if path == _CLIENT_PATH:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method_name = node.func.attr
            if method_name in {"_post", "_put", "_delete"}:
                violations.append(f"{path.relative_to(_PROJECT_ROOT)}:{node.lineno}:{method_name}")
            if method_name not in {"_request", "_request_raw"} or not node.args:
                continue
            verb = node.args[0]
            if isinstance(verb, ast.Constant) and str(verb.value).upper() in mutation_verbs:
                violations.append(
                    f"{path.relative_to(_PROJECT_ROOT)}:{node.lineno}:{method_name}({verb.value})"
                )

    assert violations == [], "Low-level GitHub writes bypass authority:\n" + "\n".join(violations)


def test_no_production_code_accesses_github_raw_transport_or_token() -> None:
    violations: list[str] = []

    for path in sorted(_PRODUCTION_ROOT.rglob("*.py")):
        if path == _CLIENT_PATH:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            sensitive_name = None
            if isinstance(node, ast.Attribute) and node.attr in _GITHUB_CLIENT_SENSITIVE_ATTRIBUTES:
                sensitive_name = node.attr
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in _GITHUB_CLIENT_SENSITIVE_ATTRIBUTES
            ):
                sensitive_name = node.value
            if sensitive_name is not None:
                violations.append(
                    f"{path.relative_to(_PROJECT_ROOT)}:{node.lineno}:{sensitive_name}"
                )

    assert violations == [], "GitHub credentials/transport escaped client.py:\n" + "\n".join(
        violations
    )


def test_write_authority_cannot_be_constructed_by_a_client_holder() -> None:
    with pytest.raises(TypeError):
        GitHubWriteAuthority()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_low_level_request_rejects_mutation_without_authority(method: str) -> None:
    client = GitHubClient("test-token")
    try:
        with pytest.raises(GitHubWriteAuthorityError, match="publisher authority"):
            await client._request(method, "/forbidden")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_low_level_raw_request_rejects_mutation_without_authority() -> None:
    client = GitHubClient("test-token")
    try:
        with pytest.raises(GitHubWriteAuthorityError, match="publisher authority"):
            await client._request_raw("POST", "/forbidden")
    finally:
        await client.close()
