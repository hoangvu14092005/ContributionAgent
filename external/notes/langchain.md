# LangChain (langchain-ai)

Upstream: https://github.com/langchain-ai/langchain
Stars: ~122k
Submodule: `external/repos/langchain/` (shallow + sparse)

## What we want to learn

- **`Runnable` protocol** — `langchain_core/runnables/base.py` defines a uniform
  `invoke` / `stream` / `batch` interface. Pull this idea into ContribAI's tool
  abstraction.
- **Tool calling convention** — `(name, description, args_schema, invoke)` JSON schema
  metadata; aligns with the existing `Tool` Protocol in `contribai/tools/protocol.py`.

## Extension seam in ContribAI

| ContribAI seam | LangChain reference | Notes |
|---|---|---|
| `contribai/tools/protocol.py` (`Tool` Protocol, line 26) | `langchain/tools/base.py` | Adapter maps LangChain `BaseTool` → ContribAI `Tool` |
| `contribai/mcp_server.py` | `langchain/mcp_adapters/` | MCP tool conversion patterns |

## Why both?

LangChain has the **broadest ecosystem** of integrations (vector DBs, retrievers,
doc-loaders). Adopting the tool-calling convention lets ContribAI users slot in
LangChain components without rewriting.

## What we WILL NOT do

- ❌ Do not depend on `langchain` directly — too heavy as a runtime dep.
- ❌ Do not adopt LangChain Expression Language (LCEL) full grammar — overkill.
- ✅ Take only the `Runnable` interface idea + tool-calling schema.

## Status

🟡 Read-only reference. Adapter planned for Layer D, PR #7.
