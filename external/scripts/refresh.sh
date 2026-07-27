#!/usr/bin/env bash
# Fast-forward each submodule to its tracked branch tip.
# Idempotent — fails fast if a submodule has diverged (do `cd` in and resolve).

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

git submodule foreach '\
  branch=$(git config --get branch.default 2>/dev/null || echo main); \
  echo "→ $name: pulling origin/$branch"; \
  git pull --ff-only origin "$branch" \
'

date -u +%Y-%m-%dT%H:%M:%SZ > .last_refresh.txt
echo "✅ Refreshed."
