# MetaGPT (geekan)

Upstream: https://github.com/geekan/MetaGPT
Stars: ~61.9k
Submodule: `external/repos/metagpt/` (shallow)

## What we want to learn

- **Role-based agents** — `metagpt/roles/` (e.g. `Engineer`, `ProductManager`, `Architect`)
  each inherit from `Role` and stack Actions.
- **`Team` orchestration** — `metagpt/team.py` runs a team of roles around a shared
  environment; this is the pattern ContribAI's `AgentCoordinator` (in `llm/agents.py`)
  could grow into instead of the current linear Analyze → Plan → Generate → Review → Refine.
- **Action protocol** — `metagpt/actions/` defines small, composable units of work.

## Extension seam in ContribAI

| ContribAI seam | MetaGPT reference | Notes |
|---|---|---|
| `contribai/llm/agents.py:32` (`BaseAgent` class) | `metagpt/roles/role.py` | Multi-agent team replaces current `_agents` dict in `AgentCoordinator` |
| `contribai/orchestrator/pipeline.py` | `metagpt/team.py` | Borrow bounded-concurrency DAG (currently we have flat `execute_parallel`) |

## Currently

- ContribAI has only **flat parallel execution** (`AgentRegistry.execute_parallel`,
  semaphore = 3).
- MetaGPT demonstrates DAG-style orchestration: roles can produce artifacts consumed
  by other roles.

## What we WILL NOT do

- ❌ Do not import `metagpt.*` at runtime.
- ❌ Do not reproduce the ~24 roles — start with 3-4 roles we actually need.
- ✅ Layer D adapter will mirror the `Role.execute(message)` shape.

## Status

🟡 Read-only reference. Adapter planned for Layer D, PR #5.
