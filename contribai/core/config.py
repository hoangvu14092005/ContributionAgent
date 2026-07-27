"""Pydantic-based configuration system for ContribAI."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from contribai.core.exceptions import ConfigError


class GitHubConfig(BaseModel):
    """GitHub API configuration."""

    token: str = ""
    max_repos_per_run: int = 5
    max_prs_per_day: int = 10
    rate_limit_buffer: int = 100
    dco_signoff: bool = True  # Auto-append Signed-off-by to commit messages

    @model_validator(mode="after")
    def resolve_token(self):
        """Fallback: $GITHUB_TOKEN env var → `gh auth token` CLI."""
        if not self.token:
            self.token = os.environ.get("GITHUB_TOKEN", "")
        if not self.token:
            try:
                result = subprocess.run(
                    ["gh", "auth", "token"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    self.token = result.stdout.strip()
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        return self


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    provider: Literal["gemini", "openai", "anthropic", "ollama", "custom"] = "gemini"
    model: str = "gemini-2.5-flash"
    api_key: str = ""
    temperature: float = 0.3
    max_tokens: int = 8192
    base_url: str | None = None  # for ollama or custom endpoints
    # Vertex AI (Google Cloud)
    vertex_project: str = ""
    vertex_location: str = "global"
    
    # Custom self-hosted models per task (env var configurable)
    custom_models: dict[str, str] = Field(default_factory=dict)
    custom_base_url: str = ""

    # ── Fallback chains (per task, ordered by preference) ────────────────────
    # Each task can have a chain of provider/model slots. If one fails,
    # the next slot is tried automatically. See contribai/llm/fallback.py.
    #
    # Example YAML:
    #   fallback_chains:
    #     analysis:
    #       - {provider: rocket-free-2, base_url: ..., model: glm-5-free}
    #       - {provider: copilot,      base_url: ..., model: claude-sonnet-4.6}
    #       - {provider: rocket-free-1, base_url: ..., model: kilo-auto/free}
    fallback_chains: dict[str, list[dict]] = Field(default_factory=dict)
    fallback_enabled: bool = False  # Master switch for fallback mechanism

    @model_validator(mode="after")
    def resolve_api_key_and_defaults(self):
        """Fallback: env vars for API keys + default model per provider."""
        if not self.api_key:
            env_map = {
                "gemini": "GEMINI_API_KEY",
                "openai": "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "custom": "CUSTOM_LLM_API_KEY",
            }
            env_var = env_map.get(self.provider, "")
            if env_var:
                self.api_key = os.environ.get(env_var, "")
        
        # Custom base URL from env
        if not self.custom_base_url:
            self.custom_base_url = os.environ.get("CUSTOM_LLM_BASE_URL", "http://localhost:20128/v1")
        
        # Custom models per task from env vars
        if not self.custom_models:
            self.custom_models = {
                "analysis": os.environ.get("LLM_MODEL_ANALYSIS", "ag/gemini-3.1-pro-high"),
                "code_gen": os.environ.get("LLM_MODEL_CODE_GEN", "gh/claude-sonnet-4.6"),
                "review": os.environ.get("LLM_MODEL_REVIEW", "cx/gpt-5.4"),
                "validation": os.environ.get("LLM_MODEL_VALIDATION", "kr/claude-sonnet-4.5"),
                "issue_solver": os.environ.get("LLM_MODEL_ISSUE_SOLVER", "gh/claude-sonnet-4.6"),
                "compression": os.environ.get("LLM_MODEL_COMPRESSION", "gh/claude-sonnet-4.6"),
                "default": os.environ.get("LLM_MODEL_DEFAULT", "gh/claude-sonnet-4.6"),
            }
        
        # Vertex AI: project from env
        if not self.vertex_project:
            self.vertex_project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        # Default model per provider
        if self.model == "gemini-2.5-flash" and self.provider != "gemini":
            default_models = {
                "openai": "gpt-4o",
                "anthropic": "claude-sonnet-4-20250514",
                "ollama": "codellama:13b",
                "custom": "default-model",
            }
            self.model = default_models.get(self.provider, self.model)
        return self

    @property
    def use_vertex(self) -> bool:
        """Whether to use Vertex AI instead of API key auth."""
        return bool(self.vertex_project)


class AnalysisConfig(BaseModel):
    """Analysis engine configuration."""

    enabled_analyzers: list[str] = Field(
        default_factory=lambda: ["security", "code_quality", "docs", "ui_ux"]
    )
    severity_threshold: Literal["low", "medium", "high", "critical"] = "medium"
    max_file_size_kb: int = 500
    skip_patterns: list[str] = Field(
        default_factory=lambda: ["*.min.js", "*.min.css", "vendor/*", "node_modules/*", "*.lock"]
    )
    max_context_tokens: int = 30_000  # token budget for context compression


class ContributionConfig(BaseModel):
    """Contribution generation configuration."""

    enabled_types: list[str] = Field(
        default_factory=lambda: [
            "security_fix",
            "docs_improve",
            "code_quality",
            "feature_add",
            "ui_ux_fix",
            "performance_opt",
            "refactor",
        ]
    )
    max_files_per_pr: int = 10
    run_tests_before_pr: bool = True
    commit_convention: Literal["conventional", "angular", "none"] = "conventional"
    pr_description_style: Literal["minimal", "detailed"] = "detailed"


class DiscoveryConfig(BaseModel):
    """Repository discovery configuration."""

    languages: list[str] = Field(default_factory=lambda: ["python"])
    stars_range: list[int] = Field(default_factory=lambda: [50, 10000])
    min_last_activity_days: int = 30
    require_contributing_guide: bool = False
    topics: list[str] = Field(default_factory=list)


class StorageConfig(BaseModel):
    """Storage / memory configuration."""

    db_path: str = "~/.contribai/memory.db"
    cache_ttl_hours: int = 24

    @property
    def resolved_db_path(self) -> Path:
        return Path(self.db_path).expanduser()


class SchedulerConfig(BaseModel):
    """Scheduler configuration for cron-based runs."""

    enabled: bool = False
    cron: str = "0 */6 * * *"  # every 6 hours
    timezone: str = "UTC"
    max_concurrent: int = 3


class WebConfig(BaseModel):
    """Web dashboard configuration."""

    host: str = "127.0.0.1"
    port: int = 8787
    enabled: bool = True
    api_keys: list[str] = Field(default_factory=list)
    webhook_secret: str = ""


class PipelineConfig(BaseModel):
    """Pipeline execution configuration."""

    max_concurrent_repos: int = 3
    timeout_per_repo_sec: int = 300
    inter_repo_delay_sec: float = 5.0  # delay between repos to avoid rate limits
    max_retries: int = 2  # middleware retry count
    min_quality_score: float = 7.0  # Phase 1: Increased from 5.0 to 7.0 for stricter quality gate
    human_review: bool = False  # pause for human approval before creating PRs
    check_ci: bool = True  # Auto-close PR if CI fails
    max_findings_per_repo: int = 3  # Cap on findings per repo (was hardcoded 2)


class QuotaConfig(BaseModel):
    """API usage quota configuration."""

    github_daily_limit: int = 5000
    llm_daily_limit: int = 1000
    llm_daily_tokens: int = 1_000_000


class NotificationConfig(BaseModel):
    """Notification channel configuration."""

    slack_webhook: str = ""
    discord_webhook: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    on_merge: bool = True
    on_close: bool = True
    on_run_complete: bool = True


class MultiModelConfig(BaseModel):
    """Multi-model routing configuration."""

    enabled: bool = False
    strategy: str = "balanced"  # performance | balanced | economy
    # Per-task model overrides (task_type → model_name)
    model_overrides: dict[str, str] = Field(default_factory=dict)


class SandboxConfig(BaseModel):
    """Sandbox execution configuration."""

    enabled: bool = False
    timeout: int = 30
    docker_image: str = ""  # override default language image


class ContribAIConfig(BaseModel):
    """Root configuration for ContribAIConfig."""

    github: GitHubConfig = Field(default_factory=GitHubConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    contribution: ContributionConfig = Field(default_factory=ContributionConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    quota: QuotaConfig = Field(default_factory=QuotaConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    multi_model: MultiModelConfig = Field(default_factory=MultiModelConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)


def _expand_env_vars(obj):
    """Recursively expand ``${VAR}`` and ``${VAR:-default}`` in config values."""
    import re
    import os

    pattern = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")

    def _expand(value):
        if isinstance(value, str):
            def replacer(match):
                var_name = match.group(1)
                default = match.group(2)
                return os.environ.get(var_name, default if default is not None else "")
            return pattern.sub(replacer, value)
        elif isinstance(value, dict):
            return {k: _expand(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [_expand(item) for item in value]
        return value

    return _expand(obj)


def load_config(path: str | Path | None = None) -> ContribAIConfig:
    """Load configuration from YAML file.

    Priority: explicit path > ./config.yaml > ~/.contribai/config.yaml > defaults
    Supports ``${VAR}`` and ``${VAR:-default}`` env var expansion in all values.
    """
    search_paths = [
        Path(path) if path else None,
        Path("config.yaml"),
        Path.home() / ".contribai" / "config.yaml",
    ]

    for p in search_paths:
        if p and p.exists():
            try:
                raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                raw = _expand_env_vars(raw)
                return ContribAIConfig(**raw)
            except yaml.YAMLError as e:
                raise ConfigError(f"Invalid YAML in {p}: {e}") from e
            except Exception as e:
                raise ConfigError(f"Failed to load config from {p}: {e}") from e

    # No config file found - use defaults
    return ContribAIConfig()
