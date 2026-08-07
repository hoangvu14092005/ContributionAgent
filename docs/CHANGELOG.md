# Changelog

Per-layer changelog for the [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) refactor
(April → July 2026). The full plan is in INTEGRATION_PLAN.md; this file records
**what actually shipped** per layer, with file references and test counts.

## Layer B — Registry plumbing — ✅ shipped (July 2026)

Replaces three hard-coded dispatch tables with pluggable registries.

### What changed

| File | Change |
|---|---|
| `contribai/llm/provider.py` | Added `LLM_PROVIDERS` dict, `@register_provider` decorator (decorator + imperative forms), `make_provider()`, `available_providers()`. Decorated `GeminiProvider`, `OpenAIProvider`, `AnthropicProvider`, `OllamaProvider`, `CustomProvider`. Moved `CopilotProvider` in from `fallback.py` and decorated `@register_provider("copilot")`. `create_llm_provider()` now goes through `make_provider()`. |
| `contribai/llm/__init__.py` | Re-exports `LLMProvider`, `LLM_PROVIDERS`, `register_provider`, `make_provider`, `available_providers`, every provider class, `MultiModelProvider`, `create_llm_provider`. |
| `contribai/llm/fallback.py` | Dropped private `_CopilotProvider` and `_resolve_gh_auth_token()`. `_create_provider_for_slot()` now delegates to `make_provider()` via a small routing table (`_OPENAI_COMPATIBLE_SLOT_NAMES`, `_KNOWN_NATIVE_PROVIDERS`). Unknown slot names fall back to `"custom"` with a debug log. |
| `contribai/plugins/__init__.py` | Added module-level singleton: `get_plugin_registry()`, `discover()`, `reset_plugin_registry()`. Re-exports `AnalyzerPlugin`, `GeneratorPlugin`, `PluginRegistry`. |
| `contribai/analysis/analyzer.py` | `CodeAnalyzer.__init__` accepts new `plugin_analyzers: list \| None = None`. `analyze()` runs each plugin in parallel with the LLM analyzers and merges findings. Plugin exceptions surface in logs without crashing the pipeline. |
| `contribai/orchestrator/pipeline.py` | `_init_components()` calls `discover()` and passes `plugin_registry.analyzers` into `CodeAnalyzer`. |
| `contribai/llm/agents.py` | `AgentCoordinator` now uses `register(name, agent)`, `unregister(name)`, `get(name)`, `list_agents()`. Defaults installed via `_default_agent_for()` helper keyed by `DEFAULT_AGENT_NAMES`. New `register_defaults=False` opt-out. |

### Tests added (27 new, all green)

| File | Tests | Coverage |
|---|---|---|
| `tests/unit/test_provider_registry.py` | 10 | `@register_provider` decorator + imperative forms, `make_provider` happy + error path, round-trip for every built-in, `LLM_PROVIDERS` contents |
| `tests/unit/test_plugin_registry.py` | 8 | Singleton identity, `reset_plugin_registry` cache busting, `discover()` returns the registry, manual `register_analyzer`, running a plugin against a real `RepoContext` |
| `tests/unit/test_agent_coordinator_registry.py` | 9 | Default agents installed, expected types per slot, `register_defaults=False`, replace existing, unregister, insertion-order preservation |

### Verification

```
$ .venv/bin/pytest tests/unit/ -q
583 passed, 2 pre-existing failures (test_pr_manager.py::TestPRBody — unrelated)
```

## Layer C — Pipeline decomposition — ✅ shipped (July 2026)

Replaces the 48-branch `_process_repo` monolith with a Haystack-style 5-step
pipeline. Issue-driven mode reuses 3/5 steps.

### What changed

| File | Change |
|---|---|
| `contribai/orchestrator/pipeline_constants.py` | **NEW**. `PROTECTED_META_FILES`, `SKIP_EXTENSIONS`, `SKIP_DIRECTORIES`, `SkipReason` Literal, `_titles_similar`. Re-exported from `pipeline.py` for back-compat with tests. |
| `contribai/orchestrator/pipeline_core.py` | **NEW**. `PipelineState` (mutable dataclass threaded through steps), `PipelineContext` (frozen dataclass holding collaborators + `set_task`), `Pipeline` class (`run(state, ctx)` chains steps; checks `state.skip_reason` after each step and short-circuits). |
| `contribai/orchestrator/steps.py` | **NEW**. 5 analysis-mode steps (`load_repo_context_step`, `run_analysis_step`, `validate_findings_step`, `generate_contribution_step`, `submit_pr_step`) + 2 issue-mode steps (`solve_issue_step`, `validate_issue_findings_step`). Module-level helpers: `identify_key_files`, `_is_actionable_finding`, `_fetch_relevant_files`, `_dedup_against_past_prs`, `_validate_findings`, `_check_ai_policy`, `_check_pr_permissions`, `_check_ci_and_close_if_failed`, `_close_linked_issues`. |
| `contribai/orchestrator/pipeline.py` | **REFACTOR**. `_process_repo` and `_process_repo_issues` rewritten as ~25-LOC thin orchestrators that build a `PipelineState` + `PipelineContext` and run the corresponding `Pipeline`. Module shrank from 1728 → 678 LOC. `_identify_key_files` kept as a 3-LOC back-compat shim. |

### Tests added (51 new, all green)

| File | Tests | Coverage |
|---|---|---|
| `tests/unit/test_orchestrator_steps.py` | 32 | One test class per step: pre-filter, dedup, validation, generation, submit. AI policy / PR permissions / no-findings short-circuits. `closes_issues` parallel list propagated through issue mode. |
| `tests/unit/test_pipeline_core.py` | 13 | `Pipeline` conductor: order, short-circuit, exception propagation, `state.result.repos_analyzed=1`, defensive copy of `steps`, `set_task` no-op on non-MultiModel providers. |
| `tests/integration/test_pipeline_e2e.py` | 4 | Full `ContribPipeline._process_repo` and `_process_repo_issues` against the step-based implementation: analysis mode, dry-run, AI policy skip, issue mode with `closes_issue` propagation. |

### Verification

```
$ .venv/bin/pytest tests/unit/ tests/integration/ -q
634 passed, 2 pre-existing failures (test_pr_manager.py::TestPRBody — unrelated)

# pipeline.py shrank from 1728 → 678 LOC (61% reduction)
$ wc -l contribai/orchestrator/pipeline.py contribai/orchestrator/steps.py \
        contribai/orchestrator/pipeline_core.py contribai/orchestrator/pipeline_constants.py
   678 contribai/orchestrator/pipeline.py
   977 contribai/orchestrator/steps.py
   191 contribai/orchestrator/pipeline_core.py
    89 contribai/orchestrator/pipeline_constants.py
  1935 total
```

Cyclomatic budget per step (target ≤10):

| Function | Branches | Notes |
|---|---|---|
| `load_repo_context_step` | 11 | 1 over (acceptable; soft target) |
| `run_analysis_step` | 6 | |
| `validate_findings_step` | 6 | |
| `generate_contribution_step` | 6 | |
| `submit_pr_step` | 5 | |
| `solve_issue_step` | 13 | 3 over (issue mode is naturally more complex) |
| `validate_issue_findings_step` | 3 | |

## Layer A — Tests + DRY — ✅ shipped (April–July 2026)

Fixes 0% test coverage on 1,077 lines and the four-way duplicate
`strip_think_blocks` regex.

### What changed

- `contribai/core/text_utils.py` — single utility (`strip_think_blocks`).
- 4 inline copies replaced across `analyzer.py`, `engine.py`, `patrol.py`, `solver.py`.
- First commit of `contribai/llm/fallback.py` (`FallbackChainProvider`,
  `ProviderSlot`, `build_fallback_chains`, `_resolve_gh_auth_token`).
- `tests/unit/test_text_utils.py` (~30 LOC)
- `tests/unit/test_style_validator.py` (~150 LOC)
- `tests/unit/test_repo_conventions.py` (~200 LOC)
- `tests/unit/test_fallback.py` (~250 LOC)

### Verification

`pytest tests/ -v` passes; coverage on new modules ≥80%; no more inline
`think` regex anywhere else in the codebase.

## Layer C — Pipeline decomposition — ⏳ next

5 step functions (`load_repo_context`, `run_analysis`, `validate_findings`,
`generate_contribution`, `submit_pr`), each ≤10 branches, plus a tiny
`Pipeline` class in `contribai/orchestrator/pipeline_core.py`.

## Layer D — Adapter pattern — ⏳ pending

OpenHands, MetaGPT, Haystack, LangChain, LlamaIndex, SWE-agent. Each is an
optional-dep gated adapter registered through the Layer B registries.
