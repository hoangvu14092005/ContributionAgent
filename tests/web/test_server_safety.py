"""Safety contract for dashboard-triggered pipeline runs."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from contribai.web.auth import configure_auth
from contribai.web.dashboard import render_dashboard
from contribai.web.server import app


@pytest.fixture(autouse=True)
def reset_auth():
    """Keep process-global auth state isolated between tests."""
    configure_auth([])
    yield
    configure_auth([])


def _post(path: str, payload: dict, *, api_key: str | None = None):
    headers = {"X-API-Key": api_key} if api_key else {}
    background_run = AsyncMock()
    queued_item = MagicMock(id="work-queued")
    with (
        patch("contribai.web.server._background_run", background_run),
        patch(
            "contribai.web.server._submit_control_command",
            AsyncMock(return_value=queued_item),
        ),
    ):
        response = TestClient(app).post(path, json=payload, headers=headers)
    return response, background_run


def test_missing_mode_defaults_to_shadow():
    response, background_run = _post("/api/run", {})

    assert response.status_code == 200
    assert response.json() == {"status": "started", "mode": "shadow"}
    assert background_run.await_args.args[0] is None
    assert background_run.await_args.args[1] == "shadow"


@pytest.mark.parametrize("mode", ["shadow", "review_only"])
def test_safe_modes_do_not_require_api_key(mode: str):
    response, background_run = _post("/api/run", {"mode": mode})

    assert response.status_code == 200
    assert response.json()["mode"] == mode
    assert background_run.await_args.args[1] == mode


def test_live_fails_closed_when_no_api_keys_are_configured():
    response, background_run = _post("/api/run", {"mode": "live"})

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()
    background_run.assert_not_awaited()


def test_live_treats_blank_api_keys_as_unconfigured():
    configure_auth(["", "   "])

    response, background_run = _post("/api/run", {"mode": "live"})

    assert response.status_code == 503
    background_run.assert_not_awaited()


def test_live_rejects_invalid_api_key():
    configure_auth(["valid-key"])

    response, background_run = _post("/api/run", {"mode": "live"}, api_key="wrong-key")

    assert response.status_code == 403
    background_run.assert_not_awaited()


def test_live_accepts_valid_api_key():
    configure_auth(["valid-key"])

    response, background_run = _post("/api/run", {"mode": "live"}, api_key="valid-key")

    assert response.status_code == 200
    assert response.json() == {
        "status": "queued",
        "mode": "live",
        "work_id": "work-queued",
    }
    background_run.assert_not_awaited()


def test_target_run_binds_repo_and_mode_from_json_body():
    response, background_run = _post(
        "/api/run/target",
        {"repo_url": "https://github.com/example/project", "mode": "review_only"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "started",
        "repo_url": "https://github.com/example/project",
        "mode": "review_only",
    }
    assert background_run.await_args.args == (
        "https://github.com/example/project",
        "review_only",
    )


def test_target_run_requires_repo_url_in_body():
    response, background_run = _post("/api/run/target", {"mode": "shadow"})

    assert response.status_code == 422
    background_run.assert_not_awaited()


def test_legacy_dry_run_body_is_rejected():
    response, background_run = _post("/api/run", {"dry_run": False})

    assert response.status_code == 422
    background_run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_dry_run"),
    [("shadow", True), ("review_only", True)],
)
async def test_background_run_maps_mode_to_pipeline_dry_run(mode: str, expected_dry_run: bool):
    pipeline = MagicMock()
    pipeline.run = AsyncMock()
    pipeline.run.return_value = MagicMock(repos_analyzed=1, prs_created=0)

    with (
        patch("contribai.web.server.load_config", return_value=MagicMock()),
        patch("contribai.web.server.ContribPipeline", return_value=pipeline),
    ):
        from contribai.web.server import _background_run

        await _background_run(None, mode)

    pipeline.run.assert_awaited_once_with(dry_run=expected_dry_run)


def test_dashboard_only_sends_explicit_safe_modes():
    html = render_dashboard({}, [], [])

    assert "triggerRun('shadow')" in html
    assert "triggerRun('review_only')" in html
    assert "triggerRun('live')" not in html
    assert "JSON.stringify({mode: mode})" in html
    assert "dry_run" not in html


def test_live_rejects_api_key_in_query_string():
    configure_auth(["valid-key"])
    response, background_run = _post("/api/run?api_key=valid-key", {"mode": "live"})
    assert response.status_code == 401
    background_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_background_live_queues_without_legacy_pipeline():
    pipeline = MagicMock()
    pipeline.run = AsyncMock()
    with (
        patch("contribai.web.server.load_config", return_value=MagicMock()),
        patch("contribai.web.server.ContribPipeline", return_value=pipeline),
        patch(
            "contribai.web.server._submit_control_command",
            AsyncMock(return_value=MagicMock(id="work-live")),
        ),
    ):
        from contribai.web.server import _background_run

        await _background_run(None, "live")
    pipeline.run.assert_not_awaited()
