# ContribAI — Target Architecture v2

After Layers A → D land, ContribAI's module graph looks like this. ASCII-first because
[docs/ARCHITECTURE.md](ARCHITECTURE.md) is the v1 diagram; this is the planned v2.

## Delivery status

| Layer | Scope | Status | Tests added |
|---|---|---|---|
| A | Tests + DRY (`text_utils`, `fallback.py` first commit) | ✅ Shipped | ~97 |
| B | Registry plumbing (LLM, plugins, agents) | ✅ Shipped | 27 |
| C | Pipeline decomposition (5 steps + `pipeline_core`) | ✅ Shipped | 51 |
| D | Adapters (OpenHands, MetaGPT, Haystack, LangChain, LlamaIndex, SWE-agent) | ⏳ Next | TBD |

**Layer A**: `beecd7b`, `48d4fb0`, `4b4cea2`. See [INTEGRATION_PLAN.md § Layer A](INTEGRATION_PLAN.md#layer-a--tests--dry-pr-1--shipped).
**Layer B**: see [INTEGRATION_PLAN.md § Layer B](INTEGRATION_PLAN.md#layer-b--registry-plumbing-pr-2--shipped) for the full deliverable list and code links.

## High-level

```
                          ┌────────────────────────────────────────────┐
                          │            contribai.cli.main             │
                          │  hunt / target / solve / patrol / serve   │
                          └─────────────────────┬──────────────────────┘
                                                │
            ┌───────────────────────────────────▼─────────────────────────────┐
            │              contribai.orchestrator.pipeline                    │
            │   ContribPipeline ─▶ Pipeline (5 steps in steps.py)              │
            │       load_repo_context → run_analysis → validate_findings      │
            │                        → generate_contribution → submit_pr       │
            └─────────┬────────────────┬──────────────────┬─────────────────────┘
                      │                │                  │
        ┌─────────────▼─────┐  ┌───────▼────────┐  ┌─────▼────────────┐
        │ contribai.agents │  │ contribai.   │  │ contribai.github │
        │   AgentRegistry  │  │   llm         │  │   client         │
        │   + adapters:    │  │   provider    │  │   + discovery    │
        │   OpenHandsAdp   │  │   registry    │  │                  │
        │   MetaGPTAdp     │  │   ┌─────────┐ │  │                  │
        │   CrewAIAdp      │  │   │gemini   │ │  │                  │
        │   AutoGenAdp     │  │   │openai   │ │  │                  │
        │   (each =        │  │   │anthropic│ │  │                  │
        │    SubAgent)     │  │   │ollama   │ │  │                  │
        └──────────────────┘  │   │custom   │ │  │                  │
                             │   │copilot  │ │  │                  │
        ┌──────────────────┐  │   └─────────┘ │  │                  │
        │ contribai.plugins│  │   + MultiModel│  │                  │
        │   PluginRegistry │  │   + Fallback  │  │                  │
        │   (entry-points) │  └───────────────┘  │                  │
        │   + adapters:    │                    │                  │
        │   HaystackAdp    │                    │                  │
        │   LlamaIndexAdp  │                    │                  │
        └──────────────────┘                    │                  │
                                                │                  │
        ┌───────────────────────────────────────▼──────────────────▼──────────┐
        │              contribai.analysis                                       │
        │   CodeAnalyzer (default)  ←→  RepoIntel (NEW Protocol)               │
        │   RepoConventions                                                         │
        │   StyleValidator                                                          │
        │   skills/*  (17+ skills, language-aware)                                 │
        │   repo_intel.llamaindex  (optional, Layer D PR #8)                       │
        └──────────────────────────────────────────────────────────────────────────┘

        ┌──────────────────────────────────────────────────────────────────────────┐
        │              contribai.generator                                         │
        │   ContributionGenerator  +  QualityScorer  +  StyleValidator           │
        │   (orchestrator pipeline step 4 wraps these)                             │
        └──────────────────────────────────────────────────────────────────────────┘

        ┌──────────────────────────────────────────────────────────────────────────┐
        │              contribai.sandbox  +  Evaluator (NEW Protocol)             │
        │   DockerSandbox (default)  ←→  SWE-agent Eval (Layer D)                  │
        └──────────────────────────────────────────────────────────────────────────┘
```

## Module map (after v2)

| Module | v2 responsibility | New in v2 |
|---|---|---|
| `core/text_utils.py` | DRY utilities (think-strip) | ✅ |
| `core/config.py` | Same | — |
| `agents/registry.py` | Sub-agent Protocol + parallel dispatch | (unchanged) |
| `agents/<framework>_adapter.py` | Adapter per external framework | ✅ |
| `llm/provider.py` | `LLMProvider` ABC + `@register_provider` + `LLM_PROVIDERS` dict | refactor |
| `llm/fallback.py` | Multi-provider fallback chain (was if/elif) | refactor + commit |
| `llm/agents.py` | `AgentCoordinator.register(name, agent)` | refactor |
| `tools/protocol.py` | `Tool` Protocol + `ToolRegistry` | (unchanged) |
| `tools/<framework>_adapter.py` | Optional adapters | ✅ |
| `plugins/base.py` | `PluginRegistry`, ABCs | (unchanged) |
| `plugins/__init__.py` | `get_plugin_registry()` singleton | ✅ |
| `plugins/<framework>_adapter.py` | Optional adapters | ✅ |
| `analysis/repo_intel.py` | `RepoIntel` Protocol (NEW) | ✅ |
| `analysis/llamaindex_intel.py` | Optional adapter | ✅ |
| `orchestrator/pipeline_core.py` | Tiny `Pipeline` class (Haystack-style) | ✅ |
| `orchestrator/steps.py` | 5 step functions | ✅ |
| `orchestrator/pipeline.py` | `ContribPipeline` delegates to steps | refactor |
| `sandbox/__init__.py` | `Evaluator` Protocol (NEW) | ✅ |
| `sandbox/swe_eval.py` | Optional SWE-bench adapter | ✅ |
| `external/repos/<8 submodules>` | Read-only reference repos | ✅ |
| `external/notes/<8 .md>` | Per-repo extension point documentation | ✅ |
| `external/scripts/setup.sh`, `refresh.sh` | Submodule lifecycle | ✅ |
| `.gitmodules` | 8 submodules tracked at root | ✅ |

## Data flow (v2)

The pipeline now runs through 5 named steps with a dict-shaped state, mirroring
Haystack's Pipeline concept without depending on it.

```python
state0 = {"repo": Repository(...), "config": config, ...}
state1 = await load_repo_context_step(state0)      # add: files, tree
state2 = await run_analysis_step(state1)           # add: skills output
state3 = await validate_findings_step(state2)      # reduce: filter false positives
state4 = await generate_contribution_step(state3)  # add: Contribution + QualityReport
state5 = await submit_pr_step(state4)              # produce: PR URL + memory
```

Each step:
- Reads from `state`, writes back to same dict
- Has ≤10 cyclomatic branches (verifiable with `radon cc`)
- Has a dedicated `tests/unit/test_orchestrator_steps.py::Test<Step>` class

## Concurrency model

- **Sub-agent parallel**: `AgentRegistry.execute_parallel` (semaphore=3, unchanged)
- **Pipeline steps**: sequential (each depends on previous output)
- **Discovery (one-shot at init)**: `PluginRegistry.discover()` is idempotent
  and guarded by `_loaded` flag

## Extension contract

Every external framework follows the **adapter + Protocol** rule:

1. Define a ContribAI Protocol (`AnalyzerPlugin`, `Tool`, `SubAgent`,
   `RepoIntel`, `Evaluator`).
2. Write a thin adapter that conforms to the Protocol.
3. Wrap the import in `try: import foo; except ImportError: HAS_FOO = False`.
4. Add the adapter to the appropriate registry via `register_*` decorator or method.
5. Ship the test file mirroring the source structure:
   `tests/unit/test_<adapter>.py`.

No code outside `contribai/` is imported at runtime. The `external/repos/<x>/`
folders are strictly for **reading** implementation patterns.

## Layer B — Registry plumbing (delivered)

After Layer B, ContribAI has three pluggable registries instead of three
hard-coded dispatch tables.

### LLM provider registry

```python
from contribai.llm import (
    register_provider, make_provider, available_providers, LLM_PROVIDERS,
)

# Built-in (registered at import time via @register_provider decorator):
#   gemini, openai, anthropic, ollama, custom, copilot

# Add a new one:
@register_provider("my-endpoint")
class MyProvider(LLMProvider):
    async def complete(self, prompt, *, system=None, temperature=None, max_tokens=None):
        ...

# Look it up:
provider = make_provider("my-endpoint", config)
```

`create_llm_provider()` (the high-level factory in
[contribai/llm/provider.py](../contribai/llm/provider.py)) and
`_create_provider_for_slot()` (in
[contribai/llm/fallback.py](../contribai/llm/fallback.py)) both go through
`make_provider()` — so any `@register_provider` addition is automatically
usable from the top-level config and from every fallback chain slot.

`CopilotProvider` was lifted out of `fallback.py` and registered under
`"copilot"`; the slot factory uses a small routing table to map free-form
slot names (`"rocket-free-2"`, `"kilo"`, `"copilot"`, …) onto registered
provider classes.

### Plugin registry singleton

```python
from contribai.plugins import discover, get_plugin_registry, reset_plugin_registry

# Pipeline calls this once at init; entry-point discovery is idempotent.
registry = discover()

# Manual registration (e.g. in tests or for embedded plugins):
registry.register_analyzer(MyAnalyzer())

# List currently-loaded plugins:
for plugin in registry.analyzers:
    print(plugin.name)
```

`discover()` triggers entry-point discovery on the process-wide singleton.
`reset_plugin_registry()` is a test-only escape hatch — the singleton is
replaced on the next `get_plugin_registry()` call so test cases stay isolated.

`CodeAnalyzer(plugin_analyzers=[...])` accepts the discovered plugins and
runs them in parallel with the LLM-powered analyzers. Pipeline init
populates this argument via `discover()`.

### AgentCoordinator registry

```python
coord = AgentCoordinator(llm_provider)

# Default agents installed (analyzer, codegen, reviewer, docs, planner)
coord.list_agents()        # ['analyzer', 'codegen', 'reviewer', 'docs', 'planner']

# Replace one:
my_reviewer = MyReviewerAgent(llm, coord._router)
coord.register("reviewer", my_reviewer)

# Or build empty:
empty = AgentCoordinator(llm_provider, register_defaults=False)
empty.register("custom", MyAgent(llm_provider, ...))
```

The `register / unregister / get / list_agents` API replaces the
hard-coded `_agents` dict that the v1 coordinator used.

## Why this is better than v1

| Aspect | v1 | v2 |
|---|---|---|
| Test coverage on new modules | 0% | ≥80% |
| DRY: `strip_think_blocks` copies | 4 | 1 |
| LLM provider registration | if/elif string | `@register_provider` decorator |
| Plugin layer | dormant | wired into pipeline init |
| Pipeline complexity (`_process_repo`) | 48 branches | 5 steps × ≤10 each |
| Multi-agent adapters | none | 6 ready (OpenHands, MetaGPT, ...) |
| External repo references | none | 8 submodules, ~412 MB |
| Cost: change a contributor integration | edit 3 files | add adapter + decorator |

For the per-PR implementation detail, see [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md).
