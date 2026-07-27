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

## Layer A — Tests + DRY (PR #1)

**Why first**: 0% test coverage on 1,077 lines is the single biggest near-term risk.
Fixing the four-way duplicate `strip_think_blocks` regex costs ~30 lines.

**Deliverables**:
- `contribai/core/text_utils.py` — single utility function
- 4 inline copies replaced across `analyzer.py`, `engine.py`, `patrol.py`, `solver.py`
- `tests/unit/test_text_utils.py` (~30 LOC)
- `tests/unit/test_style_validator.py` (~150 LOC)
- `tests/unit/test_repo_conventions.py` (~200 LOC)
- `tests/unit/test_fallback.py` (~250 LOC)
- First commit of `contribai/llm/fallback.py`

**Verification**: `pytest tests/ -v` passes; coverage on new modules ≥80%; no more
inline `think` regex elsewhere.

## Layer B — Registry Plumbing (PR #2)

**Why**: `PluginRegistry` exists but is never imported; `_create_provider_for_slot`
uses an if/elif chain; `AgentCoordinator._agents` is hardcoded. Replacing these with
real registries unlocks every subsequent adapter.

**Deliverables**:
- `LLM_PROVIDERS: dict[str, type[LLMProvider]]` + `@register_provider` decorator in
  `contribai/llm/provider.py`
- `register_provider(name)` + `make_provider(name, config)` exported from
  `contribai/llm/__init__.py`
- `_create_provider_for_slot` rewritten to call `make_provider(...)`
- `_CopilotProvider` decorated `@register_provider("copilot")`
- `contribai/plugins/__init__.py` exposes `get_plugin_registry()` singleton + `discover()`
- `contribai/orchestrator/pipeline.py` calls `discover()` and merges plugin analyzers
- `contribai/llm/agents.py` `AgentCoordinator.register(name, agent)` replaces the
  hard-coded dict
- `tests/unit/test_provider_registry.py`, `tests/unit/test_plugin_registry.py`

## Layer C — Pipeline Decomposition (PR #3)

**Why**: `_process_repo` at 48 branches cannot be safely maintained. Decomposition
borrows Haystack's dict-in/dict-out `Pipeline` concept (we don't import Haystack).

**Deliverables**:
- `contribai/orchestrator/steps.py` — 5 step functions, each ≤10 branches:
  1. `load_repo_context_step`
  2. `run_analysis_step`
  3. `validate_findings_step`
  4. `generate_contribution_step`
  5. `submit_pr_step`
- `contribai/orchestrator/pipeline_core.py` — tiny `Pipeline` class
- Refactor of `ContribPipeline.__call__` to use the new steps
- `tests/unit/test_orchestrator_steps.py` — one class per step, ≥80% coverage
- `tests/integration/test_pipeline_e2e.py` — full linear flow with mocks

## Layer D — Adapter Pattern (PRs #4–8)

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
