# external/

Reference material for the ContribAI architecture refactor (Layers A → B → C → D, see
[`docs/INTEGRATION_PLAN.md`](../docs/INTEGRATION_PLAN.md)).

## What's here

| Submodule | What we want to learn | Notes |
|---|---|---|
| `repos/openhands/` | Agent runtime + automation server (ACP protocol, multi-backend) | [notes/openhands.md](notes/openhands.md) |
| `repos/metagpt/` | Multi-agent Role/Team pattern (SoftwareCompany) | [notes/metagpt.md](notes/metagpt.md) |
| `repos/haystack/` | Pipeline.add_node composition pattern | [notes/haystack.md](notes/haystack.md) |
| `repos/langchain/` | Tool protocol, Runnable interface | [notes/langchain.md](notes/langchain.md) |
| `repos/autogen/` | GroupChat manager pattern | [notes/autogen.md](notes/autogen.md) |
| `repos/crewai/` | Role-based agents (lightweight reference) | [notes/crewai.md](notes/crewai.md) |
| `repos/llamaindex/` | Repo indexing (RAG over code) | [notes/llamaindex.md](notes/llamaindex.md) |
| `repos/swe-agent/` | SWE-bench evaluation harness | [notes/swe-agent.md](notes/swe-agent.md) |

## How to use

After cloning ContribAI:

```bash
git submodule update --init --recursive --depth 1
bash external/scripts/setup.sh         # sparse-checkout where useful
```

The setup script is **idempotent** — running it twice is a no-op.

## Refreshing

```bash
bash external/scripts/refresh.sh       # fast-forward each submodule
```

Each submodule is `shallow = true` and most use `sparse-checkout` for minimal disk
(~600 MB total expected).

## Contract — DO NOT

These submodules are **read-only references**. The adapters live in `contribai/` and
follow this discipline:

- ❌ **Never** `import openhands` from `external/repos/openhands` at runtime.
- ❌ **Never** vendor upstream code into `contribai/`.
- ❌ **Never** make a hard runtime dep on any of these repos in `pyproject.toml` dependencies.
- ✅ **Always** depend on the upstream `PyPI` package or write the adapter from scratch
  (adapters conform to ContribAI's Protocol/ABC surface; see each note for hints).
- ✅ Use this folder to **read** patterns, then write fresh code that borrows ideas.

## What if a submodule moves or breaks

```bash
# Replace a single submodule
git submodule deinit external/repos/<name>
git rm external/repos/<name>
rm -rf .git/modules/external/repos/<name>
git submodule add --depth=1 <url> external/repos/<name>
```

## Memory budget per run

This folder is reproduced fresh on every clone via `git submodule update`. The
shallow + sparse strategy means it does **not** bloat ContribAI's own git history —
each submodule keeps its own.

See [docs/INTEGRATION_PLAN.md](../docs/INTEGRATION_PLAN.md) for how this folder
relates to the code-level refactor.
