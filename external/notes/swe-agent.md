# SWE-agent (Princeton NLP)

Upstream: https://github.com/princeton-nlp/SWE-agent
Stars: ~3.5k
Submodule: `external/repos/swe-agent/` (shallow + sparse)

## What we want to learn

- **SWE-bench evaluation harness** — `sweagent/environment/` + `sweagent/run_*` show
  how to drive an LLM agent through real GitHub issues.
- **Patch validation** — running tests against the candidate patch in a sandboxed
  environment; relevant to `contribai/sandbox/`.

## Extension seam in ContribAI

| ContribAI seam | SWE-agent reference | Notes |
|---|---|---|
| `contribai/sandbox/` | `sweagent/environment/swe_env.py` | Adapter for SWE-bench style evaluation |
| `contribai/generator/scorer.py` | `sweagent/run_single.py` | Could swap heuristic checks for SWE-agent's test-driven scoring |

## What we WILL NOT do

- ❌ Do not vendor SWE-agent's environment code.
- ❌ Do not require SWE-bench Docker images as a hard runtime dep.
- ✅ The optional `Evaluator` Protocol (NEW in Layer D) lets us swap the current
  heuristic scorer for a SWE-bench-driven one without code changes elsewhere.

## Status

🟡 Read-only reference. Adapter planned for Layer D, stretch goal.
