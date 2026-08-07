"""Fail-closed safety tests for GitHub webhook dispatch."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from contribai.control.mode import ExecutionMode
from contribai.web.auth import configure_auth, require_configured_api_key
from contribai.web.server import app
from contribai.web.server import lifespan as server_lifespan
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


def _post(
    body: bytes,
    *,
    signature: str | None = None,
    event_type: str = "issues",
    content_length: int | None = None,
):
    headers = {"X-GitHub-Event": event_type, "Content-Type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    return TestClient(app, raise_server_exceptions=False).post(
        "/api/webhooks/github",
        content=body,
        headers=headers,
    )


@pytest.fixture(autouse=True)
def reset_webhooks():
    configure_auth([])
    configure_webhooks(
        enabled=False,
        secret="",
        mode=ExecutionMode.SHADOW,
        on_event=None,
    )
    yield
    configure_auth([])
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
        mode=ExecutionMode.REVIEW_ONLY,
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


def test_signed_webhook_cannot_configure_or_dispatch_live_mode():
    callback = AsyncMock()
    body = json.dumps(ISSUE_EVENT).encode()

    with pytest.raises(ValueError, match="live"):
        configure_webhooks(
            enabled=True,
            secret=SECRET,
            mode=ExecutionMode.LIVE,
            on_event=callback,
        )

    response = _post(body, signature=_signature(body))

    assert response.status_code == 404
    callback.assert_not_awaited()


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


def test_actual_body_size_is_enforced_when_content_length_underreports(monkeypatch):
    callback = AsyncMock()
    configure_webhooks(
        enabled=True,
        secret=SECRET,
        mode=ExecutionMode.SHADOW,
        on_event=callback,
    )
    monkeypatch.setattr("contribai.web.webhooks.MAX_PAYLOAD_SIZE", 8)
    body = json.dumps(ISSUE_EVENT).encode()

    response = _post(
        body,
        signature=_signature(body),
        content_length=1,
    )

    assert response.status_code == 413
    callback.assert_not_awaited()


@pytest.mark.parametrize(
    ("event_type", "payload", "expected_error"),
    [
        (
            "issues",
            {"action": "opened", "repository": [], "issue": ISSUE_EVENT["issue"]},
            "repository must be an object",
        ),
        (
            "issues",
            {
                "action": "opened",
                "repository": ISSUE_EVENT["repository"],
                "issue": [],
            },
            "issue must be an object",
        ),
        (
            "push",
            {"repository": ISSUE_EVENT["repository"], "ref": 123},
            "ref must be a string",
        ),
    ],
)
def test_malformed_event_schema_returns_400_without_dispatch(
    event_type: str,
    payload: dict,
    expected_error: str,
):
    callback = AsyncMock()
    configure_webhooks(
        enabled=True,
        secret=SECRET,
        mode=ExecutionMode.SHADOW,
        on_event=callback,
    )
    body = json.dumps(payload).encode()

    response = _post(body, signature=_signature(body), event_type=event_type)

    assert response.status_code == 400
    assert response.json() == {"error": expected_error}
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
async def test_lifespan_resets_stale_state_when_initialization_fails():
    stale_callback = AsyncMock()
    configure_auth(["stale-key"])
    configure_webhooks(
        enabled=True,
        secret="stale-secret",
        mode=ExecutionMode.REVIEW_ONLY,
        on_event=stale_callback,
    )

    with (
        patch("contribai.web.server.load_config", side_effect=RuntimeError("config failed")),
        pytest.raises(RuntimeError, match="config failed"),
    ):
        async with server_lifespan(app):
            pass

    with pytest.raises(HTTPException) as auth_error:
        require_configured_api_key("stale-key")
    assert auth_error.value.status_code == 503

    body = json.dumps(ISSUE_EVENT).encode()
    response = _post(body, signature=_signature(body, "stale-secret"))
    assert response.status_code == 404
    stale_callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_lifespan_replaces_prior_state_and_resets_on_shutdown():
    stale_callback = AsyncMock()
    active_callback = AsyncMock()
    configure_auth(["stale-key"])
    configure_webhooks(
        enabled=True,
        secret="stale-secret",
        mode=ExecutionMode.REVIEW_ONLY,
        on_event=stale_callback,
    )
    config = MagicMock()
    config.web.api_keys = ["active-key"]
    config.web.webhook_enabled = True
    config.web.webhook_secret = "active-secret"
    config.web.webhook_mode = ExecutionMode.SHADOW
    config.storage.resolved_db_path = ":memory:"
    memory = MagicMock()
    memory.init = AsyncMock()
    memory.close = AsyncMock()

    with (
        patch("contribai.web.server.load_config", return_value=config),
        patch("contribai.web.server.Memory", return_value=memory),
        patch("contribai.web.server._webhook_event_handler", active_callback),
    ):
        async with server_lifespan(app):
            assert require_configured_api_key("active-key") == "active-key"
            with pytest.raises(HTTPException) as stale_auth_error:
                require_configured_api_key("stale-key")
            assert stale_auth_error.value.status_code == 403

            body = json.dumps(ISSUE_EVENT).encode()
            stale_response = _post(body, signature=_signature(body, "stale-secret"))
            assert stale_response.status_code == 403
            active_response = _post(body, signature=_signature(body, "active-secret"))
            assert active_response.status_code == 200
            active_callback.assert_awaited_once_with(
                "issues",
                "opened",
                "https://github.com/example/project",
                ExecutionMode.SHADOW,
            )

    with pytest.raises(HTTPException) as auth_error:
        require_configured_api_key("active-key")
    assert auth_error.value.status_code == 503
    response = _post(body, signature=_signature(body, "active-secret"))
    assert response.status_code == 404
    stale_callback.assert_not_awaited()
    active_callback.assert_awaited_once()
    memory.init.assert_awaited_once()
    memory.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_dry_run"),
    [
        (ExecutionMode.SHADOW, True),
        (ExecutionMode.REVIEW_ONLY, True),
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


@pytest.mark.asyncio
async def test_webhook_handler_refuses_live_mode():
    load_config = MagicMock()
    pipeline_factory = MagicMock()

    with (
        patch("contribai.web.server.load_config", load_config),
        patch("contribai.web.server.ContribPipeline", pipeline_factory),
    ):
        from contribai.web.server import _webhook_event_handler

        await _webhook_event_handler(
            "issues",
            "opened",
            "https://github.com/example/project",
            ExecutionMode.LIVE,
        )

    load_config.assert_not_called()
    pipeline_factory.assert_not_called()
