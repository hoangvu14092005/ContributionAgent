#!/usr/bin/env bash
# ContribAI launcher — auto-loads .env and runs in the local venv
# Usage: ./run.sh hunt --rounds 1
#        ./run.sh config
#        ./run.sh patrol

set -euo pipefail

# Resolve project root (where this script lives)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Load .env if present (export all vars defined in it)
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

# Activate virtualenv if present
if [[ -d "$ROOT/.venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi

# Run contribai with all arguments
exec contribai "$@"
