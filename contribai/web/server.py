"""FastAPI REST API server for ContribAI dashboard.

Provides endpoints for monitoring stats, runs, PRs,
webhook receiver, and triggering pipeline executions.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse

from contribai import __version__
from contribai.control.command_service import CommandService
from contribai.control.mode import ExecutionMode
from contribai.core.config import ContribAIConfig, load_config
from contribai.orchestrator.memory import Memory
from contribai.orchestrator.pipeline import ContribPipeline
from contribai.web.auth import (
    configure_auth,
    get_presented_api_key,
    require_configured_api_key,
    reset_auth,
)
from contribai.web.dashboard import render_dashboard
from contribai.web.schemas import RunRequest
from contribai.web.webhooks import configure_webhooks, reset_webhooks
from contribai.web.webhooks import router as webhook_router

logger = logging.getLogger(__name__)

_config: ContribAIConfig | None = None
_memory: Memory | None = None


async def _submit_control_command(
    config: ContribAIConfig,
    repo_url: str | None,
    mode: ExecutionMode,
    *,
    source: str,
):
    """Persist an entrypoint command before any legacy pipeline continuation."""
    memory = _memory
    owns_memory = False
    if memory is None:
        db_path = getattr(getattr(config, "storage", None), "resolved_db_path", None)
        if not isinstance(db_path, (str, Path)):
            return None
        memory = Memory(db_path)
        await memory.init()
        owns_memory = True
    try:
        return await CommandService(memory).submit(
            repo_url or "contribai/discovery",
            mode=mode,
            metadata={"source": source},
        )
    finally:
        if owns_memory:
            await memory.close()


async def _webhook_event_handler(
    event_type: str,
    action: str,
    repo_url: str,
    mode: ExecutionMode,
):
    """Handle webhook events by running pipeline."""
    mode = ExecutionMode(mode)
    if mode is ExecutionMode.LIVE:
        logger.error("Rejected forbidden live webhook run for %s", repo_url)
        return
    config = load_config()
    await _submit_control_command(config, repo_url, mode, source="webhook")
    pipeline = ContribPipeline(config)
    try:
        result = await pipeline.run_single(repo_url, dry_run=mode.dry_run)
        logger.info(
            "Webhook-triggered run: %d PRs for %s",
            result.prs_created,
            repo_url,
        )
    except Exception:
        logger.exception(
            "Webhook pipeline run failed for %s",
            repo_url,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and cleanup shared resources."""
    global _config, _memory
    reset_auth()
    reset_webhooks()
    _config = None
    _memory = None
    try:
        _config = load_config()
        _memory = Memory(_config.storage.resolved_db_path)
        await _memory.init()

        configure_auth(_config.web.api_keys)
        configure_webhooks(
            enabled=_config.web.webhook_enabled,
            secret=_config.web.webhook_secret,
            mode=_config.web.webhook_mode,
            on_event=_webhook_event_handler,
        )

        logger.info("Dashboard API started")
        yield
    finally:
        reset_auth()
        reset_webhooks()
        memory = _memory
        _config = None
        _memory = None
        if memory is not None:
            await memory.close()


app = FastAPI(
    title="ContribAI Dashboard",
    version=__version__,
    lifespan=lifespan,
)

# Mount webhook router
app.include_router(webhook_router)


# ── Public endpoints ─────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the HTML dashboard."""
    stats = await _memory.get_stats()
    repos = await _memory.get_analyzed_repos(limit=20)
    prs = await _memory.get_prs(limit=20)
    return render_dashboard(stats, repos, prs)


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "version": __version__}


@app.get("/api/stats")
async def get_stats():
    """Get overall statistics."""
    return await _memory.get_stats()


@app.get("/api/repos")
async def get_repos(limit: int = 50):
    """Get analyzed repositories."""
    return await _memory.get_analyzed_repos(limit=limit)


@app.get("/api/prs")
async def get_prs(status: str | None = None, limit: int = 50):
    """Get submitted PRs, optionally filtered."""
    return await _memory.get_prs(status=status, limit=limit)


@app.get("/api/runs")
async def get_runs(limit: int = 20):
    """Get run history."""
    return await _memory.get_run_history(limit=limit)


# ── Execution endpoints (live mode requires API key) ─


async def _background_run(repo_url: str | None, mode: ExecutionMode):
    """Execute pipeline in background."""
    mode = ExecutionMode(mode)
    config = load_config()
    await _submit_control_command(config, repo_url, mode, source="web.run")
    pipeline = ContribPipeline(config)
    try:
        if repo_url:
            result = await pipeline.run_single(repo_url, dry_run=mode.dry_run)
        else:
            result = await pipeline.run(dry_run=mode.dry_run)
        logger.info(
            "Background run: %d repos, %d PRs",
            result.repos_analyzed,
            result.prs_created,
        )
    except Exception:
        logger.exception("Background run failed")


@app.post("/api/run")
async def trigger_run(
    background_tasks: BackgroundTasks,
    request: RunRequest,
    presented_key: str | None = Depends(get_presented_api_key),
):
    """Trigger a pipeline run with an explicit execution mode."""
    if request.mode is ExecutionMode.LIVE:
        require_configured_api_key(presented_key)
    background_tasks.add_task(_background_run, None, request.mode)
    return {"status": "started", "mode": request.mode}


@app.post("/api/run/target")
async def trigger_target(
    background_tasks: BackgroundTasks,
    request: RunRequest,
    presented_key: str | None = Depends(get_presented_api_key),
):
    """Target a specific repo with an explicit execution mode."""
    if not request.repo_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="repo_url is required",
        )
    if request.mode is ExecutionMode.LIVE:
        require_configured_api_key(presented_key)
    background_tasks.add_task(_background_run, request.repo_url, request.mode)
    return {
        "status": "started",
        "repo_url": request.repo_url,
        "mode": request.mode,
    }


@app.post("/api/work-items")
async def submit_work_item(
    request: RunRequest,
    presented_key: str | None = Depends(get_presented_api_key),
):
    """Submit a durable control-plane WorkItem without running GitHub writes inline."""
    if request.mode is ExecutionMode.LIVE:
        require_configured_api_key(presented_key)
    config = _config or load_config()
    item = await _submit_control_command(
        config, request.repo_url, request.mode, source="web.command"
    )
    if item is None:
        raise HTTPException(status_code=503, detail="control plane is not initialized")
    return {"status": "queued", "work_id": item.id, "repo": item.repo, "mode": item.mode}


@app.get("/api/work-items/{work_id}")
async def get_work_item(work_id: str):
    """Read one persisted WorkItem snapshot."""
    if _memory is None:
        raise HTTPException(status_code=503, detail="control plane is not initialized")
    item = await CommandService(_memory).get(work_id)
    return {
        "work_id": item.id,
        "repo": item.repo,
        "issue_number": item.issue_number,
        "mode": item.mode,
        "state": item.state,
        "attempt": item.attempt,
        "version": item.version,
    }


def run_server(
    config: ContribAIConfig | None = None,
):
    """Start the uvicorn server."""
    import uvicorn

    cfg = config or load_config()
    uvicorn.run(
        "contribai.web.server:app",
        host=cfg.web.host,
        port=cfg.web.port,
        log_level="info",
    )
