# Git Pull Checklist - Custom LLM

> Checklist nhanh khi pull code mới để giữ custom LLM configuration

## ✅ Trước Khi Pull

- [ ] Backup branch: `git branch backup-custom-llm-$(date +%Y%m%d)`
- [ ] Check status: `git status`
- [ ] Review upstream: `git fetch && git log HEAD..origin/main --oneline`

## ✅ Pull Process

```bash
# 1. Stash
git stash push -m "Backup custom LLM config before pull"

# 2. Pull
git pull origin main

# 3. Apply
git stash pop
```

## ✅ Nếu Có Conflicts

### Files Thường Conflict
- [ ] `contribai/generator/engine.py` - Giữ `set_task('code_gen')`
- [ ] `contribai/pr/manager.py` - Giữ logic đơn giản
- [ ] `contribai/github/guidelines.py` - Giữ conventional format

### Resolution Steps
- [ ] Mở file conflict
- [ ] Tìm `<<<<<<<`, `=======`, `>>>>>>>`
- [ ] Giữ phần custom (Stashed changes) có `set_task()`
- [ ] Xóa tất cả conflict markers
- [ ] Save file

## ✅ Verification

```bash
# 1. Check conflict markers
grep -r "^<<<<<<<\|^>>>>>>>\|^=======" --include="*.py" .

# 2. Syntax check
python -m py_compile contribai/generator/engine.py
python -m py_compile contribai/pr/manager.py
python -m py_compile contribai/github/guidelines.py

# 3. Test CLI
contribai --help

# 4. Test custom LLM
contribai target <repo> --dry-run
```

## ✅ Finalize

```bash
# 1. Add resolved files
git add .

# 2. Drop stash
git stash drop

# 3. Check logs
grep "Custom LLM call" ~/.contribai/logs/contribai.log
```

## ✅ Post-Pull Checks

- [ ] No syntax errors
- [ ] CLI runs successfully
- [ ] Config files intact (`config.yaml`, `.env`)
- [ ] Custom LLM endpoints work
- [ ] Model routing correct in logs
- [ ] All tests pass (optional): `pytest tests/`
- [ ] **Emoji removal** - See [docs/NO_EMOJI_POLICY.md](docs/NO_EMOJI_POLICY.md)

## 🔧 Custom Modifications to Reapply

After pulling, reapply these customizations:

### 1. No Emoji Policy
```bash
# Quick check for emojis in critical files
rg "🔒|✨|📝|🎨|⚡|🚀|♻️|🔧" contribai/generator/engine.py contribai/github/guidelines.py contribai/pr/manager.py
```

See full guide: [docs/NO_EMOJI_POLICY.md](docs/NO_EMOJI_POLICY.md)

### 2. Custom LLM Integration
- Verify `set_task()` calls in generator/analyzer
- Check model routing in logs

See full guide: [docs/GIT_PULL_CUSTOM_LLM.md](docs/GIT_PULL_CUSTOM_LLM.md)

## 🆘 If Something Goes Wrong

```bash
# Abort and retry
git merge --abort
git stash pop

# Or reset
git reset --hard HEAD@{1}
git stash pop

# Or restore backup
git checkout backup-custom-llm-<date>
```

## 📚 Full Documentation

See [docs/GIT_PULL_CUSTOM_LLM.md](docs/GIT_PULL_CUSTOM_LLM.md) for detailed guide.

---

**Print this and keep it handy!** 🖨️
