# CrewAI

Upstream: https://github.com/crewAIInc/crewAI
Stars: ~33k
Submodule: `external/repos/crewai/` (shallow)

## What we want to learn

- **Lightweight role-based orchestration** — simpler than MetaGPT, good middle-ground
  reference.
- **Sequential and hierarchical process classes** — `Process.sequential` /
  `Process.hierarchical` map cleanly onto our pipeline shape.

## Extension seam in ContribAI

- Reference for `contribai/agents/crewai_adapter.py` (stretch goal) — wrap a `Crew`
  as a `SubAgent`.
- Priority is **lower** than MetaGPT/OpenHands — if we add one multi-agent adapter,
  MetaGPT covers it.

## Status

🟡 Read-only reference. Stretch goal for Layer D.
