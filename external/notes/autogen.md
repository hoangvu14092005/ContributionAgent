# AutoGen (Microsoft)

Upstream: https://github.com/microsoft/autogen
Stars: ~52.9k
Submodule: `external/repos/autogen/` (shallow)

## What we want to learn

- **`GroupChat` manager** — orchestrates turns between agents; a model that fits
  between MetaGPT's `Team` (DAG) and our current flat parallel.
- **Conversable agents with human-in-the-loop** — useful pattern for `human_review`
  in ContribAI's `PipelineConfig` (currently a bool, never threaded into the flow).

## Extension seam in ContribAI

| ContribAI seam | AutoGen reference | Notes |
|---|---|---|
| `contribai/llm/agents.py` | `autogen/agentchat/groupchat.py` | Layer D adapter wraps a `GroupChatManager` |
| `contribai/orchestrator/pipeline.py` `human_review` flag | `autogen/agentchat/conversable_agent.py` | Layer D adds `get_human_input()` step |

## Status

🟡 Read-only reference. Stretch goal for Layer D.

See [docs/INTEGRATION_PLAN.md](../../docs/INTEGRATION_PLAN.md) for how all eight
external repos fit the four-layer refactor roadmap.
