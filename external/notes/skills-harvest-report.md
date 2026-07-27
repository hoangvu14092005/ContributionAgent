# Báo cáo: Skills có thể lấy về từ các agent framework

> Ngày: 2026-07-27
> Nguồn: 8 submodule trong `external/repos/`

## Tóm tắt nhanh

| Repo | Số skill tìm được | Mức liên quan tới ContribAI |
|------|-------------------:|------------------------------|
| **OpenHands** | 26 skills + 3 SKILL.md | **Cao** — giàu nhất, đúng định dạng YAML frontmatter |
| MetaGPT | 2 skill classes (~20 skills) | Thấp — chỉ là content skill (Summarize, Writer) |
| Haystack | 1 (SkillToolset pattern) | **Trung bình** — tham khảo kiến trúc |
| LangChain | 0 (chỉ AGENTS.md) | Thấp |
| CrewAI | 0 (chỉ AGENTS.md) | Thấp |
| AutoGen | 0 | Không có |
| LlamaIndex | 0 | Không có |
| SWE-agent | 0 | Không có |

## Hiện trạng ContribAI

`.agents/` đã có sẵn:
- **8 agents**: backend-dev, code-reviewer, devops-engineer, product-manager, qa-engineer, security-engineer, tech-lead, technical-writer
- **10 workflows**: debug, deploy, dev, docs, git-flow, release, review, security-audit, setup, test

## Skill đề xuất lấy về (Tier 1 — áp dụng trực tiếp)

### 1. `address_pr_comments.md` — Workflow trả lời comment PR
- **Nguồn**: `external/repos/openhands/skills/address_pr_comments.md`
- **Trigger**: `/address_pr_comments`
- **Inputs**: `PR_URL`, `BRANCH_NAME`
- **Hành động**: Đọc diff nhánh → gọi GitHub API lấy review/comment → xử lý từng comment
- **Tại sao cần**: ContribAI có [contribai/pr/patrol.py](contribai/pr/patrol.py) đang monitor review nhưng chưa có workflow trả lời comment tự động.

### 2. `update_pr_description.md` — Workflow cập nhật mô tả PR
- **Nguồn**: `external/repos/openhands/skills/update_pr_description.md`
- **Trigger**: `/update_pr_description`
- **Inputs**: `PR_URL` (regex-validated), `BRANCH_NAME`
- **Hành động**: So sánh diff với main → đọc PR description hiện tại → cập nhật cho sát với code đã đổi
- **Tại sao cần**: PR description hay bị sót khi code đổi nhiều.

### 3. `update_test.md` — Workflow sửa test cho khớp implementation
- **Nguồn**: `external/repos/openhands/skills/update_test.md`
- **Trigger**: `/update_test`
- **Inputs**: `BRANCH_NAME`, `TEST_COMMAND_TO_RUN`
- **Hành động**: Checkout branch → chạy test → nếu fail vì implementation đúng, sửa test
- **Tại sao cần**: Workflow `test.md` hiện tại chỉ chạy test, chưa có bước sửa test theo code.

### 4. `codereview-roasted.md` — Reviewer "Linus-style"
- **Nguồn**: `external/repos/openhands/skills/codereview-roasted.md`
- **Trigger**: `/codereview-roasted`
- **Persona**: Linus Torvalds — phê bình thẳng thắn, ưu tiên "good taste"
- **Tại sao cần**: Bổ sung cho reviewer hiện tại (đang constructive) — có thêm persona brutal-review khi cần review code từ external repo trước khi PR.

### 5. `agent-builder.md` — Interview + tạo agent mới
- **Nguồn**: `external/repos/openhands/skills/agent-builder.md`
- **Trigger**: `/agent-builder`
- **Hành động**: Hỏi ≤5 câu progressive → tóm tắt yêu cầu → research SDK docs → tạo agent
- **Tại sao cần**: Workflow tạo agent hiện tại chưa có quy trình interview.

### 6. `agent_memory.md` — Quản lý repo memory
- **Nguồn**: `external/repos/openhands/skills/agent_memory.md`
- **Trigger**: `/remember`
- **Pattern**: Lưu `.openhands/microagents/repo.md` cho mỗi repo, có xin phép trước khi ghi
- **Tại sao cần**: ContribAI có [contribai/orchestrator/memory.py](contribai/orchestrator/memory.py) nhưng chưa có workflow cho người dùng quản lý nó.

## Tier 2 — bổ sung có giá trị

| Skill | Nguồn | Mục đích | Ghi chú |
|---|---|---|---|
| `onboarding.md` | openhands/skills/ | First-time user interview + step-by-step plan | Bổ sung cho workflow `setup.md` |
| `add_agent.md` | openhands/skills/ | Template + guidance cho `.agents/agents/*.md` | Mở rộng workflow tạo agent |
| `default-tools.md` | openhands/skills/ | MCP stdio_servers config (mặc định cho mỗi repo) | Hợp với kiến trúc MCP của ContribAI |
| `github.md` | openhands/skills/ | Hướng dẫn dùng `gh` CLI + GitHub API đúng chuẩn | Hữu ích cho mọi task liên quan GitHub |
| `security.md` | openhands/skills/ | Security best practices (HTTPS, secrets, …) | Bổ sung cho `security-engineer.md` |
| `code-review.md` | openhands/skills/ | Review patterns (style, naming, complexity) | Bổ sung cho `code-reviewer.md` |

## Tier 3 — tham khảo kiến trúc

### Haystack `SkillToolset`
- **File**: `external/repos/haystack/haystack/tools/skills/skill_toolset.py`
- **Pattern** (progressive disclosure 3 bước):
  1. `warm_up()` — nạp **tên + description** của mọi skill vào tool description (model biết skill nào tồn tại)
  2. `load_skill(name)` — trả về **full instructions** khi cần + manifest file kèm theo
  3. `read_skill_file(path)` — đọc file bundled trong skill
- **So với ContribAI**: [contribai/analysis/skills.py](contribai/analysis/skills.py) đã có progressive loading — xem thêm pattern này để thống nhất.

## Tier 4 — bỏ qua (niche / ngoài scope)

| Skill | Lý do bỏ |
|---|---|
| `bitbucket.md`, `gitlab.md`, `azure_devops.md` | Multi-platform — không phải focus hiện tại (GitHub-only) |
| `docker.md`, `kubernetes.md` | Infra operations — không trực tiếp cho ContribAI workflow |
| `npm.md`, `ssh.md`, `swift-linux.md`, `pdflatex.md` | Tech-stack specific, ít dùng |
| `fix-py-line-too-long.md`, `fix_test.md` | Quá hẹp |
| `flarglebargle.md` | Joke skill |
| MetaGPT `SummarizeSkill` / `WriterSkill` | Content generation, không phải dev workflow |

## Kế hoạch triển khai đề xuất

1. **Tạo thư mục** `.agents/workflows/` (đã có sẵn) — thêm 6 workflow Tier 1 với format hiện tại của dự án.
2. **Sao chép và Việt-hoá** — dịch ngôn ngữ cho phù hợp (nếu muốn) hoặc giữ nguyên tiếng Anh để consistent.
3. **Đăng ký trigger** trong `contribai/agents/registry.py` để gọi được từ pipeline.
4. **Thêm test** cho từng workflow mới trong `tests/unit/` (đang có 431 tests).
5. **Cập nhật** [AGENTS.md](AGENTS.md) để liệt kê các workflow mới.

## Files đã đọc để ra báo cáo này

- `external/repos/openhands/skills/` (26 file) — chi tiết
- `external/repos/openhands/.agents/skills/` (3 SKILL.md) — cấu trúc mới
- `external/repos/metagpt/metagpt/skills/` — list thư mục
- `external/repos/haystack/haystack/tools/skills/skill_toolset.py` — pattern kiến trúc
- `external/repos/langchain/AGENTS.md` — context-only
- `external/repos/crewai/AGENTS.md` — context-only
- `.agents/agents/*.md`, `.agents/workflows/*.md` — hiện trạng ContribAI