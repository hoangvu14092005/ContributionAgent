# Contribution Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Chuyển ContribAI từ một coding bot có nhiều đường side effect thành một autonomous open-source contribution control plane: chọn đúng cơ hội, chạy coding engine trong workspace cô lập, xác minh bằng bằng chứng, review, publish qua một authority duy nhất và học từ outcome.

**Architecture:** Giữ modular monolith chạy trên asyncio và SQLite single-node. Tách control plane khỏi coding engine: mọi entrypoint tạo WorkItem, ExecutionSupervisor điều phối workspace/engine/verification/review, còn duy nhất GitHubPublisher được phép thực hiện GitHub write. EngineDriver chỉ điều khiển một execution trong workspace và trả EngineOutcome; PatchCollector của control plane mới thu diff và tạo CandidateSet. PublishPermit là điều kiện bắt buộc trước mọi side effect.

**Tech Stack:** Python 3.11+, asyncio, httpx/aiohttp, Pydantic, aiosqlite/SQLite, FastAPI, Click/Typer/Rich, pytest, ruff, Docker tùy chọn nhưng bắt buộc đối với live repository execution.

## Global Constraints

- Baseline triển khai là origin/main commit ce7af0d; các thay đổi local chưa commit như pipeline_core.py, steps.py và test Layer C phải được reconcile trước khi dùng.
- Giữ Python >=3.11, async I/O cho network/process/database, và line length 100 theo pyproject.toml.
- Không tách microservice trong roadmap này; SQLite vẫn là storage mặc định cho single-node.
- Không fork hoặc sao chép nguyên vẹn Codex, OpenCode, OpenHands, mini-SWE-agent, Agentless, Aider, AutoCodeRover, PR-Agent hay Continue.
- Chỉ GitHubPublisher được giữ capability github.push, github.create_issue, github.create_pr, github.comment, github.close_pr.
- Coding engine và workspace không được nhận GITHUB_TOKEN, SSH agent, Docker socket hoặc credential host. Model credential không được truyền dưới dạng raw provider key; external engine chỉ nhận scoped model lease qua CredentialBroker/model gateway.
- WorkspaceManager của ContribAI là sandbox authority duy nhất. Sandbox hoặc permission system bên trong Codex/OpenCode/OpenHands chỉ là defense-in-depth; không được cấp thêm host capability, Docker socket hoặc quyền publish.
- Mỗi engine attempt phải bắt đầu từ snapshot sạch của cùng một base SHA. Retry/N-best không được dùng chung working tree đã bị attempt trước làm bẩn.
- Engine driver phải chạy được trong non-interactive mode. Permission request của backend được chuẩn hóa thành policy decision hoặc WAITING_FOR_APPROVAL; không để engine, scheduler và reviewer chờ vòng nhau.
- shadow/review_only là mặc định; live publish phải có mode explicit, policy decision, verification proof, review proof, quota reservation và idempotency key.
- Không đóng issue upstream tự động. Chỉ được đóng issue do ContribAI tạo và có policy explicit cho phép.
- Không coi LLM self-review là authority cuối cùng; lỗi không xác minh được phải chuyển thành INCONCLUSIVE và chặn publish.
- Mỗi task phải có test tập trung và mỗi phase phải có checkpoint trước khi sang phase kế tiếp.

---

## 1. Bối cảnh và kết luận từ hai bản phân tích

### 1.1. Baseline thực tế

Repo là modular monolith với các khối:

~~~text
Entry points: CLI / FastAPI / Scheduler / Webhook / MCP
        ↓
Orchestrator: discovery → analysis/issue solving → generation → PR
        ↓
Cross-cutting: memory, events, retry, config, quotas, middleware
        ↓
External side effects: GitHub fork/branch/file/issue/PR/comment/close
~~~

Có một khác biệt cần xử lý trước khi code: bản audit trên GitHub đọc origin/main ce7af0d, còn working tree local có refactor Layer C chưa commit như pipeline_core.py và steps.py. Không được coi hai trạng thái này là cùng một baseline.

### 1.2. Pattern được harvest

| Nguồn | Pattern được giữ | Vị trí trong ContribAI |
|---|---|---|
| Codex | approval, sandbox, execution boundary, escalation | PolicyEngine, ExecutionOrchestrator |
| OpenCode | allow/ask/deny, resource-pattern permissions, once/always approval | PolicyEngine |
| OpenHands SDK | workspace abstraction, local/Docker/remote lifecycle | execution/workspaces |
| mini-SWE-agent | linear trajectory, step/cost/time budget, process-group timeout | ExecutionBudget, AgentTrajectory |
| Agentless | localization → repair → validation, N-best candidates, reranking | localization, engines, verification |
| Aider | RepoMap, symbol/reference graph, token-aware context, lint feedback loop | context, verification |
| AutoCodeRover | AST-aware class/function/edit localization | context/symbol_index, localization |
| PR-Agent | dynamic diff context, PR compression, feedback/persistent suggestions | review, lifecycle |
| Continue | rules-as-code theo glob/regex và CI checks | context/rules, không thêm runtime dependency |
| ContributionAgent | opportunity scoring, maintainer preference, outcome learning | opportunity, storage/outcomes |

### 1.3. Vấn đề gốc

Hiện có nhiều lớp bảo vệ nhưng chưa có authority duy nhất. Các đường có thể dẫn tới GitHub write gồm pipeline, issue mode, web, webhook, scheduler, CLI, MCP và cleanup.

Invariant mới:

~~~text
Any entrypoint
    → CommandService
    → WorkItem
    → ExecutionSupervisor
    → VerificationReport
    → ReviewDecision
    → PublishGate
    → PublishPermit
    → GitHubPublisher only
~~~

### 1.4. Product direction

Không biến ContribAI thành bản fork của bất kỳ coding agent nào. ContribAI nên sở hữu:

1. Opportunity Intelligence: repo/issue nào đáng làm.
2. Contribution Intelligence: patch nào đáng tin.
3. Maintainer Intelligence: project chấp nhận kiểu PR nào.

Coding engine là plugin/adapter. Coding engine không phải sản phẩm và không có quyền publish.

## 2. Kiến trúc mục tiêu

~~~mermaid
flowchart TD
    CLI["CLI"] --> CMD["CommandService"]
    WEB["Web"] --> CMD
    MCP["MCP"] --> CMD
    SCH["Scheduler"] --> CMD
    WH["Webhook"] --> CMD

    CMD --> OE["OpportunityEngine"]
    OE --> W["WorkItem"]
    W --> SUP["ExecutionSupervisor"]

    SUP --> POL["PolicyEngine: allow / ask / deny"]
    SUP --> BUD["Budget + Trajectory"]
    SUP --> CRED["CredentialBroker / Model Gateway"]
    SUP --> WS["WorkspaceManager: clean attempt snapshot"]
    WS --> ROUTER["EngineRouter"]
    ROUTER --> DRV["EngineDriver"]
    DRV --> OUT["EngineOutcome"]
    OUT --> PC["PatchCollector: diff BASE"]
    PC --> CAND["CandidateSet"]
    CAND --> VER["VerificationEngine"]
    VER --> REP["VerificationReport"]
    REP --> REV["ReviewService"]
    REV --> GATE["PublishGate"]
    GATE --> PERMIT["PublishPermit"]
    PERMIT --> PUB["GitHubPublisher only"]
    PUB --> GH["GitHub write API"]

    GH --> PATROL["PR Lifecycle / Patrol"]
    PATROL --> OUT["Outcome Learning"]
    OUT --> OE

    CMD --> MEM["SQLite Storage"]
    SUP --> EVT["EventBus"]
~~~

### 2.1. Quyết định kiến trúc

- Giữ modular monolith trước microservice. Single-node SQLite đủ cho state, quota reservation, review request và outcome.
- Control plane sở hữu state và side effect. EngineDriver chỉ sửa workspace trong execution lease và trả EngineOutcome; PatchCollector so sánh workspace trước/sau để tạo CandidateSet.
- Issue-first là đường production v1. Code scan chỉ tạo CandidateFinding cho đến khi có policy/review phù hợp.
- Dùng ExecutionMode gồm shadow, review_only, live; không dùng dry_run=False làm mặc định live.
- Fail closed trước publish. Không có baseline, verification, review, permit hoặc quota thì không publish.
- Dùng một ContributionContext thay cho các PipelineContext/RepoContext rời rạc.
- WorkspaceManager là security boundary bên ngoài duy nhất; mọi attempt và candidate đều gắn với base SHA, snapshot ID và attempt ID.
- CredentialBroker cấp model lease ngắn hạn hoặc endpoint model gateway theo work item/budget/provider; engine không giữ raw provider secret.
- NativeEngineDriver là implementation mặc định. Adapter external được chia thành InProcess SDK, CLI process và Server/Protocol; OpenHands/Codex/OpenCode/Mini-SWE là optional theo capability, version pin và contract test.

### 2.2. Engine integration contract

Boundary này được chốt trước Task 10 và Task 15 để ContribAI không ép các coding agent có runtime riêng phải giả lập API `read/write/return CandidateSet` của NativeEngine.

~~~text
ExecutionSupervisor
    → WorkspaceManager.create_attempt(base_sha, attempt_id)
    → EngineRouter.select(EngineSpec, EngineCapabilities)
    → EngineDriver.run(EngineRequest, ExecutionLease)
    → EngineOutcome                         # không chứa patch
    → PatchCollector.collect(BASE, AFTER)
    → PatchCandidate / CandidateSet
    → VerificationEngine / CandidateRanker
~~~

Interface chuẩn:

~~~python
class EngineDriver(Protocol):
    async def run(
        self,
        request: EngineRequest,
        execution: ExecutionLease,
    ) -> EngineOutcome: ...

@dataclass(frozen=True)
class EngineRequest:
    work_id: str
    attempt_id: str
    task: RepairTask
    context: ContributionContext
    repo_rules: RepoRules
    budget: ExecutionBudget
    capability_policy: CapabilityPolicy
    engine_config: Mapping[str, object]

@dataclass(frozen=True)
class EngineOutcome:
    status: EngineStatus
    exit_reason: str
    events: tuple[ExecutionEvent, ...]
    usage: EngineUsage
    cost_usd: float
    trajectory_id: str
    engine_version: str
    metadata: Mapping[str, object]
~~~

`EngineOutcome` không chứa `PatchCandidate`, `CandidateSet`, GitHub client hay publish result. `PatchCollector` phải xác nhận base SHA, thu changed files/diff/additions/deletions, phát hiện file ngoài workspace và tạo candidate hash deterministic. Candidate chỉ được đưa sang verification sau khi collector hoàn tất.

Ba loại adapter được hỗ trợ:

| Loại | Driver đầu tiên | Boundary bắt buộc |
|---|---|---|
| InProcess SDK | `NativeEngineDriver`, `MiniSWEInProcessDriver`, `OpenHandsSDKDriver` | backend nhận workspace adapter/LocalWorkspace nằm trong outer sandbox; không có publisher |
| CLI process | `CodexExecDriver`, `AiderCLIDriver` tùy chọn | spawn trong execution lease, JSONL/stdout bounded, process-group timeout/kill, diff lấy từ workspace |
| Server/Protocol | `OpenCodeServerDriver`, `CodexAppServerDriver`, `OpenHandsServerDriver` tùy chọn | server chạy trong workspace boundary, session/turn/cancel/resume map về WorkItem, không dùng host socket |

Engine capability phải được probe và pin trước khi live:

~~~python
@dataclass(frozen=True)
class EngineCapabilities:
    engine: str
    version: str
    interface_version: str
    streaming: bool
    resume: bool
    cancellation: bool
    diff_events: bool
    approvals: bool
    model_gateway: bool
~~~

`EngineRouter` chỉ route theo capability, cost, complexity và policy. PolicyEngine của ContribAI là final authority; permission của backend chỉ là bản dịch defense-in-depth. Nếu backend không hỗ trợ model gateway, cancellation hoặc sandbox contract bắt buộc thì adapter bị deny trong live mode, không tự rơi về `latest` hoặc prompt tương tác.

## 3. Definition of Done

- [ ] Static audit chỉ tìm thấy GitHub write call trong contribai/publishing/github_publisher.py và test doubles.
- [ ] shadow và review_only tạo zero GitHub writes.
- [ ] Không publish nếu thiếu PublishPermit hợp lệ.
- [ ] Human reject/skip trong cả code-scan và issue mode tạo zero PR.
- [ ] Web/API/webhook/scheduler/MCP dùng explicit live mode và safe-by-default auth.
- [ ] Workspace chạy code repo không có GitHub credential hoặc privileged host capability.
- [ ] Mọi engine chỉ implement EngineDriver và trả EngineOutcome; không engine nào tạo CandidateSet hoặc giữ patch authority.
- [ ] PatchCollector thu diff từ clean snapshot trước/sau, kiểm tra base SHA và tạo candidate hash deterministic.
- [ ] Mỗi N-best/retry attempt dùng workspace/snapshot riêng; attempt sau không bị nhiễm patch attempt trước.
- [ ] Model credential external đi qua CredentialBroker/model gateway; raw provider key không xuất hiện trong engine environment mặc định.
- [ ] InProcess, CLI và Server/Protocol driver đều pass cùng engine contract suite; unsupported capability/version bị deny trong live mode.
- [ ] Mọi WorkItem transition được persist và transition bất hợp lệ bị từ chối.
- [ ] Quota reservation chống vượt giới hạn khi concurrent.
- [ ] LLM task nằm trong request, không nằm trong mutable provider state.
- [ ] VerificationReport chứa bằng chứng; INCONCLUSIVE không được publish.
- [ ] Issue upstream không bị đóng nếu không chứng minh được issue do ContribAI tạo.
- [ ] Tests architecture/safety/concurrency/e2e pass; lint/format/build/coverage đạt CI gate.
- [ ] License, Dockerfile, README, CHANGELOG và architecture docs thống nhất với mã nguồn.

---

## Phase 0 — Freeze live danger và khóa một write path

Mục tiêu: không thêm intelligence trước khi chứng minh hệ thống không thể bypass safety.

### Task 0: Reconcile baseline và tạo execution branch

**Files:**
- Read-only: git status, git diff, git log, pyproject.toml, .github/workflows/ci.yml
- Ghi nhận quyết định trong plan này

**Interfaces:**
- Baseline commit: origin/main ce7af0d hoặc commit khác được owner xác nhận.
- Branch triển khai: codex/contribution-control-plane hoặc tên branch được thống nhất.

- [ ] Step 1: Capture baseline.

~~~bash
git status --short --branch
git log -1 --format='%H %ad %s' --date=iso
git diff --stat
~~~

- [ ] Step 2: Run baseline verification.

~~~bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check contribai/ tests/
.venv/bin/python -m ruff format --check contribai/ tests/
.venv/bin/python -m compileall -q contribai
~~~

Expected: ghi lại kết quả, kể cả khi baseline fail; không sửa code trong task này.

- [ ] Step 3: So sánh pipeline.py, pipeline_core.py, steps.py và test mới với origin/main. Chọn port có kiểm soát, tách Layer C thành PR riêng hoặc giữ ngoài branch. Không reset destructive.
- [ ] Step 4: Branch có baseline rõ ràng và không còn giả định ngầm về file chưa commit.

**Verification:** git status, các lệnh test/lint/compile trên, review diff baseline.

**Dependencies:** None.

**Estimated scope:** XS.

### Task 1: Capability model và PolicyEngine

**Files:**
- Create: contribai/publishing/capability.py
- Create: contribai/publishing/policy.py
- Create: tests/architecture/test_policy_engine.py
- Modify: contribai/core/config.py

**Interfaces:**

~~~python
class Capability(StrEnum):
    WORKSPACE_READ = "workspace.read"
    WORKSPACE_WRITE = "workspace.write"
    NETWORK = "network"
    GITHUB_READ = "github.read"
    GITHUB_COMMENT = "github.comment"
    GITHUB_PUSH = "github.push"
    GITHUB_CREATE_ISSUE = "github.create_issue"
    GITHUB_CREATE_PR = "github.create_pr"
    GITHUB_CLOSE_PR = "github.close_pr"

@dataclass(frozen=True)
class CapabilityRequest:
    actor: str
    capability: Capability
    resource: str
    work_id: str

class PolicyEngine:
    def evaluate(self, request: CapabilityRequest) -> PolicyDecision: ...
~~~

- [ ] Step 1: Viết test cho allow, ask, deny, wildcard resource và default-deny cho mọi GitHub write.
- [ ] Step 2: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/architecture/test_policy_engine.py
~~~

Expected: FAIL vì capability/policy chưa tồn tại.

- [ ] Step 3: Implement rule order deterministic: exact match > pattern match > default. GitHub write capability không có rule explicit phải deny.
- [ ] Step 4: Policy parse lỗi phải fail startup; không biến config thiếu thành permissive.
- [ ] Step 5: Chạy test và commit:

~~~bash
.venv/bin/python -m pytest -q tests/architecture/test_policy_engine.py
git add contribai/publishing/capability.py contribai/publishing/policy.py contribai/core/config.py tests/architecture/test_policy_engine.py
git commit -m "feat: add capability policy engine"
~~~

**Acceptance criteria:** policy engine độc lập với GitHub client; request thiếu rule bị deny.

**Dependencies:** Task 0.

**Estimated scope:** M.

### Task 2: PublishPermit, GitHubPublisher và idempotency

**Files:**
- Create: contribai/publishing/permit.py
- Create: contribai/publishing/idempotency.py
- Create: contribai/publishing/github_publisher.py
- Modify: contribai/github/client.py, contribai/pr/manager.py
- Test: tests/architecture/test_publish_gate.py, tests/unit/test_github_publisher.py

**Interfaces:**

~~~python
@dataclass(frozen=True)
class PublishPermit:
    work_id: str
    repo: str
    base_sha: str
    patch_sha256: str
    verification_id: str
    review_id: str
    quota_reservation_id: str
    expires_at: datetime

class GitHubPublisher:
    async def publish(
        self,
        permit: PublishPermit,
        candidate: PatchCandidate,
    ) -> PRResult: ...
~~~

- [ ] Step 1: Viết test cho permit hết hạn, repo mismatch, base SHA mismatch, patch hash mismatch, thiếu review/verification và publish lặp.
- [ ] Step 2: Chạy focused tests và xác nhận fail.
- [ ] Step 3: Tách write orchestration khỏi PRManager. Chỉ GitHubPublisher gọi fork/branch/file/issue/PR/comment/close.
- [ ] Step 4: Dùng idempotency key work_id + repo + base_sha + patch_sha256; publish lặp trả kết quả cũ hoặc reject, không tạo PR thứ hai.
- [ ] Step 5: Static audit:

~~~bash
rg -n "create_pull_request|create_or_update_file|create_issue|close_pull_request|fork_repository" contribai --glob '*.py'
~~~

Expected: production calls tập trung trong publisher.
- [ ] Step 6: Chạy tests và commit.

**Acceptance criteria:** không API write nào chạy nếu không có permit hợp lệ; publisher tự kiểm tra permit; publish lặp idempotent.

**Dependencies:** Task 1.

**Estimated scope:** L.

### Task 3: Explicit execution mode, auth fail-closed và webhook an toàn

**Files:**
- Create: contribai/control/mode.py, contribai/web/schemas.py
- Modify: contribai/web/server.py, contribai/web/dashboard.py, contribai/web/auth.py, contribai/web/webhooks.py
- Test: tests/web/test_server_safety.py, tests/web/test_webhooks.py

**Interfaces:**

~~~python
class ExecutionMode(StrEnum):
    SHADOW = "shadow"
    REVIEW_ONLY = "review_only"
    LIVE = "live"

class RunRequest(BaseModel):
    mode: ExecutionMode = ExecutionMode.SHADOW
    repo_url: str | None = None
~~~

- [ ] Step 1: Viết test chứng minh JSON body từ dashboard bind đúng mode; không truyền mode thì không tạo write; LIVE không có API key bị từ chối.
- [ ] Step 2: Sửa route nhận RunRequest trong body, không trộn query scalar với JSON body.
- [ ] Step 3: Write endpoint không có API key config phải fail startup hoặc trả lỗi cấu hình trong live deployment.
- [ ] Step 4: Webhook thiếu secret khi enabled phải fail startup; chữ ký sai/JSON sai trả HTTP error; callback chỉ tạo WorkItem mode explicit.
- [ ] Step 5: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/web/test_server_safety.py tests/web/test_webhooks.py
~~~

**Acceptance criteria:** dashboard Dry Run không thể trở thành live; webhook unsigned không kích hoạt pipeline; live web write cần auth và mode explicit.

**Dependencies:** Tasks 1-2.

**Estimated scope:** L.

### Task 4: Gộp Human Review và khóa issue side effects

**Files:**
- Modify: contribai/orchestrator/pipeline.py, contribai/issues/solver.py, contribai/pr/manager.py, contribai/orchestrator/review_gate.py
- Create/modify: tests/safety/test_review_and_issue_side_effects.py

- [ ] Step 1: Viết test invariant cho cả code-scan và issue mode: reviewer reject/skip → zero create_pr.
- [ ] Step 2: Viết test chứng minh CONTRIBUTING.md không tự động có nghĩa requires_issue_link=True.
- [ ] Step 3: Sửa issue path để đi qua cùng ReviewGate/PublishGate với code-scan.
- [ ] Step 4: Chỉ đóng issue khi persisted record có created_by_contribai=True và policy auto_close=True.
- [ ] Step 5: Chỉ tạo issue mới khi guideline parse ra requires_issue_link=True; issue mới luôn cần review explicit.
- [ ] Step 6: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/safety/test_review_and_issue_side_effects.py tests/unit/test_pr_manager.py
~~~

**Acceptance criteria:** mọi đường tới publisher đều có review; upstream issue luôn giữ nguyên; issue creation chỉ xảy ra khi policy explicit.

**Dependencies:** Tasks 1-3.

**Estimated scope:** L.

### Checkpoint 0: Live safety

- [ ] pytest tests/architecture tests/safety tests/web -q pass.
- [ ] rg không còn raw GitHub write ngoài publisher/test doubles.
- [ ] Có test cho dashboard shadow, webhook secret, issue path review và duplicate publish.
- [ ] Review thủ công MCP, CLI cleanup, scheduler và webhook side effects.
- [ ] Không sang Phase 1 nếu một invariant P0 còn fail.

---

## Phase 1 — WorkItem control plane, budget và workspace isolation

### Task 5: WorkItem state machine và persistent storage

**Files:**
- Create: contribai/domain/state.py, contribai/domain/work_item.py
- Create: contribai/storage/work_items.py
- Modify: contribai/orchestrator/memory.py
- Test: tests/domain/test_work_item_state.py, tests/storage/test_work_items.py

**Interfaces:**

~~~python
class WorkState(StrEnum):
    DISCOVERED = "discovered"
    QUALIFIED = "qualified"
    RESERVED = "reserved"
    PREPARING = "preparing"
    SOLVING = "solving"
    ENGINE_WAITING_APPROVAL = "engine_waiting_approval"
    PATCH_COLLECTING = "patch_collecting"
    PATCHED = "patched"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    REVIEW_PENDING = "review_pending"
    APPROVED = "approved"
    PUBLISH_RESERVED = "publish_reserved"
    PUBLISHED = "published"
    CI_RUNNING = "ci_running"
    MERGED = "merged"
    CLOSED = "closed"
    NEEDS_FIX = "needs_fix"

class WorkItem:
    id: str
    repo: str
    issue_number: int | None
    mode: ExecutionMode
    state: WorkState
    attempt: int
    budget: ExecutionBudget
~~~

- [ ] Step 1: Viết transition tests; PATCHED→PUBLISHED và transition lùi bất hợp lệ bị reject; engine approval wait phải đi qua `ENGINE_WAITING_APPROVAL` rồi resume/cancel được.
- [ ] Step 2: Tạo tables work_items, work_events, review_requests, verification_reports, quota_reservations, publish_permits và side_effects bằng schema version/migration.
- [ ] Step 3: Implement repository create/get/transition/append_event/record_side_effect trong transaction.
- [ ] Step 4: Persist sau mỗi transition để restart có thể resume hoặc chuyển NEEDS_FIX an toàn.
- [ ] Step 5: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/domain/test_work_item_state.py tests/storage/test_work_items.py
~~~

**Acceptance criteria:** state machine là authority duy nhất; illegal transition không thể tạo side effect; dữ liệu tồn tại sau restart.

**Dependencies:** Checkpoint 0.

**Estimated scope:** L.

### Task 6: LLMRequest, budget và trajectory không dùng mutable task state

**Files:**
- Modify: contribai/llm/models.py, contribai/llm/provider.py, contribai/llm/fallback.py
- Create: contribai/execution/budget.py, contribai/execution/trajectory.py
- Test: tests/llm/test_request_routing_concurrency.py, tests/execution/test_budget.py

**Interfaces:**

~~~python
@dataclass(frozen=True)
class LLMRequest:
    task: TaskType
    provider: str
    model: str
    messages: list[dict[str, str]]
    timeout_sec: float
    max_tokens: int
    credential_scope: str | None = None
    response_schema: dict[str, object] | None = None

@dataclass
class ExecutionBudget:
    max_steps: int
    max_cost_usd: float
    max_wall_time_sec: float
    max_tool_failures: int

class AgentTrajectory:
    async def append(self, event: ExecutionEvent) -> None: ...
    def snapshot(self) -> list[ExecutionEvent]: ...
~~~

- [ ] Step 1: Viết concurrency test chạy analysis và code-gen trên cùng provider; mỗi request dùng đúng task/model.
- [ ] Step 2: Thêm request adapter để provider hiện tại dùng được mà không phá ngay caller; đánh dấu set_task deprecated.
- [ ] Step 3: Sửa fallback dùng asyncio.wait_for cho từng ProviderSlot.timeout, sửa auth-error order và giới hạn attempt history.
- [ ] Step 4: Dừng execution khi vượt step, cost, wall time hoặc tool failure.
- [ ] Step 5: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/llm/test_request_routing_concurrency.py tests/execution/test_budget.py
~~~

**Acceptance criteria:** không còn provider-wide mutable task; timeout slot được thực thi; budget exhaustion không publish.

**Dependencies:** Task 5.

**Estimated scope:** L.

### Task 7A: Workspace abstraction và clean attempt snapshots

**Files:**
- Create: contribai/execution/workspaces/base.py, local.py, docker.py, manager.py
- Create: contribai/execution/resource_policy.py
- Replace/adapt: contribai/sandbox/sandbox.py
- Test: tests/execution/test_workspace_contract.py, tests/execution/test_workspace_snapshots.py

**Interfaces:**

~~~python
class Workspace(Protocol):
    base_sha: str
    snapshot_id: str
    attempt_id: str

    async def execute(self, command: str, timeout_sec: float) -> CommandResult: ...
    async def read_file(self, path: str) -> str: ...
    async def write_file(self, path: str, content: str) -> None: ...
    async def apply_patch(self, patch: PatchCandidate) -> None: ...
    async def diff_from_base(self) -> WorkspaceDiff: ...
    async def changed_files(self) -> tuple[str, ...]: ...
    async def reset(self) -> None: ...

class WorkspaceManager(Protocol):
    async def create_attempt(
        self, work_id: str, base_sha: str, attempt_id: str, policy: ResourcePolicy
    ) -> Workspace: ...
    async def destroy_attempt(self, snapshot_id: str) -> None: ...

class ResourcePolicy:
    network: Literal["deny", "ask", "allow"] = "deny"
    cpu_limit: float = 1.0
    memory_mb: int = 512
    pids_limit: int = 128
~~~

- [ ] Step 1: Viết fake workspace contract tests cho execute/read/write/apply/reset, base SHA, changed files và process timeout.
- [ ] Step 2: Implement LocalWorkspace chỉ cho development; live publish phải yêu cầu policy explicit và vẫn đi qua `WorkspaceManager`.
- [ ] Step 3: Implement DockerWorkspace non-root, no Docker socket, read-only base, bounded CPU/RAM/PIDs, network denied mặc định. Đây là outer sandbox authority của ContribAI.
- [ ] Step 4: `WorkspaceManager.create_attempt` luôn tạo clean snapshot từ cùng `base_sha`; retry/N-best dùng snapshot/container/worktree riêng và có cleanup sau terminal state.
- [ ] Step 5: Docker unavailable hoặc validator unavailable trả `UNVERIFIED`/`INCONCLUSIVE`, không success giả và không cho publisher chạy.
- [ ] Step 6: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/execution/test_workspace_contract.py tests/execution/test_workspace_snapshots.py
~~~

**Acceptance criteria:** mỗi attempt có workspace sạch, base SHA bất biến và diff thu được chính xác; code repo không chạy ngoài outer workspace; không verify được thì không publish.

**Dependencies:** Tasks 5-6.

**Estimated scope:** L.

### Task 7B: CredentialBroker và model gateway

**Files:**
- Create: contribai/execution/credentials.py, contribai/execution/model_gateway.py
- Modify: contribai/core/config.py, contribai/llm/provider.py, contribai/llm/fallback.py
- Test: tests/safety/test_workspace_credentials.py, tests/execution/test_credential_broker.py

**Interfaces:**

~~~python
@dataclass(frozen=True)
class CredentialLease:
    work_id: str
    attempt_id: str
    provider: str
    endpoint: str
    token: str  # gateway-scoped token, never the raw provider key
    expires_at: datetime
    max_cost_usd: float

class CredentialBroker(Protocol):
    async def issue_model_lease(
        self,
        work_id: str,
        attempt_id: str,
        provider: str,
        budget: ExecutionBudget,
    ) -> CredentialLease: ...
    async def revoke(self, lease_id: str) -> None: ...
~~~

- [ ] Step 1: Phân biệt secret classes: GitHub/SSH/Docker host credentials luôn bị deny; model access chỉ được cấp qua scoped lease.
- [ ] Step 2: Đưa provider key thật vào control-plane/model-gateway boundary; engine nhận endpoint + token ngắn hạn gắn với `work_id`, `attempt_id`, provider và cost budget.
- [ ] Step 3: Không inject `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` hoặc raw provider key vào subprocess mặc định. Backend không hỗ trợ model gateway bị deny trong live mode, không silently fallback sang raw secret.
- [ ] Step 4: Enforce expiry, provider allowlist, max cost, revoke khi cancel/timeout và redact token khỏi trajectory/stdout/event metadata.
- [ ] Step 5: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/safety/test_workspace_credentials.py tests/execution/test_credential_broker.py
~~~

**Acceptance criteria:** engine không thấy GitHub/SSH/Docker credential; model request chạy được qua brokered gateway; lease sai scope/expired/over-budget bị deny; credential không xuất hiện trong logs hoặc trajectory.

**Dependencies:** Tasks 5-6.

**Estimated scope:** L; model gateway có thể bắt đầu bằng in-process boundary rồi tách transport sau khi contract ổn định.

### Checkpoint 1: Control plane nền

- [ ] WorkItem resume được sau process restart.
- [ ] Concurrent LLM requests không lẫn task/model.
- [ ] Mỗi retry/N-best attempt bắt đầu từ clean snapshot cùng base SHA.
- [ ] Workspace test chứng minh GitHub/SSH/Docker credential absence.
- [ ] Model gateway test chứng minh scoped lease, expiry, budget và revoke.
- [ ] INCONCLUSIVE path chặn publisher.

---

## Phase 2 — Context, localization, coding engine và verification

### Task 8: ContributionContext, RepoRules và ContextEngine

**Files:**
- Create: contribai/context/context.py, rules.py, repo_map.py, symbol_index.py, builder.py
- Modify: contribai/core/models.py, contribai/analysis/analyzer.py
- Test: tests/context/test_context_engine.py, tests/context/test_repo_rules.py

**Interfaces:**

~~~python
@dataclass(frozen=True)
class ContributionContext:
    repo: Repository
    repo_snapshot: RepositorySnapshot
    repo_profile: RepoProfile | None
    repo_rules: ResolvedRepoRules
    repo_map: str
    symbol_index: SymbolIndex
    pr_history: list[PRSummary]
    relevant_files: dict[str, str]
    previous_attempts: list[AttemptSummary]
    budget: ExecutionBudget
~~~

- [ ] Step 1: Test README, CONTRIBUTING, AGENTS/CLAUDE rules, coding style, repo intelligence và PR history đi vào cùng context.
- [ ] Step 2: Tạo RepoRules auto-discover glob/regex rules; Continue chỉ là pattern tham khảo.
- [ ] Step 3: Tạo symbol index interface multi-language; Tree-sitter là optional extra, Python AST là adapter.
- [ ] Step 4: Tạo RepoMap token-aware với definitions/references/ranking và max_context_tokens.
- [ ] Step 5: Sửa analyzer/generator nhận ContributionContext; loại getattr vào field không tồn tại.
- [ ] Step 6: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/context
~~~

**Acceptance criteria:** không còn hai nguồn context cạnh tranh; repo intelligence đi tới analyzer/generator; context budget deterministic.

**Dependencies:** Checkpoint 1.

**Estimated scope:** XL; tách RepoRules, SymbolIndex, RepoMap thành commits độc lập.

### Task 9: Hierarchical localization

**Files:**
- Create: contribai/localization/models.py, localizer.py, edit_locations.py
- Modify: contribai/analysis/analyzer.py, contribai/issues/solver.py
- Test: tests/localization/test_hierarchical_localization.py

**Interfaces:**

~~~python
@dataclass(frozen=True)
class LocalizationCandidate:
    path: str
    symbol: str | None
    line_start: int | None
    line_end: int | None
    evidence: list[str]
    score: float

class Localizer:
    async def locate(
        self,
        task: ContributionTask,
        context: ContributionContext,
    ) -> LocalizationSet: ...
~~~

- [ ] Step 1: Tạo fixture Python class/function, JS/TS module/function và Go/Rust entrypoint.
- [ ] Step 2: Test recall tại file, symbol và exact edit location; candidate phải có evidence.
- [ ] Step 3: Implement file → class/function → exact location; dùng RepoMap/SymbolIndex trước LLM.
- [ ] Step 4: Trả LocalizationSet cho repair engine; không chọn một file duy nhất quá sớm.
- [ ] Step 5: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/localization/test_hierarchical_localization.py
~~~

**Acceptance criteria:** localization trả nhiều candidate có evidence; test có Recall@1/3/5.

**Dependencies:** Task 8.

**Estimated scope:** L.

### Task 10A: Engine runtime contract

**Files:**
- Create: contribai/engines/models.py, protocol.py
- Test: tests/engines/test_engine_models.py, tests/engines/test_engine_boundary.py

**Interfaces:**

~~~python
class EngineDriver(Protocol):
    async def run(
        self,
        request: EngineRequest,
        execution: ExecutionLease,
    ) -> EngineOutcome: ...

@dataclass(frozen=True)
class EngineRequest:
    work_id: str
    attempt_id: str
    task: RepairTask
    context: ContributionContext
    repo_rules: RepoRules
    budget: ExecutionBudget
    capability_policy: CapabilityPolicy
    engine_config: Mapping[str, object]

@dataclass(frozen=True)
class EngineOutcome:
    status: EngineStatus
    exit_reason: str
    events: tuple[ExecutionEvent, ...]
    usage: EngineUsage
    cost_usd: float
    trajectory_id: str
    engine_version: str
    metadata: Mapping[str, object]
~~~

- [ ] Step 1: Định nghĩa `EngineStatus` gồm completed, failed, timed_out, cancelled, waiting_for_approval và unsupported; outcome không chứa patch/candidate/publish result.
- [ ] Step 2: Boundary test chứng minh engine chỉ nhận `ExecutionLease` và scoped model lease/workspace reference; không nhận GitHub client, publisher hoặc raw provider key.
- [ ] Step 3: Giới hạn events/stdout/trajectory metadata và redact secret trước khi persist.
- [ ] Step 4: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/engines/test_engine_models.py tests/engines/test_engine_boundary.py
~~~

**Acceptance criteria:** interface không buộc backend trả CandidateSet; outcome đủ để resume/cancel/audit; engine không có publish capability.

**Dependencies:** Tasks 7A-7B, 8-9.

**Estimated scope:** M.

### Task 10B: EngineDriver, EngineRouter và NativeEngineDriver

**Files:**
- Create: contribai/engines/native.py, router.py, leases.py
- Modify: contribai/generator/engine.py, contribai/issues/solver.py
- Test: tests/engines/test_native_driver.py, tests/engines/test_engine_router.py

- [ ] Step 1: Bọc logic hiện tại thành `NativeEngineDriver`; driver sửa workspace qua lease và trả EngineOutcome, không tự tạo PatchCandidate.
- [ ] Step 2: Implement `ExecutionLease` cho workspace, budget, cancellation, policy và CredentialLease; lease hết hạn thì kill execution.
- [ ] Step 3: Thêm `EngineRouter` theo complexity, expected cost, risk, policy và capability; router không có write capability.
- [ ] Step 4: Giữ parity với structured output, style signals và self-review signals hiện tại nhưng coi chúng là trajectory/evidence, không phải verification authority.
- [ ] Step 5: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/engines/test_native_driver.py tests/engines/test_engine_router.py
~~~

**Acceptance criteria:** pipeline gọi `EngineDriver.run`; Native driver hoạt động qua workspace lease; route không bypass PolicyEngine hoặc publisher.

**Dependencies:** Task 10A.

**Estimated scope:** L.

### Task 10C: PatchCollector và candidate assembly

**Files:**
- Create: contribai/engines/patch_collector.py, contribai/engines/candidates.py
- Modify: contribai/core/models.py, contribai/execution/workspaces/manager.py
- Test: tests/engines/test_patch_collector.py, tests/engines/test_candidate_assembly.py

- [ ] Step 1: Capture before state: `base_sha`, snapshot ID, tracked/untracked file manifest và repository status.
- [ ] Step 2: Sau EngineOutcome terminal, collect `git diff BASE`, changed files, additions, deletions và binary/unreadable file markers.
- [ ] Step 3: Reject diff nếu base SHA đổi, file nằm ngoài workspace, patch hash không deterministic hoặc engine chưa terminal.
- [ ] Step 4: Tạo `PatchCandidate` và `CandidateSet` từ các attempt độc lập; patch collector không gọi GitHub.
- [ ] Step 5: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/engines/test_patch_collector.py tests/engines/test_candidate_assembly.py
~~~

**Acceptance criteria:** CandidateSet chỉ được tạo bởi control plane từ workspace before/after; diff thu chính xác file add/modify/delete; base mismatch bị chặn.

**Dependencies:** Tasks 7A, 10A-10B.

**Estimated scope:** L.

### Task 10D: Capability probe và version pinning

**Files:**
- Create: contribai/engines/capabilities.py, probes.py
- Modify: contribai/core/config.py, contribai/engines/router.py
- Test: tests/engines/test_capability_probe.py, tests/engines/test_version_policy.py

~~~python
@dataclass(frozen=True)
class EngineCapabilities:
    engine: str
    version: str
    interface_version: str
    streaming: bool
    resume: bool
    cancellation: bool
    diff_events: bool
    approvals: bool
    model_gateway: bool
~~~

- [ ] Step 1: Probe binary/SDK/server version trước run; lưu capability snapshot vào WorkItem/trajectory.
- [ ] Step 2: Pin supported version range hoặc digest cho từng adapter; không dùng `latest` trong production.
- [ ] Step 3: Deny live run nếu thiếu cancellation, sandbox, model gateway hoặc protocol capability bắt buộc; shadow có thể ghi unsupported outcome.
- [ ] Step 4: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/engines/test_capability_probe.py tests/engines/test_version_policy.py
~~~

**Acceptance criteria:** router chỉ chọn driver có capability/version hợp lệ; unsupported backend không bị silent fallback sang runtime khác hoặc raw secret.

**Dependencies:** Tasks 10A-10B.

**Estimated scope:** M.

### Task 11: VerificationEngine và repair feedback loop

**Files:**
- Create: contribai/verification/models.py, engine.py, ranker.py, runners.py
- Modify: contribai/generator/scorer.py, contribai/generator/style_validator.py
- Test: tests/verification/test_verification_engine.py, tests/verification/test_candidate_ranker.py, tests/verification/test_attempt_isolation.py

**Interfaces:**

~~~python
class VerificationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"

@dataclass(frozen=True)
class VerificationReport:
    status: VerificationStatus
    baseline_passed: bool
    syntax_passed: bool
    tests_passed: bool
    lint_passed: bool
    typecheck_passed: bool
    security_passed: bool
    quality_score: float
    tests_run: int
    tests_failed: int
    evidence: tuple[VerificationEvidence, ...]
~~~

- [ ] Step 1: Test baseline failure, syntax failure, targeted test failure, lint/typecheck failure, security failure và unavailable validator.
- [ ] Step 2: Gộp QualityScorer/StyleValidator thành checks score 0..1; loại semantics 7.0/10 lẫn 0..1.
- [ ] Step 3: Với từng `PatchCandidate` từ PatchCollector, verify trên snapshot độc lập: baseline → patch → targeted tests → affected suite → lint/typecheck/security.
- [ ] Step 4: Tạo FailureContext gồm command, exit code, traceback, changed symbol và surrounding lines; retry chỉ khi recoverable.
- [ ] Step 5: Rank candidates theo verification, regression safety, minimality, quality và cost; candidate tốt nhất không được overwrite evidence của candidate khác.
- [ ] Step 6: Test attempt A/B/C không chia sẻ working tree và candidate hash không đổi sau approval.
- [ ] Step 7: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/verification
~~~

**Acceptance criteria:** INCONCLUSIVE không publish; LLM review failure không tự approve; best candidate có evidence.

**Dependencies:** Tasks 7A-7B, 8-10D.

**Estimated scope:** XL; tách runners và ranker nếu cần.

### Checkpoint 2: Coding quality

- [ ] Localization có Recall@1/3/5.
- [ ] NativeEngineDriver chạy qua workspace/engine contract.
- [ ] PatchCollector tạo CandidateSet từ before/after diff, không từ engine return value.
- [ ] Mỗi N-best attempt dùng snapshot sạch cùng base SHA.
- [ ] VerificationReport phân biệt FAILED và INCONCLUSIVE.
- [ ] Candidate ranking được test độc lập.
- [ ] Không engine nào import publisher hoặc GitHub write client; internal permission không thay thế PolicyEngine.

---

## Phase 3 — Opportunity, review, lifecycle và entrypoint integration

### Task 12: OpportunityEngine và Issue-first orchestration

**Files:**
- Create: contribai/opportunity/models.py, engine.py, scoring.py
- Modify: contribai/github/discovery.py, contribai/analysis/repo_intel.py, contribai/orchestrator/memory.py
- Test: tests/opportunity/test_opportunity_engine.py, tests/opportunity/test_scoring.py

**Interfaces:**

~~~python
@dataclass(frozen=True)
class ContributionOpportunity:
    repo: str
    issue_number: int | None
    maintainer_receptiveness: float
    issue_clarity: float
    reproducibility: float
    testability: float
    conflict_risk: float
    estimated_cost_usd: float
    expected_impact: float
    merge_probability: float
~~~

- [ ] Step 1: Deterministic score tests cho merge rate, review time, open PR backlog, AI policy, CI/tests, issue labels, assignment, reproduction và scope.
- [ ] Step 2: Tách read-only discovery khỏi write pipeline; OpportunityEngine không biết publisher.
- [ ] Step 3: Tính ExpectedContributionValue = P(correct patch) × P(maintainer wants change) × P(merge) × impact − cost − risk/spam penalty.
- [ ] Step 4: Đặt issue-first làm production default; code scan chỉ tạo CandidateFinding.
- [ ] Step 5: Persist score evidence để outcome learning giải thích được ranking.
- [ ] Step 6: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/opportunity
~~~

**Acceptance criteria:** discovery ưu tiên merge probability/expected value thay vì chỉ stars; mỗi score có evidence; issue-first chạy được ở shadow mode.

**Dependencies:** Tasks 5, 8, 11.

**Estimated scope:** L.

### Task 13: Persistent ReviewService và dynamic PR review context

**Files:**
- Create: contribai/review/models.py, service.py, dynamic_context.py
- Modify: contribai/orchestrator/review_gate.py, contribai/pr/patrol.py
- Test: tests/review/test_review_service.py, tests/review/test_dynamic_context.py

**Interfaces:**

~~~python
class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"

class ReviewService:
    async def request(self, work_id: str, candidate_hash: str) -> ReviewRequest: ...
    async def decide(self, review_id: str, decision: ReviewDecision) -> ReviewRequest: ...
    async def get(self, review_id: str) -> ReviewRequest: ...
~~~

- [ ] Step 1: Test persistent request, approve/reject/expire, candidate hash mismatch và no-TTY operation.
- [ ] Step 2: Chuyển terminal HumanReviewer thành adapter của ReviewService; CLI, Web và MCP dùng cùng DB record.
- [ ] Step 3: Dynamic diff context ưu tiên changed lines, enclosing symbol, repo rules, issue và test evidence.
- [ ] Step 4: PR patrol tạo FeedbackWorkItem thay vì tự push trực tiếp.
- [ ] Step 5: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/review
~~~

**Acceptance criteria:** scheduler/webhook không cần TTY nhưng vẫn chờ review; review decision có hash/proof; feedback tạo work item mới.

**Dependencies:** Tasks 5, 11.

**Estimated scope:** L.

### Task 14: CommandService và gom entrypoints về control plane

**Files:**
- Create: contribai/control/command_service.py
- Modify: contribai/cli/main.py, contribai/web/server.py, contribai/mcp_server.py, contribai/scheduler/scheduler.py, contribai/web/webhooks.py
- Test: tests/integration/test_entrypoints_control_plane.py

- [ ] Step 1: Integration tests cho CLI target, web target, scheduler trigger, webhook event và MCP command; tất cả tạo WorkItem.
- [ ] Step 2: Implement CommandService submit/get/approve/reject/resume/cancel.
- [ ] Step 3: MCP giữ read tools; write tools chỉ tạo command/review request hoặc yêu cầu PublishPermit, không raw GitHub write.
- [ ] Step 4: Scheduler/webhook chỉ enqueue/submit work; mode lấy từ config explicit, không hardcode live.
- [ ] Step 5: Sửa CLI solve để thực sự tạo issue work item hoặc đổi help text thành inspect-only.
- [ ] Step 6: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/integration/test_entrypoints_control_plane.py
~~~

**Acceptance criteria:** mọi entrypoint dùng cùng command/control path; không entrypoint nào giữ GitHub write capability.

**Dependencies:** Tasks 2-5, 12-13.

**Estimated scope:** XL; chia MCP và Web/Scheduler thành hai commits nếu cần.

### Checkpoint 3: End-to-end safe contribution

- [ ] Issue-first shadow run tạo WorkItem và trajectory.
- [ ] WorkspaceManager tạo clean attempt; PatchCollector tạo PatchCandidate và VerificationReport.
- [ ] Human approve tạo PublishPermit; reject không tạo PR.
- [ ] Publisher tạo tối đa một PR cho cùng idempotency key.
- [ ] CLI/Web/MCP/Scheduler/Webhook cùng nhìn thấy state.

---

## Phase 4 — Optional engines, outcome learning và production hardening

### Task 15: External EngineDriver adapters tùy chọn

Không gom tất cả backend vào một adapter generic. Mỗi driver phải pass cùng contract suite, nhưng được phép giữ runtime-native behavior của mình. Adapter chỉ điều khiển execution; PatchCollector, VerificationEngine, ReviewService và GitHubPublisher vẫn thuộc control plane.

**Shared files:**
- Create: contribai/engines/adapters/, tests/engines/contracts/test_driver_contract.py
- Modify: pyproject.toml, contribai/engines/router.py, contribai/execution/credentials.py

**Shared contract — mọi driver phải pass:**

1. Không thấy `GITHUB_TOKEN`, `SSH_AUTH_SOCK`, host filesystem hoặc Docker socket.
2. Chỉ nhận model gateway/scoped lease; không đọc raw provider key trong live mode.
3. Không được `git push`, gọi GitHub write hoặc tạo PR.
4. Timeout có thể kill process/session; cancel có hiệu lực và được persist.
5. Network/resource policy của outer workspace có hiệu lực.
6. Crash không làm mất WorkItem; outcome/event được ghi bounded.
7. Base SHA giữ nguyên; diff được PatchCollector thu chính xác.
8. stdout/event/trajectory bị giới hạn kích thước và redact credential.
9. Cost/time/tool budget được ghi nhận; over-budget không tiếp tục execution.
10. Duplicate run không tạo duplicate PR; verification/publish vẫn do ContribAI quyết định.

#### Task 15.1: MiniSWEInProcessDriver

**Files:** `contribai/engines/adapters/mini_swe.py`, `tests/engines/adapters/test_mini_swe.py`

- [ ] Dùng Python binding khi package/version hỗ trợ; map environment của mini-SWE vào outer `Workspace`/execution lease.
- [ ] Giữ linear trajectory và step/cost/time budget, nhưng không cho backend tạo CandidateSet hay gọi publisher.
- [ ] Nếu binding không đáp ứng workspace/model gateway contract, chuyển sang unsupported thay vì tự cấp credential hoặc mount host.
- [ ] Chạy contract suite và adapter test.

**Acceptance criteria:** đây là adapter đầu tiên chứng minh EngineDriver không phụ thuộc CLI/server; patch chỉ xuất hiện sau PatchCollector.

**Dependencies:** Tasks 7A-7B, 10A-10D, 11, 14.

**Estimated scope:** M.

#### Task 15.2: OpenHandsSDKDriver

**Files:** `contribai/engines/adapters/openhands_sdk.py`, `tests/engines/adapters/test_openhands_sdk.py`

- [ ] Nhúng OpenHands SDK ở application boundary; `LocalWorkspace` của OpenHands chỉ trỏ vào workspace đã nằm trong outer ContribAI container.
- [ ] Không bật nested DockerWorkspace, Docker socket hoặc host mount. ContribAI `WorkspaceManager` là sandbox authority duy nhất.
- [ ] Map event/trajectory/cancel/timeout về `EngineOutcome`; server mode là follow-up riêng nếu SDK contract không đủ.
- [ ] Chạy contract suite và test chứng minh không có Docker-in-Docker.

**Acceptance criteria:** OpenHands không tạo sandbox authority thứ hai; outer workspace vẫn kiểm soát credential, network và process lifecycle.

**Dependencies:** Task 15.1.

**Estimated scope:** M.

#### Task 15.3: OpenCodeServerDriver

**Files:** `contribai/engines/adapters/opencode_server.py`, `tests/engines/adapters/test_opencode_server.py`

- [ ] Start/connect OpenCode server trong execution boundary, tạo session, gửi prompt, stream events và lấy terminal outcome.
- [ ] Dùng HTTP/API contract từ Python; không thêm Node bridge vào core nếu không cần thiết.
- [ ] Dịch PolicyEngine của ContribAI sang OpenCode allow/ask/deny để defense-in-depth. Nếu server phát permission ask, trả `WAITING_FOR_APPROVAL`/policy event thay vì block deadlock.
- [ ] Map session abort/resume nếu capability probe cho phép; diff vẫn lấy từ workspace qua PatchCollector.
- [ ] Chạy contract suite, HTTP lifecycle test và server crash/restart test.

**Acceptance criteria:** OpenCode permission không trở thành authority cuối hoặc vòng chờ với scheduler/reviewer; session không có GitHub write path.

**Dependencies:** Task 15.2.

**Estimated scope:** L.

#### Task 15.4: CodexExecDriver

**Files:** `contribai/engines/adapters/codex_exec.py`, `tests/engines/adapters/test_codex_exec.py`

- [ ] Chạy `codex exec` non-interactive trong clean workspace với JSONL event output và bounded stdout/stderr.
- [ ] Map process exit, timeout, cancel và event stream về `EngineOutcome`; không dùng output cuối của Codex để tạo patch.
- [ ] Probe version/flags và pin supported version; config không được dùng dangerous bypass để vượt outer policy.
- [ ] Chạy contract suite và fake CLI integration test.

**Acceptance criteria:** Codex V1 tích hợp qua CLI process, không import Rust source vào Python và không cần Codex giữ GitHub credential.

**Dependencies:** Task 15.3.

**Estimated scope:** M.

#### Task 15.5: CodexAppServerDriver

**Files:** `contribai/engines/adapters/codex_app_server.py`, `tests/engines/adapters/test_codex_app_server.py`

- [ ] Map WorkItem/attempt/trajectory sang thread/turn lifecycle; map event, file-change, command, approval, interrupt và cancellation.
- [ ] Capability probe bắt buộc initialization/protocol version, resume và cancellation trước live mode; pin version/digest, không dùng latest.
- [ ] Approval event của Codex chỉ quay về PolicyEngine/ReviewService; không cho app-server tự quyết định publish.
- [ ] Nếu app-server capability drift hoặc handshake fail, trả unsupported/inconclusive và giữ WorkItem resume-safe.
- [ ] Chạy contract suite và protocol fixture test.

**Acceptance criteria:** app-server chỉ là runtime transport; WorkItem, budget, policy, snapshot, verification và publishing vẫn do ContribAI sở hữu.

**Dependencies:** Task 15.4.

**Estimated scope:** L.

**Không thuộc milestone engine đầu tiên:** Aider được giữ như optional CLI adapter sau contract suite; RepoMap/lint/git patterns được harvest trước. Agentless, AutoCodeRover, PR-Agent và Continue được dùng như algorithm/review/rules patterns, không clone thành runtime adapter.

- [ ] Shared step: đăng ký optional extras, không làm thay đổi default NativeEngineDriver.
- [ ] Shared step: chạy:

~~~bash
.venv/bin/python -m pytest -q tests/engines/contracts tests/engines/adapters
~~~

**Acceptance criteria:** mỗi adapter thay được runtime mà không thay safety/verification/publishing; unsupported version/capability bị deny trong live; optional install không đổi default runtime.

**Dependencies:** Tasks 7A-7B, 10A-10D, 11, 14.

**Estimated scope:** XL; bắt buộc triển khai tuần tự 15.1 → 15.5, không mở song song trước khi shared contract ổn định.

### Task 16: Outcome learning và benchmark Contribution Value

**Files:**
- Create: contribai/storage/outcomes.py, contribai/opportunity/learning.py
- Modify: contribai/orchestrator/memory.py, contribai/opportunity/engine.py
- Test: tests/opportunity/test_outcome_learning.py, tests/benchmarks/test_contribution_metrics.py

- [ ] Step 1: Ghi outcome accepted/rejected/merged/closed, review latency, requested changes, CI result, cost, tool failures và policy denials.
- [ ] Step 2: Tính maintainer/repo preference với evidence threshold để tránh overfit.
- [ ] Step 3: Tạo benchmark fixtures cho localization, patch apply, syntax/test/lint/regression, cost, wall time, acceptance, merge và review time.
- [ ] Step 4: Tích hợp expected value vào OpportunityEngine với feature evidence.
- [ ] Step 5: Chạy:

~~~bash
.venv/bin/python -m pytest -q tests/opportunity/test_outcome_learning.py tests/benchmarks/test_contribution_metrics.py
~~~

**Acceptance criteria:** outcome làm thay đổi ranking có kiểm soát; metric cuối là expected contribution value.

**Dependencies:** Tasks 12-14.

**Estimated scope:** L.

### Task 17: CI, packaging, docs và cleanup

**Files:**
- Modify: .github/workflows/ci.yml, pyproject.toml, Dockerfile, README.md, docs/ARCHITECTURE.md, docs/ARCHITECTURE_V2.md, CHANGELOG.md
- Create or reconcile: LICENSE, docs/CONTRIBUTION_CONTROL_PLANE.md
- Test: tests/architecture/test_packaging_contract.py

- [ ] Step 1: Packaging tests cho version/license/duplicate dependency/Docker COPY LICENSE/entrypoint.
- [ ] Step 2: Chọn một license duy nhất giữa README và pyproject.toml, thêm license thật vào root và sửa Docker build context.
- [ ] Step 3: Cập nhật architecture docs để không mô tả middleware/skills/sandbox là active nếu chưa có production call path.
- [ ] Step 4: Bổ sung CI architecture/safety/concurrency suites và coverage cho operational paths.
- [ ] Step 5: Chạy:

~~~bash
.venv/bin/python -m pytest -q --cov=contribai --cov-report=term-missing --cov-fail-under=50
.venv/bin/python -m ruff check contribai/ tests/
.venv/bin/python -m ruff format --check contribai/ tests/
.venv/bin/python -m compileall -q contribai
docker build -t contribai:plan-check .
~~~

**Acceptance criteria:** CI phản ánh safety path thật; docs/license/version/Docker nhất quán; test contract cũ không mô tả behavior sai.

**Dependencies:** Checkpoints 0-3 và Tasks 15-16 nếu optional adapters được bật.

**Estimated scope:** L.

### Checkpoint 4: Release readiness

- [ ] Architecture test suite pass.
- [ ] Full test, lint, format, compile và Docker build pass.
- [ ] PublishPermit/quota/idempotency/side-effect audit pass.
- [ ] Có rollback/runbook cho publisher và workspace.
- [ ] Có benchmark report trước/sau OpportunityEngine và NativeEngineDriver; external drivers có contract report riêng.
- [ ] Human owner review plan completion và bật live mode riêng biệt.

---

## 4. Test matrix bắt buộc

### Safety invariants

1. shadow → zero GitHub writes.
2. Issue mode + reviewer reject → zero PR.
3. Concurrent opportunities với limit 5 → tối đa 5 publish permits.
4. CI fail không đóng issue upstream.
5. Webhook không secret → startup error hoặc endpoint disabled.
6. MCP create_pr không permit → denied.
7. Workspace environment không có GITHUB_TOKEN.
8. LLM analysis và code-gen concurrent → đúng task model.
9. Candidate thay đổi sau approval → permit invalid.
10. Publish cùng opportunity hai lần → idempotent reject hoặc reuse result.
11. Retry/N-best attempts không chia sẻ working tree; mọi candidate giữ cùng base SHA.
12. EngineOutcome không chứa patch; PatchCollector thu đúng file add/modify/delete và candidate hash.
13. External driver không thấy raw model key; CredentialLease hết hạn/over-budget bị deny.
14. CLI/server driver cancel, timeout, crash và bounded output không làm mất WorkItem.
15. Engine version/capability drift bị deny trong live mode, không silent fallback.

### Quality metrics

- Localization Recall@1, Recall@3, Recall@5.
- Patch apply rate.
- Baseline preservation rate.
- Syntax, targeted-test, regression-test, lint, typecheck, security pass rate.
- Average model/tool calls, cost, wall time.
- Policy denials, unsafe action attempts, human rejection rate.
- PR acceptance rate, merge rate, median review time.
- Expected Contribution Value theo repo/issue type.

## 5. Rủi ro và biện pháp giảm thiểu

| Rủi ro | Mức độ | Biện pháp |
|---|---:|---|
| Local Layer C khác origin/main | Cao | Task 0 reconcile trước khi code |
| Publisher bị bypass bởi MCP/webhook/cleanup | Critical | Static write boundary + architecture tests |
| Coding workspace đọc được credential | Critical | Docker non-root, env scrub, không socket/SSH/token |
| Model gateway/credential broker bị bypass | Critical | Scoped lease, expiry/revoke, raw-key deny và safety contract tests |
| Nested sandbox tạo thêm security authority | Cao | Outer WorkspaceManager duy nhất; OpenHands LocalWorkspace trong container |
| External protocol drift hoặc adapter deadlock | Cao | Capability probe, version pin, bounded events, WAITING_FOR_APPROVAL state |
| N-best attempts nhiễm patch lẫn nhau | Cao | Snapshot riêng từ cùng base SHA, PatchCollector trước verification |
| State machine quá rộng | Cao | SQLite single-node, transition table nhỏ, checkpoint |
| Tree-sitter/engine adapters tăng dependency | Trung bình | Optional extras, NativeEngine default |
| Verification chậm/đắt | Cao | baseline cache, targeted tests, budgets, cost metrics |
| LLM provider race | Cao | immutable LLMRequest, concurrency tests |
| Opportunity score overfit | Trung bình | evidence threshold và benchmark fixtures |
| Refactor phá backward compatibility | Cao | adapter layer, migrations, contract tests |
| Documentation lệch runtime | Trung bình | docs update cùng release checkpoint |

## 6. Open questions trước Task 1

1. Baseline chính thức là origin/main hay branch Layer C local? Plan mặc định chọn origin/main và không xóa local changes.
2. Live mode có bắt buộc human approval cho mọi PR trong v1 không? Khuyến nghị: có cho đến khi merge/quality evidence đủ.
3. Initial supported languages của DockerWorkspace là Python, JavaScript/TypeScript, Go và Rust hay mở rộng thêm?
4. Có chấp nhận Tree-sitter language packages dưới optional extra không?
5. Có cần remote workspace trong v1 hay chỉ local/Docker single-node?
6. Issue creation có cho phép trong live mode nếu maintainer guideline bắt buộc, hay vẫn phải human approve?
7. License chính thức là MIT hay AGPL-3.0?
8. Model gateway v1 sẽ proxy provider nào trước; provider nào chưa hỗ trợ gateway phải bị deny hay chỉ được chạy shadow?
9. Có cho phép external driver direct-key trong development không? Khuyến nghị: chỉ brokered gateway trong live/review_only.
10. Sau Mini-SWE, owner muốn bật OpenHands SDK hay OpenCode server trước? Codex app-server chỉ mở sau khi protocol pin ổn định.

## 6.1. Source anchors cho external adapters

Các link này chỉ làm evidence cho adapter boundary và capability probe; không phải dependency runtime và không được copy nguyên source vào ContribAI:

- [Codex `exec` CLI](https://github.com/openai/codex/blob/main/codex-rs/exec/src/cli.rs) và [Codex app-server](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)
- [mini-SWE-agent](https://github.com/swe-agent/mini-swe-agent)
- [OpenHands SDK workspace](https://docs.openhands.dev/sdk/arch/workspace), [SDK source](https://github.com/OpenHands/software-agent-sdk/) và [agent server](https://docs.openhands.dev/sdk/guides/agent-server/overview)
- [OpenCode server](https://opencode.ai/docs/server/), [SDK](https://opencode.ai/docs/sdk/) và [permissions](https://opencode.ai/docs/permissions)
- [Aider RepoMap](https://aider.chat/docs/repomap.html), [Git integration](https://aider.chat/docs/git.html) và [Agentless](https://github.com/openautocoder/agentless)

## 7. Handoff

Thứ tự triển khai bắt buộc:

~~~text
P0 Safety
→ State + Publisher Gate
→ Workspace Isolation
→ Credential Broker + Engine Runtime Contract
→ Context/Localization
→ Native Driver + PatchCollector + Verification
→ Opportunity Intelligence
→ Mini-SWE → OpenHands SDK → OpenCode Server → Codex Exec → Codex App-Server
→ Outcome Learning
→ Cleanup
~~~

Không đảo thứ tự bằng cách ưu tiên thêm model, fork coding agent khác, microservice hoặc sửa lint trước khi một write authority duy nhất được chứng minh bằng test.
