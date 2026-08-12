# ADR-0001: Own contribution control and publishing outside coding engines

## Status

Accepted

## Date

2026-08-07

## Context

ContribAI integrates coding runtimes with different execution models: native
Python, in-process SDKs, CLI processes, and JSON-RPC/HTTP servers. Those
runtimes can inspect and edit a repository, but they must not decide whether a
change is verified, reviewed, or published. Giving an engine a GitHub client or
raw provider credentials would make the safety boundary runtime-dependent and
would make retries/N-best attempts difficult to audit.

## Decision

ContribAI owns the control plane:

```text
WorkItem → isolated workspace → EngineDriver → EngineOutcome
         → PatchCollector → Verification → Review → PublishPermit
         → GitHubPublisher
```

`EngineDriver` returns bounded execution evidence only. The control plane
collects the workspace diff and creates `PatchCandidate` values. All external
drivers use the outer `WorkspaceManager` as the sandbox authority and receive
only a scoped model lease or gateway handle. Optional drivers are registered
explicitly and do not replace the Native driver by merely being installed.

## Alternatives considered

### Make every engine return `CandidateSet`

Rejected. CLI and server agents naturally mutate a working tree and do not
share ContribAI's candidate abstraction. Converting their final workspace diff
in the control plane keeps the boundary general and makes the before/after
hash auditable.

### Import external agent source into ContribAI

Rejected. Codex, OpenCode, OpenHands, and mini-SWE have independent release and
runtime lifecycles. Thin adapters isolate version drift and avoid duplicating
security authorities.

### Let each runtime own its sandbox or permission prompts

Rejected. Nested Docker or multiple approval authorities can create capability
gaps and scheduler/reviewer deadlocks. Runtime permissions are defense in
depth; the outer policy and publish gate remain final.

## Consequences

- New runtimes must pass the same contract suite for credentials, workspace,
  cancellation, bounded output, capability pinning, and no-publish behavior.
- Patch collection and verification are common across Native, SDK, CLI, and
  server drivers.
- A model gateway or scoped lease is required for external model access; raw
  provider keys remain in the control plane.
- The legacy pipeline can be migrated incrementally without changing the
  publish authority or WorkItem state machine.
