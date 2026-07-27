# LlamaIndex (run-llama)

Upstream: https://github.com/run-llama/llama_index
Stars: ~44k
Submodule: `external/repos/llamaindex/` (shallow + sparse)

## What we want to learn

- **Code indexing over an entire repo** — `llama-index-core` has
  `CodeSplitter` (AST-aware) and `VectorStoreIndex` backends.
- **Query interface over code** — natural-language query against the indexed repo;
  ideal for the issue-solver path (`contribai/issues/solver.py`).

## Extension seam in ContribAI

| ContribAI seam | LlamaIndex reference | Notes |
|---|---|---|
| `contribai/analysis/repo_intel.py` (NEW `RepoIntel` Protocol) | `llama-index-core/indices/` | Adapter exposes `index(repo)` and `similar_findings(finding)` |
| `contribai/issues/solver.py` | `llama-index-core/query_engine/` | Adapter that does NL query against repo + feeds into the solver |

## Why it matters

ContribAI's current `repo_intel` module does keyword/file-tree inspection only. For
large repos (the kind with good-first-issues), semantic indexing would surface
similar past fixes and reduce false positives.

## What we WILL NOT do

- ❌ Do not make `llama-index-core` a hard runtime dep — it's optional.
- ❌ Do not vendor `CodeSplitter` — write our own minimal splitter (or import the
  package in the adapter with a try/except ImportError).
- ✅ The `RepoIntel` Protocol (introduced in Layer D) makes the swap possible
  without changing call-sites.

## Status

🟡 Read-only reference. Adapter planned for Layer D, PR #8.
