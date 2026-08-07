# Contribution Control Plane — Execution Checklist

Checklist này bám theo plan canonical tại docs/superpowers/plans/2026-08-07-contribution-control-plane.md.

## Phase 0 — Live safety

- [x] Task 0: Reconcile baseline và Layer C local.
- [x] Task 1: Capability model và PolicyEngine.
- [x] Task 2: PublishPermit, GitHubPublisher và idempotency.
- [x] Task 3: Explicit execution mode, auth fail-closed và webhook an toàn.
- [x] Task 4: Gộp Human Review và khóa issue side effects.
- [x] Checkpoint 0: Không còn write bypass.

## Phase 1 — Control plane

- [x] Task 5: WorkItem state machine và persistent storage.
- [ ] Task 6: LLMRequest, budget và trajectory.
- [ ] Task 7A: Workspace abstraction và clean attempt snapshots.
- [ ] Task 7B: CredentialBroker và model gateway.
- [ ] Checkpoint 1: State, budget, snapshot và credential contract pass.

## Phase 2 — Coding quality

- [ ] Task 8: ContributionContext, RepoRules và ContextEngine.
- [ ] Task 9: Hierarchical localization.
- [ ] Task 10A: Engine runtime contract (`EngineDriver`/`EngineOutcome`).
- [ ] Task 10B: EngineRouter và `NativeEngineDriver`.
- [ ] Task 10C: `PatchCollector` và candidate assembly.
- [ ] Task 10D: Capability probe và version pinning.
- [ ] Task 11: VerificationEngine và repair feedback loop.
- [ ] Checkpoint 2: Driver, clean snapshots và CandidateSet có evidence xác minh.

## Phase 3 — Product integration

- [ ] Task 12: OpportunityEngine và Issue-first orchestration.
- [ ] Task 13: Persistent ReviewService và dynamic PR review context.
- [ ] Task 14: CommandService và gom entrypoints.
- [ ] Checkpoint 3: Safe end-to-end shadow/review/live flow.

## Phase 4 — Optional expansion

- [ ] Task 15.1: MiniSWEInProcessDriver.
- [ ] Task 15.2: OpenHandsSDKDriver trong outer sandbox.
- [ ] Task 15.3: OpenCodeServerDriver.
- [ ] Task 15.4: CodexExecDriver.
- [ ] Task 15.5: CodexAppServerDriver.
- [ ] Shared engine contract suite: in-process/CLI/server, credential, cancel, diff, budget, no-publish.
- [ ] Task 16: Outcome learning và benchmark.
- [ ] Task 17: CI, packaging, docs và cleanup.
- [ ] Checkpoint 4: Release readiness.
