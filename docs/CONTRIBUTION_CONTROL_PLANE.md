# Contribution Control Plane

Status: active safety/control-plane architecture for the current `4.1.0`
codebase. The durable queue, workspace/engine contracts, verification/review
proofs, and publish gate are implemented. A proof-complete execution worker is
not yet wired into the legacy discovery pipeline, so LIVE entry points queue a
WorkItem and do not fall back to legacy direct publishing.

## Boundary

```text
OpportunityEngine
       ↓
CommandService → WorkItemRepository
       ↓
WorkspaceManager → clean attempt at one base SHA
       ↓
EngineRouter → EngineDriver
       ↓
EngineOutcome (audit only)
       ↓
PatchCollector → PatchCandidate / CandidateSet
       ↓
VerificationEngine → VerificationReport
       ↓
ReviewService → hash-bound ReviewDecision
       ↓
PublishPermit → GitHubPublisher
```

All CLI, web, MCP, scheduler, and webhook entry points submit work through
`CommandService`. A command is idempotent when its caller supplies an
idempotency key. The WorkItem state machine is the durable source of truth for
resume, cancel, review, and publish-boundary decisions.

## Safety invariants

- `GitHubPublisher` is the only component with GitHub write authority.
- A coding engine receives an `ExecutionLease`, isolated workspace, budget,
  trajectory handle, and optionally a scoped model lease/gateway. It does not
  receive a GitHub client, publisher, raw provider key, SSH credential, or
  Docker socket.
- `EngineOutcome` never contains a patch. `PatchCollector` compares the clean
  before snapshot with the workspace diff after execution and binds the
  candidate to its base SHA and deterministic hash.
- Review decisions bind the exact candidate hash. A changed candidate cannot
  reuse an earlier approval.
- `PublishPermit` binds work id, candidate hash, base SHA, verification proof,
  quota/idempotency proof, expiry, and approved side effects. The publisher
  rejects missing or stale proof.
- N-best attempts use independent workspace snapshots. A retry cannot inherit
  edits from an earlier candidate.

## Engine drivers

`NativeEngineDriver` is the default. Optional drivers all implement the same
`EngineDriver.run(request, execution) -> EngineOutcome` contract:

| Runtime | Boundary | Production note |
|---|---|---|
| Native | In-process | Default implementation |
| mini-SWE-agent | Python binding | Optional `engine-mini-swe` extra |
| OpenHands | SDK | Local workspace inside the outer sandbox; no nested Docker |
| OpenCode | HTTP/server | Policy is defense-in-depth; ContribAI remains final authority |
| Codex exec | CLI process | Bounded JSONL, process-group cancellation, version probe |
| Codex app-server | JSON-RPC | Pinned protocol/capabilities, thread/turn/interrupt mapping |

The adapters do not create candidates or publish. Unsupported SDK versions,
missing binaries, capability drift, expired leases, and absent outer workspaces
fail closed with `EngineStatus.UNSUPPORTED` or a terminal execution outcome.

## Credential and workspace model

`CredentialBroker` retains raw provider keys in the control plane. An engine
may receive only a short-lived `CredentialLease` or opaque `model_gateway`
handle. CLI/server drivers expose the lease through an exact
`CONTRIBAI_MODEL_GATEWAY_*` allow-list; `GITHUB_TOKEN`, `SSH_AUTH_SOCK`, cloud
credentials, and arbitrary API-key variables are scrubbed.

`WorkspaceManager` is the only sandbox authority. Docker workspaces run with
no network by default, bounded CPU/memory/PID resources, a non-root user, no
host socket, and no host credential mounts. OpenHands local workspace mode is
used inside that outer boundary rather than creating Docker-in-Docker.

## Outcome learning and metrics

`OutcomeStore` records accepted, rejected, merged, and closed outcomes with
review latency, requested changes, CI state, cost, wall time, tool failures,
and policy denials. `OutcomeLearner` applies smoothed probabilities only after
the configured evidence threshold; small samples stay close to neutral priors.
`ContributionBenchmark` reports localization recall, patch/verification
rates, cost, wall time, acceptance, merge, review latency, and policy metrics.

## Transitional call paths

The control-plane queueing and publish gate are active. Existing generator,
issue-solver, and patrol implementations still contain legacy logic while
their execution is migrated behind `EngineDriver`, `PatchCollector`, and
`VerificationEngine`. LIVE CLI/Web/Scheduler commands stop after durable queue
submission until that worker is wired; SHADOW and REVIEW_ONLY may execute the
legacy pipeline only with `dry_run=True`. Direct GitHub writes outside
`GitHubPublisher` are blocked by architecture tests. Do not add an
engine-specific publisher or bypass `CommandService` from an entry point.

## Verification commands

```bash
.venv/bin/python -m pytest -q tests/architecture tests/safety tests/execution
.venv/bin/python -m pytest -q tests/engines/contracts tests/engines/adapters
.venv/bin/python -m pytest -q tests/integration/test_entrypoints_control_plane.py
.venv/bin/python -m ruff check contribai/ tests/
.venv/bin/python -m compileall -q contribai
```
