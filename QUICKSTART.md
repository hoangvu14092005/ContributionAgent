# ContribAI - Hướng Dẫn Sử Dụng Nhanh

## Mục Lục
1. [Giới Thiệu](#giới-thiệu)
2. [Cài Đặt](#cài-đặt)
3. [Cấu Hình](#cấu-hình)
4. [Sử Dụng Cơ Bản](#sử-dụng-cơ-bản)
5. [Sử Dụng Nâng Cao](#sử-dụng-nâng-cao)
6. [Troubleshooting](#troubleshooting)

---

## Giới Thiệu

ContribAI là một AI agent tự động đóng góp vào các dự án open source trên GitHub. Nó có thể:

- 🔍 **Phân tích code** - Tìm lỗi bảo mật, chất lượng code, thiếu docs, vấn đề UI/UX
- 🛠️ **Tự động sửa lỗi** - Sinh code fix và tạo Pull Request
- 📋 **Giải quyết issues** - Đọc GitHub issues và code để giải quyết
- 🚀 **Hunt mode** - Quét hàng loạt repos và tạo PRs tự động

---

## Cài Đặt

### Yêu Cầu Hệ Thống

- **Python:** 3.11 trở lên
- **Git:** Để clone repository
- **GitHub Account:** Để tạo PRs
- **LLM API:** Gemini, OpenAI, Anthropic, hoặc self-hosted

### Bước 1: Clone Repository

```bash
git clone https://github.com/chinhkrb113/ContribAI.git
cd ContribAI
```

### Bước 2: Cài Đặt Dependencies

```bash
# Tạo virtual environment (khuyến nghị)
python -m venv venv

# Kích hoạt virtual environment
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

# Cài đặt package
pip install -e ".[dev]"
```

### Bước 3: Kiểm Tra Cài Đặt

```bash
contribai --version
contribai info
```

---

## Cấu Hình

### Bước 1: Tạo GitHub Token

**Xem hướng dẫn chi tiết:** [docs/GITHUB_TOKEN_SETUP.md](docs/GITHUB_TOKEN_SETUP.md)

**Tóm tắt:**
1. Truy cập: https://github.com/settings/tokens/new
2. Chọn scopes: `repo`, `workflow`, `read:org`, `read:user`, `user:email`
3. Generate token và copy

### Bước 2: Cấu Hình Environment Variables

```bash
# Copy file mẫu
cp .env.example .env

# Chỉnh sửa file .env
nano .env
# hoặc
code .env
```

**Nội dung file `.env`:**

```bash
# GitHub Token (BẮT BUỘC)
GITHUB_TOKEN=ghp_your_github_token_here

# Chọn 1 trong các options sau:

# Option 1: Dùng Custom Self-Hosted LLM (Khuyến nghị nếu bạn có)
CUSTOM_LLM_BASE_URL=http://localhost:20128/v1
CUSTOM_LLM_API_KEY=your_api_key
LLM_MODEL_ANALYSIS=ag/gemini-3.1-pro-high
LLM_MODEL_CODE_GEN=gh/claude-sonnet-4.6
LLM_MODEL_REVIEW=cx/gpt-5.4
LLM_MODEL_VALIDATION=kr/claude-sonnet-4.5
LLM_MODEL_ISSUE_SOLVER=gh/claude-sonnet-4.6
LLM_MODEL_COMPRESSION=gh/claude-sonnet-4.6

# Option 2: Dùng Gemini (Google AI)
# GEMINI_API_KEY=your_gemini_api_key

# Option 3: Dùng OpenAI
# OPENAI_API_KEY=your_openai_api_key

# Option 4: Dùng Anthropic (Claude)
# ANTHROPIC_API_KEY=your_anthropic_api_key
```

### Bước 3: Cấu Hình config.yaml

```bash
# Copy file mẫu
cp config.example.yaml config.yaml

# Chỉnh sửa
nano config.yaml
```

**Cấu hình tối thiểu:**

```yaml
github:
  token: ""  # Sẽ đọc từ .env
  max_repos_per_run: 5
  max_prs_per_day: 10

llm:
  # Chọn provider phù hợp
  provider: "custom"  # hoặc "gemini", "openai", "anthropic"
  
  # Nếu dùng custom
  # custom_base_url và custom_models sẽ đọc từ .env
  
  # Nếu dùng gemini/openai/anthropic
  # provider: "gemini"
  # model: "gemini-2.5-flash"
  # api_key: ""  # Sẽ đọc từ .env

analysis:
  enabled_analyzers:
    - security
    - code_quality
    - docs
    - ui_ux
  severity_threshold: "medium"

discovery:
  languages:
    - python
    - javascript
    - typescript
  stars_range: [50, 10000]
```

---

## Sử Dụng Cơ Bản

### 1. Test Cấu Hình

```bash
# Kiểm tra thông tin hệ thống
contribai info

# Kết quả mong đợi:
# ✅ GitHub token: Valid
# ✅ LLM provider: custom (http://localhost:20128/v1)
# ✅ Database: ~/.contribai/memory.db
```

### 2. Phân Tích 1 Repository (Dry Run)

```bash
# Chỉ phân tích, không tạo PR
contribai target https://github.com/owner/repo --dry-run
```

**Kết quả:**
- Phân tích code
- Tìm findings (lỗi, vấn đề)
- Sinh code fix
- Hiển thị preview (không tạo PR thật)

### 3. Tạo PR Cho 1 Repository

```bash
# Phân tích và tạo PR thật
contribai target https://github.com/owner/repo
```

**Quá trình:**
1. Fork repository
2. Phân tích code
3. Sinh code fix
4. Tạo branch mới
5. Commit changes
6. Tạo Pull Request
7. Monitor CI/CD

### 4. Hunt Mode - Tự Động Tìm Repos

```bash
# Tìm repos và tạo PRs tự động
contribai hunt

# Với options
contribai hunt --rounds 3 --delay 30
# rounds: Số vòng quét (mỗi vòng tìm 5-10 repos)
# delay: Thời gian chờ giữa các vòng (giây)
```

### 5. Giải Quyết Issues

```bash
# Tìm và giải quyết open issues
contribai solve https://github.com/owner/repo
```

---

## Sử Dụng Nâng Cao

### Hunt Mode Chi Tiết

```bash
# Hunt với cấu hình chi tiết
contribai hunt \
  --rounds 5 \
  --delay 60 \
  --mode both \
  --dry-run

# Options:
# --rounds: Số vòng quét (default: 5)
# --delay: Delay giữa các vòng (giây, default: 30)
# --mode: analysis | issues | both (default: both)
# --dry-run: Preview không tạo PR
```

### Lọc Theo Ngôn Ngữ

```bash
# Chỉ phân tích Python repos
contribai run --language python

# Nhiều ngôn ngữ
contribai run --language python --language javascript
```

### Xem Thống Kê

```bash
# Xem tổng quan
contribai stats

# Kết quả:
# 📊 Total PRs created: 45
# ✅ Merged: 32 (71%)
# ⏳ Open: 8 (18%)
# ❌ Closed: 5 (11%)
# 🎯 Success rate: 71%
```

### Xem Trạng Thái PRs

```bash
# Xem tất cả PRs đã tạo
contribai status

# Kết quả:
# PR #123 - owner/repo - ✅ Merged
# PR #124 - owner/repo2 - ⏳ Open
# PR #125 - owner/repo3 - 🔄 Under review
```

### Dọn Dẹp Forks

```bash
# Xóa các forks không còn dùng
contribai cleanup

# Xóa forks không có open PRs
```

### Web Dashboard

```bash
# Khởi động web dashboard
contribai serve

# Truy cập: http://localhost:8787
# Dashboard hiển thị:
# - Thống kê PRs
# - Repos đã phân tích
# - Findings
# - Logs
```

### Scheduler - Chạy Tự Động

```bash
# Chạy theo lịch (cron)
contribai schedule --cron "0 */6 * * *"

# Chạy mỗi 6 giờ
# Hoặc cấu hình trong config.yaml:
```

```yaml
scheduler:
  enabled: true
  cron: "0 */6 * * *"  # Mỗi 6 giờ
  timezone: "UTC"
  max_concurrent: 3
```

---

## Workflow Thực Tế

### Workflow 1: Test Trên 1 Repo Nhỏ

```bash
# Bước 1: Tìm 1 repo nhỏ để test
# Ví dụ: https://github.com/your-username/test-repo

# Bước 2: Dry run để xem findings
contribai target https://github.com/your-username/test-repo --dry-run

# Bước 3: Review kết quả
# - Xem findings có hợp lý không
# - Xem code fix có đúng không

# Bước 4: Tạo PR thật nếu OK
contribai target https://github.com/your-username/test-repo
```

### Workflow 2: Hunt Repos Python

```bash
# Bước 1: Cấu hình trong config.yaml
discovery:
  languages:
    - python
  stars_range: [100, 1000]  # Repos vừa phải
  min_last_activity_days: 30  # Active gần đây

# Bước 2: Dry run hunt
contribai hunt --rounds 1 --dry-run

# Bước 3: Review findings

# Bước 4: Hunt thật
contribai hunt --rounds 3 --delay 60
```

### Workflow 3: Giải Quyết Issues

```bash
# Bước 1: Tìm repos có "good first issue"
# GitHub search: label:"good first issue" language:python

# Bước 2: Solve issues
contribai solve https://github.com/owner/repo

# Bước 3: Monitor PRs
contribai status
```

---

## Cấu Hình Nâng Cao

### Multi-Model Routing (Gemini Only)

```yaml
multi_model:
  enabled: true
  strategy: "balanced"  # performance | balanced | economy
```

Tự động chọn model Gemini phù hợp cho từng task:
- Analysis → gemini-3-flash
- Code Gen → gemini-3-flash hoặc gemini-3.1-pro
- Planning → gemini-3.1-pro

### Sandbox Execution

```yaml
sandbox:
  enabled: true  # Yêu cầu Docker
  timeout: 30
  docker_image: "python:3.11-slim"
```

Validate code trong Docker container trước khi tạo PR.

### Pipeline Configuration

```yaml
pipeline:
  max_concurrent_repos: 3  # Số repos xử lý song song
  timeout_per_repo_sec: 300  # Timeout mỗi repo
  inter_repo_delay_sec: 5.0  # Delay giữa các repos
  human_review: false  # true = pause để review trước khi tạo PR
```

### Notifications

```yaml
notifications:
  slack_webhook: "https://hooks.slack.com/..."
  discord_webhook: "https://discord.com/api/webhooks/..."
  on_merge: true
  on_close: true
  on_run_complete: true
```

---

## Troubleshooting

### Lỗi: "Bad credentials"

```
Error: Bad credentials
```

**Nguyên nhân:** GitHub token không hợp lệ

**Giải pháp:**
1. Kiểm tra token trong `.env`
2. Tạo token mới: [docs/GITHUB_TOKEN_SETUP.md](docs/GITHUB_TOKEN_SETUP.md)
3. Đảm bảo token có đủ quyền

### Lỗi: "API rate limit exceeded"

```
Error: API rate limit exceeded
```

**Nguyên nhân:** Vượt quá 5000 requests/hour

**Giải pháp:**
1. Đợi 1 giờ (rate limit reset)
2. Giảm `max_concurrent_repos` trong config.yaml
3. Tăng `inter_repo_delay_sec`

### Lỗi: "LLM connection refused"

```
LLMError: Custom LLM error: Connection refused
```

**Nguyên nhân:** LLM endpoint không chạy

**Giải pháp:**
1. Kiểm tra LLM server có đang chạy không
2. Test endpoint:
```bash
curl -X POST http://localhost:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"test","messages":[{"role":"user","content":"hi"}]}'
```
3. Kiểm tra `CUSTOM_LLM_BASE_URL` trong `.env`

### Lỗi: "No findings"

```
INFO: No findings for owner/repo
```

**Nguyên nhân:** Code quá tốt hoặc analyzers không tìm thấy vấn đề

**Giải pháp:**
1. Thử repo khác
2. Giảm `severity_threshold` trong config.yaml
3. Enable thêm analyzers:
```yaml
analysis:
  enabled_analyzers:
    - security
    - code_quality
    - docs
    - ui_ux
    - performance
    - refactor
    - testing
```

### Lỗi: "Fork already exists"

```
Error: Fork already exists
```

**Nguyên nhân:** Đã fork repo này trước đó

**Giải pháp:**
1. Xóa fork cũ trên GitHub
2. Hoặc dùng `contribai cleanup` để dọn dẹp

### Debug Mode

```bash
# Bật debug logging
export LOG_LEVEL=DEBUG
contribai target https://github.com/owner/repo --dry-run

# Xem logs chi tiết
tail -f ~/.contribai/logs/contribai.log
```

---

## Best Practices

### 1. Bắt Đầu Với Dry Run

```bash
# Luôn test với --dry-run trước
contribai target https://github.com/owner/repo --dry-run
```

### 2. Giới Hạn PRs Per Day

```yaml
github:
  max_prs_per_day: 10  # Không spam
```

### 3. Chọn Repos Phù Hợp

```yaml
discovery:
  stars_range: [100, 5000]  # Không quá nhỏ, không quá lớn
  min_last_activity_days: 30  # Active gần đây
```

### 4. Monitor PRs

```bash
# Kiểm tra PRs thường xuyên
contribai status

# Respond to reviews nhanh
```

### 5. Respect Maintainers

- Đọc CONTRIBUTING.md trước khi tạo PR
- Không spam nhiều PRs cùng lúc
- Close PR nếu maintainer không muốn
- Respond to feedback nhanh chóng

### 6. Backup Configuration

```bash
# Backup config và database
cp config.yaml config.yaml.backup
cp ~/.contribai/memory.db ~/.contribai/memory.db.backup
```

---

## Tài Liệu Tham Khảo

- [README.md](README.md) - Tổng quan dự án
- [docs/GITHUB_TOKEN_SETUP.md](docs/GITHUB_TOKEN_SETUP.md) - Hướng dẫn tạo GitHub token
- [docs/CUSTOM_LLM_SETUP.md](docs/CUSTOM_LLM_SETUP.md) - Cấu hình custom LLM
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - Kiến trúc hệ thống
- [CUSTOM_LLM_CHANGES.md](CUSTOM_LLM_CHANGES.md) - Thay đổi custom LLM

---

## Ví Dụ Thực Tế

### Ví Dụ 1: Phân Tích Repo Python

```bash
# 1. Cấu hình
cat > config.yaml << EOF
github:
  max_prs_per_day: 5

llm:
  provider: "custom"

analysis:
  enabled_analyzers:
    - security
    - code_quality
  severity_threshold: "medium"

discovery:
  languages:
    - python
  stars_range: [100, 1000]
EOF

# 2. Dry run
contribai target https://github.com/psf/requests --dry-run

# 3. Review findings
# 4. Tạo PR nếu OK
contribai target https://github.com/psf/requests
```

### Ví Dụ 2: Hunt JavaScript Repos

```bash
# 1. Cấu hình
discovery:
  languages:
    - javascript
    - typescript
  stars_range: [500, 5000]

# 2. Hunt
contribai hunt --rounds 2 --delay 60 --mode analysis

# 3. Monitor
contribai status
```

### Ví Dụ 3: Solve Good First Issues

```bash
# 1. Tìm repos có good first issues
# GitHub: label:"good first issue" language:python stars:100..1000

# 2. Solve
contribai solve https://github.com/owner/repo

# 3. Check PR
contribai status
```

---

## Câu Hỏi Thường Gặp (FAQ)

**Q: ContribAI có miễn phí không?**
A: Code là open source theo MIT, nhưng bạn cần trả phí cho LLM API (Gemini, OpenAI, etc.) hoặc tự host.

**Q: Tôi cần bao nhiêu tiền cho LLM API?**
A: Phụ thuộc vào usage. Ước tính ~$0.01-0.05 per PR với Gemini Flash.

**Q: ContribAI có tự động merge PRs không?**
A: KHÔNG. Chỉ tạo PRs, maintainers sẽ review và merge.

**Q: Tôi có thể dùng cho private repos không?**
A: Có, nếu GitHub token có quyền `repo`.

**Q: ContribAI có an toàn không?**
A: Có nhiều safety checks (validation, self-review, CI monitoring), nhưng vẫn nên review PRs trước khi merge.

**Q: Tôi có thể customize analyzers không?**
A: Có, xem [Plugin System](README.md#plugin-system).

---

## Support

- **Issues:** https://github.com/chinhkrb113/ContribAI/issues
- **Discussions:** https://github.com/chinhkrb113/ContribAI/discussions
- **Email:** [your-email]

---

**Happy Contributing! 🚀**
