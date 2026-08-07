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


def configure_webhooks(
    *,
    enabled: bool,
    secret: str,
    mode: ExecutionMode,
    on_event=None,
):
    """Configure webhook safety policy and event handler."""
    global _webhook_enabled, _webhook_secret, _webhook_mode, _on_event_callback
    if enabled and not secret.strip():
        raise ValueError("webhook secret is required when webhooks are enabled")

    _webhook_enabled = enabled
    _webhook_secret = secret
    _webhook_mode = ExecutionMode(mode)
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

    # Bug 4 fix: if content-length header is missing, check actual body size
    if not content_length and len(body) > MAX_PAYLOAD_SIZE:
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
    repo_name = payload.get("repository", {}).get("full_name", "")

    logger.info(
        "Webhook: %s.%s from %s",
        event_type,
        action,
        repo_name,
    )

    # Handle events
    trigger = False
    repo_url = ""

    if event_type == "issues" and action in (
        "opened",
        "labeled",
    ):
        repo_url = payload.get("repository", {}).get("html_url", "")
        trigger = True
        logger.info(
            "Issue event: #%s %s",
            payload.get("issue", {}).get("number"),
            payload.get("issue", {}).get("title"),
        )

    elif event_type == "push":
        repo_url = payload.get("repository", {}).get("html_url", "")
        ref = payload.get("ref", "")
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
