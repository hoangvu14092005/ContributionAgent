"""Fail-closed safety tests for GitHub webhook dispatch."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from contribai.control.mode import ExecutionMode
from contribai.web.server import app
from contribai.web.webhooks import configure_webhooks

SECRET = "webhook-test-secret"
ISSUE_EVENT = {
    "action": "opened",
    "repository": {
        "full_name": "example/project",
        "html_url": "https://github.com/example/project",
    },
    "issue": {"number": 7, "title": "Fix it"},
}


def _signature(body: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _post(body: bytes, *, signature: str | None = None):
    headers = {"X-GitHub-Event": "issues", "Content-Type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return TestClient(app).post("/api/webhooks/github", content=body, headers=headers)


@pytest.fixture(autouse=True)
def reset_webhooks():
    configure_webhooks(
        enabled=False,
        secret="",
        mode=ExecutionMode.SHADOW,
        on_event=None,
    )
    yield
    configure_webhooks(
        enabled=False,
        secret="",
        mode=ExecutionMode.SHADOW,
        on_event=None,
    )


def test_disabled_webhook_does_not_dispatch():
    callback = AsyncMock()
    configure_webhooks(
        enabled=False,
        secret=SECRET,
        mode=ExecutionMode.LIVE,
        on_event=callback,
    )
    body = json.dumps(ISSUE_EVENT).encode()

    response = _post(body, signature=_signature(body))

    assert response.status_code == 404
    callback.assert_not_awaited()


def test_enabled_webhook_requires_secret():
    for secret in ("", "   "):
        with pytest.raises(ValueError, match="secret"):
            configure_webhooks(
                enabled=True,
                secret=secret,
                mode=ExecutionMode.SHADOW,
                on_event=AsyncMock(),
            )


@pytest.mark.parametrize("signature", [None, "sha256=invalid"])
def test_missing_or_invalid_signature_is_rejected(signature: str | None):
    callback = AsyncMock()
    configure_webhooks(
        enabled=True,
        secret=SECRET,
        mode=ExecutionMode.SHADOW,
        on_event=callback,
    )
    body = json.dumps(ISSUE_EVENT).encode()

    response = _post(body, signature=signature)

    assert response.status_code == 403
    callback.assert_not_awaited()


def test_malformed_json_returns_400_without_dispatch():
    callback = AsyncMock()
    configure_webhooks(
        enabled=True,
        secret=SECRET,
        mode=ExecutionMode.SHADOW,
        on_event=callback,
    )
    body = b"{not-json"

    response = _post(body, signature=_signature(body))

    assert response.status_code == 400
    callback.assert_not_awaited()


def test_callback_receives_explicit_configured_mode():
    callback = AsyncMock()
    configure_webhooks(
        enabled=True,
        secret=SECRET,
        mode=ExecutionMode.REVIEW_ONLY,
        on_event=callback,
    )
    body = json.dumps(ISSUE_EVENT).encode()

    response = _post(body, signature=_signature(body))

    assert response.status_code == 200
    assert response.json()["triggered"] is True
    callback.assert_awaited_once_with(
        "issues",
        "opened",
        "https://github.com/example/project",
        ExecutionMode.REVIEW_ONLY,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_dry_run"),
    [
        (ExecutionMode.SHADOW, True),
        (ExecutionMode.REVIEW_ONLY, True),
        (ExecutionMode.LIVE, False),
    ],
)
async def test_webhook_handler_maps_explicit_mode(mode: ExecutionMode, expected_dry_run: bool):
    pipeline = MagicMock()
    pipeline.run_single = AsyncMock(
        return_value=MagicMock(repos_analyzed=1, prs_created=0),
    )

    with (
        patch("contribai.web.server.load_config", return_value=MagicMock()),
        patch("contribai.web.server.ContribPipeline", return_value=pipeline),
    ):
        from contribai.web.server import _webhook_event_handler

        await _webhook_event_handler(
            "issues",
            "opened",
            "https://github.com/example/project",
            mode,
        )

    pipeline.run_single.assert_awaited_once_with(
        "https://github.com/example/project",
        dry_run=expected_dry_run,
    )
