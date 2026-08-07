"""GitHub webhook receiver for auto-triggering pipeline.

Supports events: issues.opened, issues.labeled, push.
Verifies HMAC-SHA256 signatures for security.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from contribai.control.mode import ExecutionMode
from contribai.web.auth import verify_webhook_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

_webhook_enabled: bool = False
_webhook_secret: str = ""
_webhook_mode: ExecutionMode = ExecutionMode.SHADOW
_on_event_callback = None

# Max webhook payload: 10 MB (GitHub's own limit is 25 MB)
MAX_PAYLOAD_SIZE = 10 * 1024 * 1024


def reset_webhooks() -> None:
    """Disable webhook dispatch and remove all process-global state."""
    global _webhook_enabled, _webhook_secret, _webhook_mode, _on_event_callback
    _webhook_enabled = False
    _webhook_secret = ""
    _webhook_mode = ExecutionMode.SHADOW
    _on_event_callback = None


def configure_webhooks(
    *,
    enabled: bool,
    secret: str,
    mode: ExecutionMode,
    on_event=None,
):
    """Configure webhook safety policy and event handler."""
    global _webhook_enabled, _webhook_secret, _webhook_mode, _on_event_callback
    mode = ExecutionMode(mode)
    if mode is ExecutionMode.LIVE:
        raise ValueError("live webhook mode is forbidden")
    if enabled and not secret.strip():
        raise ValueError("webhook secret is required when webhooks are enabled")

    _webhook_enabled = enabled
    _webhook_secret = secret
    _webhook_mode = mode
    _on_event_callback = on_event


@router.post("/github")
async def github_webhook(request: Request):
    """Receive GitHub webhook events."""
    if not _webhook_enabled:
        return JSONResponse({"error": "Webhook receiver is disabled"}, status_code=404)

    # Reject oversized payloads
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_PAYLOAD_SIZE:
                return JSONResponse({"error": "Payload too large"}, status_code=413)
        except ValueError:
            return JSONResponse({"error": "Invalid Content-Length"}, status_code=400)

    # Read body once — used for signature check and payload size fallback
    body = await request.body()

    # Content-Length is untrusted and may underreport the payload.
    if len(body) > MAX_PAYLOAD_SIZE:
        return JSONResponse({"error": "Payload too large"}, status_code=413)

    # Enabled receivers always have a configured secret and require a valid signature.
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_webhook_signature(body, signature, _webhook_secret):
        logger.warning("Invalid webhook signature")
        return JSONResponse({"error": "Invalid signature"}, status_code=403)

    # Parse event
    event_type = request.headers.get("X-GitHub-Event", "")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "Malformed JSON payload"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "JSON payload must be an object"}, status_code=400)

    action = payload.get("action", "")
    if not isinstance(action, str):
        return JSONResponse({"error": "action must be a string"}, status_code=400)

    repo_name = ""
    repo_url = ""
    issue_number = None
    issue_title = ""
    ref = ""
    if event_type in {"issues", "push"}:
        repository = payload.get("repository")
        if not isinstance(repository, dict):
            return JSONResponse({"error": "repository must be an object"}, status_code=400)

        repo_name = repository.get("full_name", "")
        repo_url = repository.get("html_url", "")
        if not isinstance(repo_name, str):
            return JSONResponse({"error": "repository.full_name must be a string"}, status_code=400)
        if not isinstance(repo_url, str):
            return JSONResponse({"error": "repository.html_url must be a string"}, status_code=400)

    if event_type == "issues":
        issue = payload.get("issue")
        if not isinstance(issue, dict):
            return JSONResponse({"error": "issue must be an object"}, status_code=400)
        issue_number = issue.get("number")
        issue_title = issue.get("title", "")
        if issue_number is not None and (
            not isinstance(issue_number, int) or isinstance(issue_number, bool)
        ):
            return JSONResponse({"error": "issue.number must be an integer"}, status_code=400)
        if not isinstance(issue_title, str):
            return JSONResponse({"error": "issue.title must be a string"}, status_code=400)

    if event_type == "push":
        ref = payload.get("ref", "")
        if not isinstance(ref, str):
            return JSONResponse({"error": "ref must be a string"}, status_code=400)

    logger.info(
        "Webhook: %s.%s from %s",
        event_type,
        action,
        repo_name,
    )

    # Handle events
    trigger = False

    if event_type == "issues" and action in (
        "opened",
        "labeled",
    ):
        trigger = True
        logger.info(
            "Issue event: #%s %s",
            issue_number,
            issue_title,
        )

    elif event_type == "push":
        if ref.endswith("/main") or ref.endswith("/master"):
            trigger = True
            logger.info("Push to default branch: %s", ref)

    if trigger and _on_event_callback and repo_url:
        try:
            await _on_event_callback(event_type, action, repo_url, _webhook_mode)
        except Exception:
            logger.exception("Webhook event handler failed")

    return {
        "status": "received",
        "event": event_type,
        "action": action,
        "triggered": trigger,
    }
