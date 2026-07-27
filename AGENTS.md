# AI Agent Guide for ContribAI

> This document is designed for AI assistants (GitHub Copilot, Claude, Cursor, Coderabbit, etc.)
> scanning this repository. It provides structured context to help AI understand the codebase.

## What This Project Is

ContribAI is an **autonomous AI agent** that contributes to open source projects on GitHub.
It discovers repos, analyzes code, generates fixes, and submits pull requests — all without human intervention.

**It is NOT** a library/SDK, web app, or CLI tool intended for end-user consumption.
It is itself an AI agent that operates on other GitHub repositories.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Async | asyncio, aiohttp |
| HTTP | httpx (async) |
| Database | SQLite (aiosqlite) |
| LLM | Google Gemini (primary), OpenAI, Anthropic, Ollama, Vertex AI |
| GitHub | REST API v3 (via httpx) |
| Web | FastAPI + uvicorn |
| CLI | Typer + Rich |
| Tests | pytest (520 tests, 32 new) |
| Lint | ruff |

## Architecture (v4.1.0)

### Core Pipeline
```
Discovery → Middleware Chain → Analysis → Generation → PR → CI Monitor
```

### Key Patterns
1. **Middleware Chain** — 5 ordered middlewares (`contribai/core/middleware.py`)
2. **Progressive Skills** — 17 analysis skills loaded on-demand (`contribai/analysis/skills.py`)
3. **Sub-Agent Registry** — 5 agents with parallel execution (`contribai/agents/registry.py`)
4. **Tool Protocol** — MCP-inspired tool interface (`contribai/tools/protocol.py`)
5. **Outcome Learning** — Tracks PR outcomes to learn per-repo preferences (`contribai/orchestrator/memory.py`)
6. **Context Compression** — LLM-driven + truncation-based context compression (`contribai/analysis/context_compressor.py`)
7. **MCP Server** — 14 tools exposed via stdio for Claude Desktop (`contribai/mcp_server.py`)
8. **Event Bus** — 15 typed events with async subscribers and JSONL logging (`contribai/core/events.py`)
9. **Working Memory** — Auto-load/save context per repo with TTL (`contribai/orchestrator/memory.py`)
10. **Sandbox** — Docker-based code validation with local fallback (`contribai/sandbox/sandbox.py`)
11. **Skill Discovery** — `.agents/` markdown files parsed by `contribai/agents/skill_loader.py`; discoverable via `contribai skills list | find | show`

### Module Dependency Graph
```
cli/main.py
  └── orchestrator/pipeline.py (entry point)
        ├── core/config.py (configuration)
        ├── core/middleware.py (pipeline middlewares)
        ├── github/client.py (HTTP API)
        ├── github/discovery.py (repo search)
        ├── analysis/analyzer.py (7 analyzers)
        │     └── analysis/skills.py (progressive loading)
        ├── generator/engine.py (code generation)
        │     └── generator/scorer.py (quality scoring)
        ├── pr/manager.py (PR lifecycle)
        ├── pr/patrol.py (review monitoring)
        ├── issues/solver.py (issue solving)
        ├── orchestrator/memory.py (SQLite + working_memory)
        ├── agents/registry.py (sub-agent orchestration)
        ├── agents/skill_loader.py (parse .agents/ markdown frontmatter)
        ├── tools/protocol.py (tool interface)
        ├── analysis/context_compressor.py (LLM compression)
        ├── core/events.py (event bus + JSONL logger)
        ├── sandbox/sandbox.py (Docker + ast.parse)
        └── mcp_server.py (MCP stdio server, 14 tools)
```

## Code Conventions

| Convention | Standard |
|-----------|---------|
| Naming | `snake_case` for functions/variables, `PascalCase` for classes |
| Docstrings | Google style with Args/Returns/Raises |
| Async | All I/O operations are `async/await` |
| Error handling | `try/except` with logging, no bare `except` |
| Imports | Absolute imports, `from __future__ import annotations` |
| Type hints | Full type hints, `str | None` style unions |
| Line length | 100 chars (ruff) |
| Formatting | ruff format |

## Common Patterns

### LLM Calls
```python
# All LLM calls go through LLMProvider.complete()
response = await self._llm.complete(prompt, system_prompt=system)
```

### GitHub API Calls
```python
# All GitHub API calls go through GitHubClient
content = await self._github.get_file_content(owner, repo, path)
await self._github.create_or_update_file(owner, repo, path, content, message, signoff=signoff)
```

### Configuration
```python
# All config through Pydantic-like dataclasses in core/config.py
config = ContribAIConfig.from_yaml("config.yaml")
config.github.token  # str
config.llm.provider  # str
config.analysis.enabled_analyzers  # list[str]
```

### Memory/Persistence
```python
# SQLite via aiosqlite — outcome learning + working memory
memory = Memory("~/.contribai/memory.db")
await memory.init()
await memory.record_outcome(repo, pr_number, url, type, "merged")
prefs = await memory.get_repo_preferences(repo)

# Working memory — auto-load/save per-repo context (72h TTL)
await memory.store_context(repo, "analysis_summary", summary, ttl_hours=72)
cached = await memory.get_context(repo, "analysis_summary")
```

## File Organization Rules

- **Code files only**: ContribAI only modifies `.py`, `.js`, `.ts`, `.go`, `.rs` etc.
- **Never modify**: `LICENSE`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `.github/FUNDING.yml`
- **Skip extensions**: `.md`, `.yaml`, `.json`, `.toml`, `.cfg`, `.ini`
- **Protected meta files**: Any governance/meta files are off-limits

## Agent Skill Catalog

`.agents/` contains 30 markdown skills in 3 categories. All are programmatically discoverable via `contribai skills list`. Add a new skill by dropping a markdown file with YAML frontmatter (`description`, optional `trigger`, optional `inputs`).

### Agents (`.agents/agents/`) — role personas
| File | Trigger | Role |
|------|---------|------|
| `backend-dev.md` | — | Implements core features, Python modules, API integrations |
| `code-reviewer.md` | — | Reviews all PRs for quality, consistency, best practices |
| `codereview-roasted.md` | `/codereview-roasted` | Linus Torvalds-style brutally honest review persona |
| `code-review-patterns.md` | `/code-review` | Style, naming, complexity, architecture review checklist |
| `devops-engineer.md` | — | CI/CD, Docker, builds, deployments, infrastructure |
| `github-ops.md` | `/github` | How to use gh CLI and GitHub REST API correctly |
| `product-manager.md` | — | Prioritizes features, writes specs, manages roadmap |
| `qa-engineer.md` | — | Test coverage, edge cases, flakiness |
| `security-engineer.md` | — | Audits code for vulnerabilities, reviews security changes |
| `security-patterns.md` | `/security` | Core security principles + OWASP-aligned checklist |
| `tech-lead.md` | — | Architecture decisions, code review coordination |
| `technical-writer.md` | — | Documentation, README, CHANGELOG |

### Workflows (`.agents/workflows/`) — procedural step-by-step
| File | Trigger | Purpose |
|------|---------|---------|
| `address_pr_comments.md` | `/address_pr_comments` | Read PR review feedback and reply with code fixes |
| `add_agent.md` | `/add_agent` | Guided creation of a new agent/workflow file |
| `agent-builder.md` | `/agent-builder` | Interview user ≤5 questions and design a new agent |
| `agent_memory.md` | `/remember` | Maintain per-repo memory at `.agents/agents/memory/<repo>.md` |
| `debug.md` | — | Debug pipeline / agent failure |
| `deploy.md` | — | Production deploy checklist |
| `dev.md` | — | ContribAI development workflow |
| `docs.md` | — | Documentation update procedure |
| `git-flow.md` | — | Branching, merging, DCO signoff |
| `release.md` | — | Version bump, changelog, tag, publish |
| `review.md` | — | Code review workflow |
| `security-audit.md` | — | Security audit checklist |
| `setup.md` | — | New developer onboarding |
| `test.md` | — | Run tests, check coverage, fix failures |
| `update_pr_description.md` | `/update_pr_description` | Refresh PR body to reflect current diff |
| `update_test.md` | `/update_test` | Fix failing tests to match intentional behavior change |

### Knowledge (`.agents/knowledge/`) — reference docs
| File | Trigger | Topic |
|------|---------|-------|
| `default-tools.md` | `/default-tools` | MCP stdio servers auto-loaded per session |
| `onboarding.md` | `/onboard` | First-time user interview + step-by-step plan |

### CLI discovery
```bash
contribai skills list                    # All 30 skills in a table
contribai skills list --category workflow
contribai skills list --has-trigger
contribai skills find PR                 # Keyword search
contribai skills show /address_pr_comments   # Full body
contribai skills show update_test
```

- **Code files only**: ContribAI only modifies `.py`, `.js`, `.ts`, `.go`, `.rs` etc.
- **Never modify**: `LICENSE`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `.github/FUNDING.yml`
- **Skip extensions**: `.md`, `.yaml`, `.json`, `.toml`, `.cfg`, `.ini`
- **Protected meta files**: Any governance/meta files are off-limits

## Testing

```bash
pytest tests/ -v                  # 520 tests
pytest tests/ -v --cov=contribai  # With coverage (threshold: 50%)
```

Test structure:
```
tests/
├── unit/              # Unit tests for each module
│   ├── test_analyzer.py
│   ├── test_config.py
│   ├── test_pipeline_v2.py
│   ├── test_github_client.py
│   ├── test_patrol.py
│   └── ...
└── conftest.py        # Shared fixtures
```

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `GITHUB_TOKEN` | Yes | GitHub API authentication |
| `GEMINI_API_KEY` | Yes* | Google Gemini LLM |
| `OPENAI_API_KEY` | Alt | OpenAI LLM (alternative) |
| `ANTHROPIC_API_KEY` | Alt | Anthropic LLM (alternative) |
| `GOOGLE_CLOUD_PROJECT` | Opt | Vertex AI project |

## Known Limitations

1. Sandbox execution is opt-in (`sandbox.enabled = True`) — defaults to local `ast.parse` fallback
2. Single-repo PRs only — no cross-repo changes
3. No interactive mode — fully autonomous
4. Rate limited by GitHub API (5000 req/hour for authenticated users)
5. Context window managed by `ContextCompressor` (default 30k tokens)
