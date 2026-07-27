# Haystack (deepset-ai)

Upstream: https://github.com/deepset-ai/haystack
Stars: ~24k
Submodule: `external/repos/haystack/` (shallow + sparse)

## What we want to learn

- **Pipeline.add_node** — `haystack/core/pipeline/pipeline.py` builds a DAG from named
  nodes; `Pipeline.run({...})` flows data through edges.
- **Component protocol** — every node implements `run(input: dict) -> dict`.

## Why this matters

`contribai/orchestrator/pipeline.py:_process_repo` currently has **48 cyclomatic
branches**. The method conflates: load repo, run analysis, validate findings, generate
contribution, submit PR. Each is a step; Haystack style lets us split into 5 nodes
and a 5-line `Pipeline.run()` driver.

## Extension seam in ContribAI

| ContribAI file | Haystack reference | Notes |
|---|---|---|
| `contribai/orchestrator/pipeline.py` (1715 lines) | `haystack/core/pipeline/pipeline.py` | Refactor target for Layer C |
| `contribai/plugins/haystack_adapter.py` (NEW, Layer D) | — | Wrap a Haystack `Pipeline` as a ContribAI `AnalyzerPlugin` |

## Planned Layer C refactor

```python
class ContribPipeline:
    async def __call__(self, repo: Repository) -> PipelineResult:
        return await Pipeline([
            self._load_repo_context,    # ~10 branches
            self._run_analysis,         # ~8 branches
            self._validate_findings,     # ~6 branches
            self._generate_contribution, # ~12 branches (was inside _process_repo)
            self._submit_pr,             # ~6 branches
        ]).run({"repo": repo})
```

Each step becomes testable in isolation (Layer C PR #3 ships
`tests/unit/test_orchestrator_steps.py` with one class per step).

## What we WILL NOT do

- ❌ Do not vendor Haystack's `Pipeline` class — write our own tiny `Pipeline` (~30 lines).
- ❌ Do not adopt Haystack's component config YAML format — overkill for our scope.
- ✅ Borrow the **idea** (linear pipeline of dict-in/dict-out async callables).

## Status

🟡 Read-only reference. Layer C refactor planned for PR #3.
