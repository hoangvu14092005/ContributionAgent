# No Emoji Policy - ContribAI

## Overview

ContribAI follows a **professional, emoji-free** approach for all PR titles, commit messages, and critical logs. This ensures compatibility with enterprise environments and maintains a serious, professional tone.

## Why No Emojis?

1. **Professional Standards**: Enterprise and serious open-source projects prefer clean, conventional commit formats
2. **Conventional Commits**: Standard format is `type(scope): description` without decorative icons
3. **Automation Friendly**: Tools like semantic-release, changelog generators work better without emojis
4. **Universal Compatibility**: Some systems/terminals don't render emojis correctly

## Emoji Removal Checklist

After pulling upstream changes, search and remove emojis from these critical areas:

### 1. PR Titles & Commit Messages

**Files to check:**
- `contribai/generator/engine.py` - `_generate_pr_title()` method
- `contribai/github/guidelines.py` - `adapt_pr_title()` function
- `contribai/pr/manager.py` - `_human_branch_name()` and title generation

**Common emoji patterns to remove:**
```python
# ❌ BAD - With emojis
type_labels = {
    ContributionType.SECURITY_FIX: "🔒 Security",
    ContributionType.CODE_QUALITY: "✨ Quality",
    ContributionType.DOCS_IMPROVE: "📝 Docs",
    ContributionType.UI_UX_FIX: "🎨 UI/UX",
    ContributionType.PERFORMANCE_OPT: "⚡ Performance",
    ContributionType.FEATURE_ADD: "🚀 Feature",
    ContributionType.REFACTOR: "♻️ Refactor",
}

# ✅ GOOD - Professional format
type_prefixes = {
    ContributionType.SECURITY_FIX: "fix",
    ContributionType.CODE_QUALITY: "refactor",
    ContributionType.DOCS_IMPROVE: "docs",
    ContributionType.UI_UX_FIX: "fix",
    ContributionType.PERFORMANCE_OPT: "perf",
    ContributionType.FEATURE_ADD: "feat",
    ContributionType.REFACTOR: "refactor",
}
```

### 2. PR Body Templates

**Files to check:**
- `contribai/github/guidelines.py` - `_default_pr_body()` and `_fill_pr_template()`
- `contribai/pr/manager.py` - `_generate_pr_body()`

**Remove emoji parameters:**
```python
# ❌ BAD
def _default_pr_body(contribution, emoji: str, label: str, files_list: str):
    return f"## {emoji} {label}\n\n"

# ✅ GOOD
def _default_pr_body(contribution, label: str, files_list: str):
    return f"## Description\n\n"
```

### 3. Logger Messages (PR/Commit Related)

**Files to check:**
- `contribai/pr/manager.py`
- `contribai/generator/engine.py`
- `contribai/orchestrator/pipeline.py`

**Critical log messages to clean:**
```python
# ❌ BAD
logger.info("✅ PR #%d created: %s", pr_number, url)
logger.info("🛠️ Generating fix for: %s", title)
logger.info("📋 Created issue #%d", issue_number)
logger.info("🔗 Found same pattern in %d files", count)
logger.warning("⚠️ PR #%d has issues", pr_number)

# ✅ GOOD
logger.info("PR #%d created: %s", pr_number, url)
logger.info("Generating fix for: %s", title)
logger.info("Created issue #%d", issue_number)
logger.info("Found same pattern in %d files", count)
logger.warning("PR #%d has issues", pr_number)
```

### 4. User-Facing Messages

**Files to check:**
- `contribai/orchestrator/review_gate.py`
- `contribai/cli/main.py`

**Remove from:**
- Panel titles
- Console output
- User prompts

```python
# ❌ BAD
console.print(f"🚀 Starting ContribAI pipeline")
Panel(info, title="[bold]📋 Contribution Details")

# ✅ GOOD
console.print(f"Starting ContribAI pipeline")
Panel(info, title="[bold]Contribution Details")
```

## Quick Search & Replace Guide

### Step 1: Find All Emojis

```bash
# Search for common emojis in Python files
rg "🔒|✨|📝|🎨|⚡|🚀|♻️|🔧|📋|✅|🧠|🛠️|🤖|🔗|🔄|⚠️|✍️" --type py
```

### Step 2: Priority Files (Must Fix)

These files directly affect PR/commit output:

1. **`contribai/generator/engine.py`**
   - Line ~622: `_generate_pr_title()` - Remove emoji labels
   - Line ~276: Warning messages in prompts

2. **`contribai/github/guidelines.py`**
   - Line ~80: `adapt_pr_title()` - Remove emoji fallback
   - Line ~227: `type_info` dict - Remove emoji tuples
   - Line ~250: `_fill_pr_template()` - Remove emoji parameter
   - Line ~320: `_default_pr_body()` - Remove emoji parameter

3. **`contribai/pr/manager.py`**
   - Line ~154: PR creation success log
   - Line ~337: Issue creation log
   - Line ~427: Compliance check logs
   - Line ~492: Auto-fix logs
   - Line ~525: CLA signing logs

4. **`contribai/orchestrator/pipeline.py`**
   - Line ~743: Guidelines log
   - Line ~1008: Fix generation log
   - Line ~1226: Issue solving log

### Step 3: Automated Fix Script

Create a script to automate emoji removal:

```bash
#!/bin/bash
# remove_emojis.sh

# Define files to fix
FILES=(
    "contribai/generator/engine.py"
    "contribai/github/guidelines.py"
    "contribai/pr/manager.py"
    "contribai/orchestrator/pipeline.py"
    "contribai/orchestrator/review_gate.py"
    "contribai/cli/main.py"
)

# Emoji patterns to remove (with space after)
EMOJIS=("🔒 " "✨ " "📝 " "🎨 " "⚡ " "🚀 " "♻️ " "🔧 " "📋 " "✅ " "🧠 " "🛠️ " "🤖 " "🔗 " "🔄 " "⚠️ " "✍️ ")

for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "Processing $file..."
        for emoji in "${EMOJIS[@]}"; do
            # Remove emoji from the file
            sed -i "s/$emoji//g" "$file"
        done
    fi
done

echo "Emoji removal complete!"
```

## Testing After Removal

After removing emojis, verify:

1. **PR Title Format**:
   ```bash
   python -m pytest tests/unit/test_generator.py -k test_pr_title
   ```

2. **Manual Test**:
   ```python
   from contribai.core.models import Finding, ContributionType, Severity
   from contribai.generator.engine import ContributionGenerator
   
   finding = Finding(
       type=ContributionType.SECURITY_FIX,
       severity=Severity.HIGH,
       title="Missing input validation",
       description="Test",
       file_path="src/auth/login.py"
   )
   
   # Should output: "fix(auth): missing input validation"
   # NOT: "🔒 Security: Missing input validation"
   ```

3. **Check Logs**:
   ```bash
   # Run a test and check logs don't contain emojis
   python -m contribai run --dry-run | grep -E "🔒|✨|📝|🎨|⚡|🚀|♻️|🔧"
   # Should return nothing
   ```

## Conventional Commits Reference

Our standard format:

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `style`: Formatting (not CSS)
- `refactor`: Code restructuring
- `perf`: Performance improvement
- `test`: Adding tests
- `chore`: Maintenance tasks
- `ci`: CI/CD changes
- `build`: Build system changes

**Examples:**
```
fix(auth): resolve login timeout issue
feat(payment): add stripe integration
docs(api): update endpoint documentation
perf(database): optimize query performance
refactor(utils): simplify error handling
```

## Maintenance Notes

When merging upstream changes:

1. **Always check** the 4 priority files listed above
2. **Run the search** command to find new emojis
3. **Test PR generation** before committing
4. **Update this doc** if new emoji patterns are found

## Related Files

- `CUSTOM_LLM_CHANGES.md` - Documents other custom modifications
- `GIT_PULL_CHECKLIST.md` - General pull checklist
- `docs/GIT_PULL_CUSTOM_LLM.md` - Custom LLM merge guide

---

**Last Updated**: 2026-03-29
**Maintainer**: ContribAI Team
