# ContribAI

> **AI Agent that automatically contributes to open source projects on GitHub**
> 
> **✨ Custom LLM Support** — Use your own self-hosted LLM with per-task model routing

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-431%20passed-brightgreen)](#testing)
[![Version](https://img.shields.io/badge/version-4.1.0-blue)](https://github.com/chinhkrb113/ContribAI/releases)
[![Custom LLM](https://img.shields.io/badge/Custom%20LLM-Supported-green)](#custom-llm-support)


ContribAI discovers open source repositories, analyzes code for improvements, generates fixes, and submits Pull Requests — all autonomously.

```
  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
  │ Discovery│───▶│ Analysis │───▶│Generator │───▶│ PR + CI  │───▶│ Patrol   │
  │          │    │ 20 skills│    │ LLM +    │    │ Fork,    │    │ Auto-fix │
  │ Find repos│    │ Security │    │ self-    │    │ commit,  │    │ review   │
  │ by lang, │    │ quality, │    │ review,  │    │ create   │    │ feedback │
  │ stars    │    │ perf     │    │ scoring  │    │ PR + CLA │    │ & reply  │
  └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
```

**Safety:** Quality gate (7-check scorer), duplicate detection, AI policy respect, CI monitoring, rate limiting, dry-run mode

## Quick Start

```bash
# 1. Install
git clone https://github.com/chinhkrb113/ContribAI.git
cd ContribAI
pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
cp config.example.yaml config.yaml

# Edit .env with your credentials:
# - GITHUB_TOKEN (required)
# - CUSTOM_LLM_BASE_URL + API_KEY (if using custom LLM)
# - Or GEMINI_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY

# Edit config.yaml:
# - Set llm.provider: "custom" (or "gemini", "openai", "anthropic")

# 3. Run
contribai hunt              # Autonomous: discover repos → analyze → PR
contribai target <repo_url> # Target a specific repo
contribai run --dry-run     # Preview without creating PRs
```

**📚 Detailed Guides:**
- [QUICKSTART.md](QUICKSTART.md) — Quick start guide (English)
- [HUONG_DAN_SU_DUNG.md](HUONG_DAN_SU_DUNG.md) — Hướng dẫn chi tiết (Tiếng Việt)
- [SETUP_INSTRUCTIONS.md](SETUP_INSTRUCTIONS.md) — Setup checklist

## Features

| Category | Highlights |
|----------|-----------|
| **Analysis** | Security (secrets, SQLi, XSS), code quality, performance, docs, UI/UX, refactoring |
| **LLM** | **Custom self-hosted LLM** with per-task routing + Gemini, OpenAI, Anthropic, Ollama, Vertex AI |
| **Custom LLM** | ✨ **OpenAI-compatible API** — Route different models per task (analysis, code gen, review, etc.) |
| **Hunt Mode** | Multi-round autonomous hunting, cross-file fixes, inter-repo delay |
| **PR Patrol** | Monitors PRs for review feedback, auto-responds and pushes code fixes |
| **MCP Server** | 14 tools for Claude Desktop + Antigravity IDE via stdio protocol |
| **Safety** | AI policy detection, CLA auto-signing, quality gate, duplicate prevention |
| **Platform** | Web dashboard, scheduler, webhooks, Docker, profiles, plugins |
| **Notifications** | Slack, Discord, Telegram with retry |

## Usage

```bash
# Hunt mode (autonomous)
contribai hunt                         # Discover and contribute
contribai hunt --rounds 5 --delay 15   # 5 rounds, 15min delay
contribai hunt --mode issues           # Issue solving only

# Target specific repos
contribai target <repo_url>            # Analyze and contribute
contribai solve <repo_url>             # Solve open issues

# Monitor & maintain
contribai patrol                       # Respond to PR reviews
contribai status                       # Check submitted PRs
contribai stats                        # Overall statistics
contribai cleanup                      # Remove stale forks

# Platform
contribai serve                        # Dashboard at :8787
contribai schedule --cron "0 */6 * * *"  # Auto-run every 6h

# Profiles
contribai profile security-focused     # Run with preset profile
```

## Configuration

### Option 1: Custom Self-Hosted LLM (Recommended)

```bash
# .env
GITHUB_TOKEN=ghp_your_token
CUSTOM_LLM_BASE_URL=http://localhost:20128/v1
CUSTOM_LLM_API_KEY=your_api_key

# Per-task model routing
LLM_MODEL_ANALYSIS=ag/gemini-3.1-pro-high
LLM_MODEL_CODE_GEN=gh/claude-sonnet-4.6
LLM_MODEL_REVIEW=cx/gpt-5.4
LLM_MODEL_VALIDATION=kr/claude-sonnet-4.5
LLM_MODEL_ISSUE_SOLVER=gh/claude-sonnet-4.6
LLM_MODEL_COMPRESSION=gh/claude-sonnet-4.6
LLM_MODEL_DEFAULT=gh/claude-sonnet-4.6
```

```yaml
# config.yaml
llm:
  provider: "custom"
  # All settings read from .env
```

### Option 2: Cloud LLM Providers

```yaml
# config.yaml
github:
  token: "ghp_your_token"       # or set GITHUB_TOKEN env var

llm:
  provider: "gemini"            # gemini | openai | anthropic | ollama
  model: "gemini-2.5-flash"
  api_key: "your_api_key"       # or set GEMINI_API_KEY env var

discovery:
  languages: [python, javascript]
  stars_range: [100, 5000]
```

See [`config.example.yaml`](config.example.yaml) for all options.

## Custom LLM Support

ContribAI supports **self-hosted LLM endpoints** with OpenAI-compatible API format. You can route different models for different tasks:

| Task | Default Model | When Used |
|------|---------------|-----------|
| `analysis` | `ag/gemini-3.1-pro-high` | Code analysis (security, quality, docs) |
| `code_gen` | `gh/claude-sonnet-4.6` | Generating code fixes |
| `review` | `cx/gpt-5.4` | Self-reviewing generated code |
| `validation` | `kr/claude-sonnet-4.5` | Validating findings |
| `issue_solver` | `gh/claude-sonnet-4.6` | Solving GitHub issues |
| `compression` | `gh/claude-sonnet-4.6` | Compressing context |
| `default` | `gh/claude-sonnet-4.6` | Other operations |

**Setup Guide:** [docs/CUSTOM_LLM_SETUP.md](docs/CUSTOM_LLM_SETUP.md)

**Quick Reference:** [docs/CUSTOM_LLM_QUICK_REF.md](docs/CUSTOM_LLM_QUICK_REF.md)

**Technical Details:** [CUSTOM_LLM_CHANGES.md](CUSTOM_LLM_CHANGES.md)

## Architecture

```
contribai/
├── core/           # Config, models, middleware, events, retry, quotas
├── llm/            # Multi-provider LLM + task routing + context management
├── github/         # GitHub API client, discovery, guidelines
├── analysis/       # 20+ analysis skills + framework detection + compression
├── generator/      # Fix generation + self-review + quality scoring
├── orchestrator/   # Pipeline, SQLite memory (7 tables), review gate
├── pr/             # PR lifecycle + patrol + CLA/DCO compliance
├── issues/         # Issue classification + multi-file solving
├── agents/         # Sub-agent registry (DeerFlow-inspired)
├── tools/          # Extensible tool protocol
├── mcp/            # MCP client for external tools
├── mcp_server.py   # MCP server (14 tools for Claude Desktop)
├── sandbox/        # Docker-based code validation
├── web/            # FastAPI dashboard + webhooks + auth
├── scheduler/      # APScheduler cron automation
├── notifications/  # Slack, Discord, Telegram
├── plugins/        # Entry-point plugin system
├── templates/      # YAML contribution templates
└── cli/            # Rich CLI + TUI
```

See [`docs/system-architecture.md`](docs/system-architecture.md) for detailed architecture.

## Docker

```bash
docker compose up -d dashboard            # Dashboard at :8787
docker compose run --rm runner run        # One-shot run
docker compose up -d dashboard scheduler  # Dashboard + scheduler
```

## Testing

```bash
pytest tests/ -v                    # Run all 431 tests
pytest tests/ -v --cov=contribai    # With coverage
ruff check contribai/               # Lint
ruff format contribai/              # Format
```

## Extending

**Plugins** — Create custom analyzers/generators as Python packages:

```python
from contribai.plugins.base import AnalyzerPlugin

class MyAnalyzer(AnalyzerPlugin):
    @property
    def name(self): return "my-analyzer"

    async def analyze(self, context):
        return findings
```

```toml
# pyproject.toml
[project.entry-points."contribai.analyzers"]
my_analyzer = "my_package:MyAnalyzer"
```

**MCP** — Use ContribAI from Claude Desktop or Antigravity IDE:

```json
// Claude Desktop: ~/.config/claude/claude_desktop_config.json
// Antigravity IDE: ~/.gemini/antigravity/mcp_config.json
{
  "mcpServers": {
    "contribai": {
      "command": "python",
      "args": ["-m", "contribai.mcp_server"]
    }
  }
}
```

## Documentation

### Getting Started

| Doc | Description |
|-----|-------------|
| [**QUICKSTART.md**](QUICKSTART.md) | **Quick start guide** — Installation, configuration, basic usage (English) |
| [**HUONG_DAN_SU_DUNG.md**](HUONG_DAN_SU_DUNG.md) | **Hướng dẫn chi tiết** — Cài đặt, cấu hình, sử dụng (Tiếng Việt) |
| [**SETUP_INSTRUCTIONS.md**](SETUP_INSTRUCTIONS.md) | **Setup checklist** — Step-by-step setup guide |
| [`HALL_OF_FAME.md`](HALL_OF_FAME.md) | **9 merged PRs** across 21 repos — real results |

### Custom LLM

| Doc | Description |
|-----|-------------|
| [**CUSTOM_LLM_SETUP.md**](docs/CUSTOM_LLM_SETUP.md) | **Setup guide** — Configure self-hosted LLM with per-task routing |
| [**CUSTOM_LLM_QUICK_REF.md**](docs/CUSTOM_LLM_QUICK_REF.md) | **Quick reference** — Cheat sheet for custom LLM usage |
| [**GIT_PULL_CUSTOM_LLM.md**](docs/GIT_PULL_CUSTOM_LLM.md) | **Git workflow** — Pull updates while keeping custom config |
| [`CUSTOM_LLM_CHANGES.md`](CUSTOM_LLM_CHANGES.md) | **Technical details** — Implementation of custom LLM provider |

### Architecture & Development

| Doc | Description |
|-----|-------------|
| [`system-architecture.md`](docs/system-architecture.md) | Pipeline, middleware, events, LLM routing |
| [`code-standards.md`](docs/code-standards.md) | Conventions, patterns, testing |
| [`deployment-guide.md`](docs/deployment-guide.md) | Install, Docker, config, CLI reference |
| [`project-roadmap.md`](docs/project-roadmap.md) | Version history and future plans |
| [`codebase-summary.md`](docs/codebase-summary.md) | Module map and tech stack |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Contribution guidelines |

### Security

| Doc | Description |
|-----|-------------|
| [**BAO_MAT_TOKEN.md**](docs/BAO_MAT_TOKEN.md) | **Token security** — How to protect GitHub tokens (Tiếng Việt) |
| [`GITHUB_TOKEN_SETUP.md`](docs/GITHUB_TOKEN_SETUP.md) | GitHub token creation guide |

## License

AGPL-3.0 + Commons Clause — see [LICENSE](LICENSE) for details.
