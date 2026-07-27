# OpenHands (All-Hands-AI)

Upstream: https://github.com/All-Hands-AI/OpenHands
Stars: ~82.3k
Submodule: `external/repos/openhands/` (shallow)

## What we want to learn

- **Agent SDK abstraction** — `openhands/sdk/` (formerly `agenthub/`) exposes an agent
  controller that any LLM backend can drive.
- **Runtime / sandbox isolation** — Docker-based sandbox for code execution; we can adopt
  similar for `contribai/sandbox/`.
- **Automation server** — webhook + schedule triggers around agents; relevant to
  `contribai/scheduler/` and `contribai/web/webhooks.py`.

## Extension seam in ContribAI

| ContribAI seam | OpenHands reference | Notes |
|---|---|---|
| `contribai/agents/registry.py` (`SubAgent` Protocol, line 40) | `openhands/agenthub/` | One OpenHands agent class can implement `SubAgent.execute(ctx)` directly |
| `contribai/sandbox/` | `openhands/runtime/` | Borrow Docker sandbox pattern (no direct import — re-implement) |
| `contribai/web/webhooks.py` | `openhands/server/` | Webhook-triggered pipeline runs |

## What we WILL NOT do

- ❌ Do not `import openhands` at runtime.
- ❌ Do not vendor any OpenHands code into `contribai/`.
- ✅ Layer D will write a thin adapter (`contribai/agents/openhands_adapter.py`) that
  conforms to `SubAgent` Protocol — the adapter knows nothing about `external/repos/`.

## Status

🟡 Read-only reference. Adapter planned for Layer D, PR #4.
