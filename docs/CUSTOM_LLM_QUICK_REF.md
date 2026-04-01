# Custom LLM Quick Reference Card

> Cheat sheet nhanh cho việc sử dụng và maintain custom LLM trong ContribAI

## 🚀 Quick Start

```bash
# 1. Setup
cp .env.example .env
nano .env  # Edit your values

# 2. Configure
echo "llm:
  provider: custom" >> config.yaml

# 3. Run
contribai target https://github.com/owner/repo --dry-run
```

## 📝 Environment Variables

```bash
# Required
export CUSTOM_LLM_BASE_URL="http://localhost:20128/v1"
export CUSTOM_LLM_API_KEY="your_api_key"

# Model routing (optional, có defaults)
export LLM_MODEL_ANALYSIS="ag/gemini-3.1-pro-high"
export LLM_MODEL_CODE_GEN="gh/claude-sonnet-4.6"
export LLM_MODEL_REVIEW="cx/gpt-5.4"
export LLM_MODEL_VALIDATION="kr/claude-sonnet-4.5"
export LLM_MODEL_ISSUE_SOLVER="gh/claude-sonnet-4.6"
export LLM_MODEL_COMPRESSION="gh/claude-sonnet-4.6"
export LLM_MODEL_DEFAULT="gh/claude-sonnet-4.6"
```

## 🔄 Git Pull Workflow

```bash
# Safe pull with custom LLM
git stash push -m "Backup custom LLM"
git pull origin main
git stash pop

# If conflicts occur:
# 1. Keep custom task setting logic (set_task calls)
# 2. Remove conflict markers (<<<<<<<, =======, >>>>>>>)
# 3. Test: python -m py_compile <file>
# 4. Verify: contribai --help
```

## 🎯 Task-to-Model Mapping

| Task | Default Model | When Used |
|------|---------------|-----------|
| `analysis` | `ag/gemini-3.1-pro-high` | Code analysis |
| `code_gen` | `gh/claude-sonnet-4.6` | Generate fixes |
| `review` | `cx/gpt-5.4` | Self-review |
| `validation` | `kr/claude-sonnet-4.5` | Validate findings |
| `issue_solver` | `gh/claude-sonnet-4.6` | Solve issues |
| `compression` | `gh/claude-sonnet-4.6` | Compress context |
| `default` | `gh/claude-sonnet-4.6` | Other operations |

## 🔍 Debugging

```bash
# Check model routing in logs
grep "Custom LLM call" ~/.contribai/logs/contribai.log

# Test syntax
python -m py_compile contribai/llm/provider.py

# Verify config
contribai config

# Test connection
contribai target <repo> --dry-run
```

## 📂 Key Files

### Must Keep Custom
- `contribai/llm/provider.py` - Custom provider
- `contribai/core/config.py` - Config with custom fields
- `config.yaml` - Your local config
- `.env` - Your credentials

### May Have Conflicts
- `contribai/generator/engine.py` - Task: code_gen
- `contribai/analysis/analyzer.py` - Task: analysis
- `contribai/orchestrator/pipeline.py` - Task: validation
- `contribai/issues/solver.py` - Task: issue_solver
- `contribai/analysis/context_compressor.py` - Task: compression

## 🛠️ Common Commands

```bash
# Run with custom LLM
contribai target <repo> --dry-run

# Hunt mode
contribai hunt --max-repos 5

# Solve specific issue
contribai solve <repo> --issue 123

# Check status
contribai status

# View config
contribai config
```

## ⚠️ Conflict Resolution Patterns

### Pattern: Task Setting
```python
# KEEP THIS (custom)
if hasattr(self._llm, 'set_task'):
    self._llm.set_task('code_gen')

response = await self._llm.complete(prompt, system=system)
```

### Pattern: Provider Init
```python
# KEEP THIS (custom)
if config.llm.provider == "custom":
    return CustomProvider(config.llm)
```

## 📚 Full Documentation

- **Setup:** [docs/CUSTOM_LLM_SETUP.md](CUSTOM_LLM_SETUP.md)
- **Git Workflow:** [docs/GIT_PULL_CUSTOM_LLM.md](GIT_PULL_CUSTOM_LLM.md)
- **Changes Log:** [CUSTOM_LLM_CHANGES.md](../CUSTOM_LLM_CHANGES.md)

## 🆘 Emergency Commands

```bash
# Abort merge
git merge --abort

# Reset to before pull
git reset --hard HEAD@{1}

# Restore from backup
git checkout backup-custom-llm-<date>

# View stashed changes
git stash show -p stash@{0}
```

## ✅ Post-Pull Checklist

- [ ] No conflict markers in code
- [ ] Python syntax valid
- [ ] CLI runs: `contribai --help`
- [ ] Config intact: `contribai config`
- [ ] Custom LLM works: test with `--dry-run`
- [ ] Logs show correct models

---

**Quick Help:** For any issues, check [GIT_PULL_CUSTOM_LLM.md](GIT_PULL_CUSTOM_LLM.md) first!
