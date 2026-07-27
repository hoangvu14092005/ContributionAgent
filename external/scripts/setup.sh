#!/usr/bin/env bash
# Initialize all submodules + apply sparse-checkout where useful.
# Idempotent — safe to run multiple times.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

# Ensure git is at the version that supports sparse-checkout (>=2.25)
git --version

echo "→ git submodule update --init --recursive --depth 1"
git submodule update --init --recursive --depth 1

# Apply sparse-checkout for the heavy repos.
declare -A SPARSE=(
  [repos/haystack]="haystack/core/pipeline haystack/components"
  [repos/langchain]="libs/core libs/community/langchain"
  [repos/llamaindex]="llama-index-core"
  [repos/swe-agent]="sweagent README.md"
)
for path in "${!SPARSE[@]}"; do
  if [[ -d "$path" ]]; then
    echo "→ sparse-checkout $path: ${SPARSE[$path]}"
    (cd "$path" && git sparse-checkout set --no-cone ${SPARSE[$path]}) || true
  fi
done

date -u +%Y-%m-%dT%H:%M:%SZ > .last_refresh.txt
echo "✅ Submodules ready. See ../README.md."
