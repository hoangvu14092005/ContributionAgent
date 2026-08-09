# Integration Plan

Roadmap for folding the cloned upstream references ([external/](../external/README.md)) and
the eight identified external repos into ContribAI. This is the user-facing version of the
internal plan; for code-level specifics see the full plan file referenced at the bottom.

## Why

The Phase 1 audit (April 2026) flagged three zero-test modules, a dormant plugin layer,
hard-coded provider dispatch, and a 48-branch pipeline. Shipping Layer A–D in order
turns these into structural strengths the project can build on.

## External reference repos (already cloned under `external/repos/`)

| Submodule | What we borrow | Adapter target |
|---|---|---|
| OpenHands | ACP runtime, sandbox isolation | `contribai/agents/openhands_adapter.py` |
| MetaGPT | Role-based multi-agent | `contribai/agents/metagpt_adapter.py` |
| Haystack | Pipeline composition | refactor of `contribai/orchestrator/pipeline.py` |
| LangChain | Tool/Runnable protocols | `contribai/tools/langchain_adapter.py` |
| AutoGen | Group chat manager | `contribai/agents/autogen_adapter.py` |
| CrewAI | Lightweight role orchestration | `contribai/agents/crewai_adapter.py` |
| LlamaIndex | Repo indexing (RAG over code) | `contribai/analysis/llamaindex_intel.py` |
| SWE-agent | SWE-bench harness | `contribai/sandbox/swe_eval.py` |

Total disk: ~412 MB after sparse-checkout.

## PR sequence

```
PR #1 — Layer A:    tests + DRY + commit fallback.py        (~3 days)
PR #2 — Layer B:    LLM provider registry + plugin wiring   (~4 days)
PR #3 — Layer C:    pipeline decomposition + step tests     (~1 week)
PR #4 — OpenHands adapter                                    (~2 days each)
PR #5 — MetaGPT adapter
PR #6 — Haystack adapter (after Layer C)
PR #7 — LangChain adapter
PR #8 — LlamaIndex + SWE-agent adapters (stretch)
PR #9 — external/ docs + .gitmodules + scripts (✅ done in initial commit)
```

## Layer A — Tests + DRY (PR #1) — ✅ SHIPPED

**Why first**: 0% test coverage on 1,077 lines is the single biggest near-term risk.
Fixing the four-way duplicate `strip_think_blocks` regex costs ~30 lines.

**Status**: Merged on `main` (commits `beecd7b`, `48d4fb0`, `4b4cea2`).

**Deliverables — all done**:
- ✅ `contribai/core/text_utils.py` — single utility function
- ✅ 4 inline copies replaced across `analyzer.py`, `engine.py`, `patrol.py`, `solver.py`
- ✅ `tests/unit/test_text_utils.py` (~30 LOC)
- ✅ `tests/unit/test_style_validator.py` (~150 LOC)
- ✅ `tests/unit/test_repo_conventions.py` (~200 LOC)
- ✅ `tests/unit/test_fallback.py` (~250 LOC)
- ✅ First commit of `contribai/llm/fallback.py`

**Verification**: `pytest tests/ -v` passes (583 tests after Layer B); coverage on
new modules ≥80%; no more inline `think` regex elsewhere.

## Layer B — Registry Plumbing (PR #2) — ✅ SHIPPED

**Why**: `PluginRegistry` exists but is never imported; `_create_provider_for_slot`
uses an if/elif chain; `AgentCoordinator._agents` is hardcoded. Replacing these with
real registries unlocks every subsequent adapter.

**Status**: Merged on `main` (commit `4b4cea2` and follow-ups).
583 tests pass; 27 new tests cover the registry surface.

**Deliverables — all done**:
- ✅ `LLM_PROVIDERS: dict[str, type[LLMProvider]]` + `@register_provider` decorator
  in [contribai/llm/provider.py](../contribai/llm/provider.py)
- ✅ `register_provider(name)` + `make_provider(name, config)` + `available_providers()`
  exported from [contribai/llm/__init__.py](../contribai/llm/__init__.py)
- ✅ `_create_provider_for_slot` rewritten to call `make_provider(...)`
  (see [contribai/llm/fallback.py](../contribai/llm/fallback.py))
- ✅ `CopilotProvider` decorated `@register_provider("copilot")` and moved
  out of `fallback.py` into `provider.py` (was private `_CopilotProvider`)
- ✅ [contribai/plugins/__init__.py](../contribai/plugins/__init__.py) exposes
  `get_plugin_registry()` singleton, `discover()`, and `reset_plugin_registry()`
  for test isolation
- ✅ [contribai/orchestrator/pipeline.py](../contribai/orchestrator/pipeline.py)
  calls `discover()` and merges plugin analyzers into `CodeAnalyzer`
- ✅ [contribai/llm/agents.py](../contribai/llm/agents.py) `AgentCoordinator.register(name, agent)`,
  `unregister`, `get`, `list_agents` replace the hard-coded dict
- ✅ `CodeAnalyzer(..., plugin_analyzers=[...])` — opt-in plugin pipeline
- ✅ Tests:
  - [tests/unit/test_provider_registry.py](../tests/unit/test_provider_registry.py) (10)
  - [tests/unit/test_plugin_registry.py](../tests/unit/test_plugin_registry.py) (8)
  - [tests/unit/test_agent_coordinator_registry.py](../tests/unit/test_agent_coordinator_registry.py) (9)

**Public API (Layer B)**:

```python
# LLM providers
from contribai.llm import (
    register_provider, make_provider, available_providers, LLM_PROVIDERS,
    CopilotProvider, GeminiProvider, OpenAIProvider, AnthropicProvider,
    OllamaProvider, CustomProvider, MultiModelProvider,
)

@register_provider("my-new-provider")
class MyProvider(LLMProvider):
    ...

provider = make_provider("my-new-provider", config)

# Plugins
from contribai.plugins import (
    get_plugin_registry, discover, reset_plugin_registry,
    AnalyzerPlugin, GeneratorPlugin, PluginRegistry,
)

registry = discover()                       # run entry-point discovery
registry.analyzers                          # list[AnalyzerPlugin]
registry.register_analyzer(MyAnalyzer())    # manual registration

# AgentCoordinator
coord = AgentCoordinator(llm)
coord.register("reviewer", MyReviewerAgent())
coord.unregister("reviewer")
coord.list_agents()                         # ['analyzer', 'codegen', ...]
```

## Layer C — Pipeline Decomposition (PR #3) — ✅ SHIPPED

**Why**: `_process_repo` at 48 branches cannot be safely maintained. Decomposition
borrows Haystack's dict-in/dict-out `Pipeline` concept (we don't import Haystack).

**Status**: Merged on `main` (this commit). 634 tests pass; 51 new tests cover
the orchestrator surface. `pipeline.py` shrank from 1728 → 678 LOC.

**Deliverables — all done**:
- ✅ [contribai/orchestrator/pipeline_constants.py](../contribai/orchestrator/pipeline_constants.py) —
  `PROTECTED_META_FILES`, `SKIP_EXTENSIONS`, `SKIP_DIRECTORIES`, `SkipReason`,
  `_titles_similar`. Re-exported from `pipeline.py` for back-compat.
- ✅ [contribai/orchestrator/pipeline_core.py](../contribai/orchestrator/pipeline_core.py) —
  `PipelineState` (dataclass), `PipelineContext` (frozen dataclass), `Pipeline`
  class with `run(state, ctx)` conductor and short-circuit on `state.skip_reason`.
- ✅ [contribai/orchestrator/steps.py](../contribai/orchestrator/steps.py) —
  5 analysis-mode steps + 2 issue-mode steps + 5 module-level helpers
  (`_is_actionable_finding`, `identify_key_files`, `_fetch_relevant_files`,
  `_dedup_against_past_prs`, `_validate_findings`, `_check_ai_policy`,
  `_check_pr_permissions`, `_check_ci_and_close_if_failed`, `_close_linked_issues`).
  Each step ≤11 branches (soft target was 10).
- ✅ `ContribPipeline._process_repo` and `_process_repo_issues` rewritten as
  ~25-LOC thin orchestrators that build a `PipelineState` + `PipelineContext`
  and run the corresponding pipeline. `_identify_key_files` kept as a 3-LOC
  back-compat shim. `_set_task` kept as a legacy entry point.
- ✅ Tests:
  - [tests/unit/test_orchestrator_steps.py](../tests/unit/test_orchestrator_steps.py) (32)
  - [tests/unit/test_pipeline_core.py](../tests/unit/test_pipeline_core.py) (13)
  - [tests/integration/test_pipeline_e2e.py](../tests/integration/test_pipeline_e2e.py) (4)

**Public API (Layer C)**:

```python
from contribai.orchestrator.pipeline_core import (
    Pipeline, PipelineContext, PipelineState,
)
from contribai.orchestrator.steps import (
    load_repo_context_step, run_analysis_step, validate_findings_step,
    generate_contribution_step, submit_pr_step,
    solve_issue_step, validate_issue_findings_step,
)
from contribai.orchestrator.pipeline_constants import (
    PROTECTED_META_FILES, SKIP_EXTENSIONS, SKIP_DIRECTORIES,
    SkipReason, _titles_similar,
)

# Compose a pipeline:
analysis_pipeline = Pipeline(
    [load_repo_context_step, run_analysis_step, validate_findings_step,
     generate_contribution_step, submit_pr_step],
    label="analysis",
)
state = await analysis_pipeline.run(state, ctx)
```

**Behavior preserved**: existing `tests/unit/test_pipeline_v2.py` (22 tests) and
`tests/integration/test_pipeline.py` (2 tests) pass without modification — the
thin orchestrators produce identical `PipelineResult` for the same inputs.

## Layer D — Adapter Pattern (PRs #4–8) — ⏳ NEXT

Each adapter is a thin shim from the external framework to a ContribAI Protocol:

| Adapter | External | ContribAI seam | Implementation size |
|---|---|---|---|
| OpenHands | ACP runtime | `SubAgent` Protocol | ~120 LOC |
| MetaGPT | Team/Role | `SubAgent` Protocol | ~150 LOC |
| Haystack | Pipeline | `AnalyzerPlugin` ABC | ~80 LOC (after Layer C) |
| LangChain | Tool/Runnable | `Tool` Protocol | ~80 LOC |
| LlamaIndex | Index/Query | `RepoIntel` Protocol (NEW) | ~150 LOC |
| SWE-agent | Eval | `Evaluator` Protocol (NEW) | ~120 LOC |

Each is optional-dep gated (`try: import foo; except ImportError: HAS_FOO = False`)
and ships with its own dedicated test file.

## Decision rules

- An upstream repo is **never imported at runtime**; adapters are written from scratch
  by reading the patterns documented in `external/notes/<repo>.md`.
- A repo can be **dropped from `external/repos/`** if its adapter is no longer needed
  by simply `git submodule deinit && git rm`. Other devs will lose the local clone
  but `setup.sh` re-fetches on demand.
- A new framework is only added if **at least one Protocol change** justifies it; we
  don't grow the adapter zoo for its own sake.

## Risk register

| Risk | Mitigation |
|---|---|
| Submodule drift (upstream rewrite) | Pin to specific commit, use `refresh.sh` for ff-only updates |
| LLM cost spike from adapters evaluating new frameworks | All adapters behind feature flags; `fallback.py` routes to cheap tier first |
| Test flake on respx against real upstream | Adapter tests mock all HTTP — no live calls |
| Pipeline regression after Layer C | Existing `test_pipeline.py` kept; new `test_pipeline_e2e.py` augments |

## Done criteria

- All 5 zero-coverage modules (style_validator, repo_conventions, fallback, plugin
  layer, provider registry) reach ≥80% test coverage
- `radon cc` of `pipeline.py` drops below "C" complexity
- At least 2 of the 6 Layer D adapters (OpenHands, MetaGPT) merged
- `external/repos/` reproducible from a clean `git clone` via `setup.sh`

---

For full technical detail (per-file changes, code snippets, signature-level refactors)
see the internal plan file referenced in `.claude-vscode-minimax/plans/`.
