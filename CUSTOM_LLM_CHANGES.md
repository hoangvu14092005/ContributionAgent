# Custom LLM Provider - Implementation Summary

## Changes Made

### 1. Core Configuration (`contribai/core/config.py`)

**Added to `LLMConfig` class:**
- `provider` now supports `"custom"` option
- New field: `custom_models: dict[str, str]` - Maps task types to model names
- New field: `custom_base_url: str` - Base URL for custom endpoint
- Environment variable support for all custom settings:
  - `CUSTOM_LLM_BASE_URL` - Base URL (default: `http://localhost:20128/v1`)
  - `CUSTOM_LLM_API_KEY` - API key for authentication
  - `LLM_MODEL_ANALYSIS` - Model for code analysis
  - `LLM_MODEL_CODE_GEN` - Model for code generation
  - `LLM_MODEL_REVIEW` - Model for self-review
  - `LLM_MODEL_VALIDATION` - Model for finding validation
  - `LLM_MODEL_ISSUE_SOLVER` - Model for issue solving
  - `LLM_MODEL_COMPRESSION` - Model for context compression

### 2. LLM Provider (`contribai/llm/provider.py`)

**Added `CustomProvider` class:**
- Implements OpenAI-compatible API client
- Supports per-task model routing via `set_task()` method
- Automatic model selection based on current task context
- Logging of model usage per task
- Rate limit error handling with retry support

**Key methods:**
```python
def set_task(self, task_type: str) -> None
    """Set current task type for model routing."""

def _get_model_for_task(self, override_model: str | None = None) -> str
    """Get the appropriate model for the current task."""
```

### 3. Task Type Integration

**Updated files to set task context:**

1. **`contribai/analysis/analyzer.py`**
   - Sets task to `'analysis'` before LLM calls in `_run_analyzer()`

2. **`contribai/generator/engine.py`**
   - Sets task to `'code_gen'` in `generate()` method
   - Sets task to `'review'` in `_self_review()` method

3. **`contribai/orchestrator/pipeline.py`**
   - Sets task to `'validation'` in `_validate_findings()` method

4. **`contribai/issues/solver.py`**
   - Sets task to `'issue_solver'` in `solve_issue()` and `solve_issue_deep()` methods

5. **`contribai/analysis/context_compressor.py`**
   - Sets task to `'compression'` in `summarize_with_llm()` method

**Pattern used:**
```python
# Set task type for custom provider
if hasattr(self._llm, 'set_task'):
    self._llm.set_task('task_name')

response = await self._llm.complete(prompt, system=system)
```

### 4. Configuration Files

**Created/Updated:**
- `.env.example` - Template for environment variables
- `config.example.yaml` - Added custom provider configuration example
- `docs/CUSTOM_LLM_SETUP.md` - Comprehensive setup guide

## Task-to-Model Mapping

| Task | Environment Variable | Default Model | Usage |
|------|---------------------|---------------|-------|
| `analysis` | `LLM_MODEL_ANALYSIS` | `ag/gemini-3.1-pro-high` | Code analysis (security, quality, docs, UI/UX) |
| `code_gen` | `LLM_MODEL_CODE_GEN` | `gh/claude-sonnet-4.6` | Generating code fixes from findings |
| `review` | `LLM_MODEL_REVIEW` | `cx/gpt-5.4` | Self-reviewing generated code |
| `validation` | `LLM_MODEL_VALIDATION` | `kr/claude-sonnet-4.5` | Validating findings to filter false positives |
| `issue_solver` | `LLM_MODEL_ISSUE_SOLVER` | `gh/claude-sonnet-4.6` | Analyzing and solving GitHub issues |
| `compression` | `LLM_MODEL_COMPRESSION` | `gh/claude-sonnet-4.6` | Compressing context to fit token limits |
| `default` | `LLM_MODEL_DEFAULT` | `gh/claude-sonnet-4.6` | **Các thao tác khác không được định nghĩa cụ thể** |

## API Request Format

When using custom provider, ContribAI sends requests like:

```http
POST http://localhost:20128/v1/chat/completions
Content-Type: application/json
Authorization: Bearer your_api_key

{
  "model": "ag/gemini-3.1-pro-high",
  "messages": [
    {"role": "system", "content": "You are a senior software engineer..."},
    {"role": "user", "content": "Analyze this code..."}
  ],
  "temperature": 0.2,
  "max_tokens": 8192
}
```

## Quick Start

### Option 1: Environment Variables (Recommended)

```bash
# 1. Copy .env.example to .env
cp .env.example .env

# 2. Edit .env with your values
nano .env

# 3. Set provider in config.yaml
llm:
  provider: "custom"

# 4. Run ContribAI
contribai target https://github.com/owner/repo --dry-run
```

### Option 2: Direct Configuration

```yaml
# config.yaml
llm:
  provider: "custom"
  custom_base_url: "http://localhost:20128/v1"
  api_key: "your_api_key"
  custom_models:
    analysis: "ag/gemini-3.1-pro-high"
    code_gen: "gh/claude-sonnet-4.6"
    review: "cx/gpt-5.4"
    validation: "kr/claude-sonnet-4.5"
    issue_solver: "gh/claude-sonnet-4.6"
    compression: "gh/claude-sonnet-4.6"
```

## Logging

When custom provider is active, you'll see:

```
INFO: Custom provider initialized: http://localhost:20128/v1 (models: analysis=ag/gemini-3.1-pro-high, code_gen=gh/claude-sonnet-4.6, ...)
INFO: 🤖 Custom LLM call [task=analysis, model=ag/gemini-3.1-pro-high]
INFO: 🤖 Custom LLM call [task=code_gen, model=gh/claude-sonnet-4.6]
INFO: 🤖 Custom LLM call [task=review, model=cx/gpt-5.4]
INFO: 🤖 Custom LLM call [task=validation, model=kr/claude-sonnet-4.5]
```

## Testing

```bash
# Test with dry-run mode
export CUSTOM_LLM_BASE_URL=http://localhost:20128/v1
export LLM_MODEL_ANALYSIS=ag/gemini-3.1-pro-high
export LLM_MODEL_CODE_GEN=gh/claude-sonnet-4.6

contribai target https://github.com/owner/repo --dry-run

# Check logs for model routing
grep "Custom LLM call" ~/.contribai/logs/contribai.log
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    ContribAI Pipeline                        │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  Analysis Phase                                               │
│  ├─ analyzer.py → set_task('analysis')                       │
│  └─ LLM → POST /v1/chat/completions                          │
│           model: ag/gemini-3.1-pro-high                       │
│                                                               │
│  Code Generation Phase                                        │
│  ├─ engine.py → set_task('code_gen')                         │
│  └─ LLM → POST /v1/chat/completions                          │
│           model: gh/claude-sonnet-4.6                         │
│                                                               │
│  Self-Review Phase                                            │
│  ├─ engine.py → set_task('review')                           │
│  └─ LLM → POST /v1/chat/completions                          │
│           model: cx/gpt-5.4                                   │
│                                                               │
│  Validation Phase                                             │
│  ├─ pipeline.py → set_task('validation')                     │
│  └─ LLM → POST /v1/chat/completions                          │
│           model: kr/claude-sonnet-4.5                         │
│                                                               │
│  Issue Solving Phase                                          │
│  ├─ solver.py → set_task('issue_solver')                     │
│  └─ LLM → POST /v1/chat/completions                          │
│           model: gh/claude-sonnet-4.6                           │
│                                                               │
│  Context Compression Phase                                    │
│  ├─ context_compressor.py → set_task('compression')          │
│  └─ LLM → POST /v1/chat/completions                          │
│           model: gh/claude-sonnet-4.6                         │
│                                                               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                  ┌───────────────────────┐
                  │  Custom LLM Endpoint  │
                  │  localhost:20128/v1   │
                  └───────────────────────┘
```

## Backward Compatibility

All existing providers (gemini, openai, anthropic, ollama) continue to work as before. The custom provider is an additional option that doesn't affect existing functionality.

## Error Handling

The custom provider includes:
- Rate limit detection and retry with exponential backoff
- Connection error handling
- Model not found error reporting
- Graceful fallback on provider initialization failure

## Next Steps

1. Start your custom LLM endpoint
2. Configure environment variables or config.yaml
3. Test with `--dry-run` mode
4. Monitor logs for model routing
5. Adjust models per task based on performance

For detailed setup instructions, see `docs/CUSTOM_LLM_SETUP.md`.

For git workflow when pulling updates, see `docs/GIT_PULL_CUSTOM_LLM.md`.


---

## Git Workflow: Maintaining Custom LLM When Pulling Updates

### Overview

When pulling new code from upstream, conflicts may occur between the upstream changes and your custom LLM modifications. This section provides a safe workflow to handle these situations.

### Quick Reference

```bash
# 1. Stash your changes
git stash push -m "Backup custom LLM config before pull"

# 2. Pull new code
git pull origin main

# 3. Apply your changes back
git stash pop

# 4. Resolve conflicts (if any)
# 5. Test and commit
```

### Files That May Conflict

**High Priority (Always Keep Custom):**
- `contribai/llm/provider.py` - Core custom LLM logic
- `contribai/core/config.py` - Custom config fields
- `contribai/generator/engine.py` - Task setting for code generation
- `contribai/analysis/analyzer.py` - Task setting for analysis
- `contribai/orchestrator/pipeline.py` - Task setting for validation
- `contribai/issues/solver.py` - Task setting for issue solving
- `contribai/analysis/context_compressor.py` - Task setting for compression

**Medium Priority (Review Carefully):**
- `contribai/pr/manager.py` - PR title formatting
- `contribai/github/guidelines.py` - Guidelines adaptation
- `config.example.yaml` - Configuration template

**Low Priority (Usually Safe):**
- `config.yaml` - Your local config (not tracked)
- `.env` - Your environment variables (not tracked)
- Documentation files - Custom docs (untracked)

### Common Conflict Patterns

#### Pattern 1: Task Setting in engine.py

**Conflict:**
```python
<<<<<<< Updated upstream
            response = await self._llm.complete(
                prompt_with_hint, system=system, temperature=0.2
            )
=======
            # Set task type for custom provider
            if hasattr(self._llm, 'set_task'):
                self._llm.set_task('code_gen')

            response = await self._llm.complete(prompt, system=system, temperature=0.2)
>>>>>>> Stashed changes
```

**Resolution:** Keep custom with upstream variable names:
```python
            # Set task type for custom provider
            if hasattr(self._llm, 'set_task'):
                self._llm.set_task('code_gen')

            response = await self._llm.complete(
                prompt_with_hint, system=system, temperature=0.2
            )
```

#### Pattern 2: PR Title Logic

**Conflict:**
```python
<<<<<<< Updated upstream
            default_prefixes = ["Security:", "Quality:", ...]
            if any(new_title.startswith(prefix) for prefix in default_prefixes):
                # Complex logic
=======
            # Always regenerate title in conventional format if guidelines exist
            if guidelines and guidelines.has_guidelines:
>>>>>>> Stashed changes
```

**Resolution:** Keep simpler custom logic:
```python
            # Always regenerate title in conventional format if guidelines exist
            if guidelines and guidelines.has_guidelines:
```

#### Pattern 3: Guidelines Formatting

**Conflict:**
```python
<<<<<<< Updated upstream
    # Default: clean text format (no emoji in PR title)
    type_labels = {...}
    return f"{label}: {finding_title}"
=======
    # Build title with optional scope (always use conventional format, no emojis)
    if (scope and guidelines.requires_scope) or scope:
        return f"{cc_type}({scope}): {finding_title.lower()}"
    else:
        return f"{cc_type}: {finding_title.lower()}"
>>>>>>> Stashed changes
```

**Resolution:** Keep custom (no emoji, conventional format):
```python
    # Build title with optional scope (always use conventional format, no emojis)
    if (scope and guidelines.requires_scope) or scope:
        return f"{cc_type}({scope}): {finding_title.lower()}"
    else:
        return f"{cc_type}: {finding_title.lower()}"
```

### Resolution Strategy

**Rule of Thumb:**
1. **Always keep** custom LLM task setting logic (`set_task()` calls)
2. **Always keep** custom provider initialization
3. **Merge carefully** upstream bug fixes and new features
4. **Remove all** conflict markers before committing
5. **Test thoroughly** after resolution

### Verification Checklist

After resolving conflicts:

```bash
# 1. Check for remaining conflict markers
grep -r "^<<<<<<<\|^>>>>>>>\|^=======" --include="*.py" .

# 2. Verify Python syntax
python -m py_compile contribai/generator/engine.py
python -m py_compile contribai/pr/manager.py
python -m py_compile contribai/github/guidelines.py
python -m py_compile contribai/llm/provider.py

# 3. Test CLI
contribai --help

# 4. Test custom LLM connection (dry-run)
contribai target https://github.com/owner/repo --dry-run

# 5. Check logs for proper model routing
grep "Custom LLM call" ~/.contribai/logs/contribai.log
```

### Detailed Documentation

For comprehensive step-by-step instructions, see:
- **[docs/GIT_PULL_CUSTOM_LLM.md](docs/GIT_PULL_CUSTOM_LLM.md)** - Complete guide with examples

### Emergency Recovery

If something goes wrong:

```bash
# Option 1: Abort and start over
git merge --abort
git stash pop

# Option 2: Reset to before pull
git reset --hard HEAD@{1}
git stash pop

# Option 3: Restore from backup branch
git checkout backup-custom-llm-<date>
```

### Best Practices

1. **Always create a backup branch before pulling:**
   ```bash
   git branch backup-custom-llm-$(date +%Y%m%d)
   ```

2. **Review upstream changes before pulling:**
   ```bash
   git fetch origin
   git log HEAD..origin/main --oneline
   git diff HEAD..origin/main -- contribai/llm/provider.py
   ```

3. **Pull frequently to minimize conflicts:**
   - Small, frequent pulls are easier to resolve than large ones

4. **Document your customizations:**
   - Keep this file updated with any new custom logic
   - Add comments in code explaining custom behavior

5. **Test after every pull:**
   - Run syntax checks
   - Test CLI commands
   - Verify custom LLM endpoints work

### Support

If you encounter issues during pull/merge:
1. Check [docs/GIT_PULL_CUSTOM_LLM.md](docs/GIT_PULL_CUSTOM_LLM.md) for detailed solutions
2. Review this file for conflict patterns
3. Use `git stash show -p` to see your custom changes
4. Ask AI assistant with full context about your custom setup

---

**Last Updated:** 2026-03-28  
**Conflict Resolution Experience:** Successfully resolved 3 conflicts in engine.py, manager.py, and guidelines.py
