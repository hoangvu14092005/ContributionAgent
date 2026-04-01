# Hướng Dẫn Sử Dụng ContribAI - Chi Tiết

## 📚 Mục Lục

1. [Cài Đặt Từ Đầu](#1-cài-đặt-từ-đầu)
2. [Cấu Hình Chi Tiết](#2-cấu-hình-chi-tiết)
3. [Các Lệnh Cơ Bản](#3-các-lệnh-cơ-bản)
4. [Workflow Thực Tế](#4-workflow-thực-tế)
5. [Xử Lý Lỗi](#5-xử-lý-lỗi)

---

## 1. Cài Đặt Từ Đầu

### Bước 1.1: Chuẩn Bị Môi Trường

```bash
# Kiểm tra Python version (cần >= 3.11)
python --version
# hoặc
python3 --version

# Nếu chưa có Python 3.11+, cài đặt:
# Windows: Tải từ https://www.python.org/downloads/
# Mac: brew install python@3.11
# Linux: sudo apt install python3.11
```

### Bước 1.2: Clone Repository

```bash
# Clone về máy
git clone https://github.com/chinhkrb113/ContribAI.git

# Di chuyển vào thư mục
cd ContribAI

# Kiểm tra cấu trúc
ls -la
```

### Bước 1.3: Tạo Virtual Environment

```bash
# Tạo virtual environment
python -m venv venv

# Kích hoạt (Windows)
venv\Scripts\activate

# Kích hoạt (Linux/Mac)
source venv/bin/activate

# Kiểm tra đã kích hoạt chưa (sẽ thấy (venv) ở đầu dòng)
which python
```

### Bước 1.4: Cài Đặt Dependencies

```bash
# Upgrade pip
pip install --upgrade pip

# Cài đặt ContribAI
pip install -e ".[dev]"

# Kiểm tra cài đặt
contribai --version
contribai --help
```

**Nếu gặp lỗi cài đặt:**

```bash
# Lỗi: Microsoft Visual C++ required (Windows)
# Giải pháp: Cài Visual Studio Build Tools
# https://visualstudio.microsoft.com/downloads/

# Lỗi: gcc not found (Linux)
sudo apt install build-essential python3-dev

# Lỗi: command line tools (Mac)
xcode-select --install
```

---

## 2. Cấu Hình Chi Tiết

### Bước 2.1: Tạo GitHub Token

**Chi tiết xem:** [docs/GITHUB_TOKEN_SETUP.md](docs/GITHUB_TOKEN_SETUP.md)

**Các bước nhanh:**

1. Truy cập: https://github.com/settings/tokens/new

2. Điền thông tin:
   - **Note:** `ContribAI - Autonomous Contributor`
   - **Expiration:** `90 days`

3. Chọn scopes (quyền):
   ```
   ✅ repo (full control)
   ✅ workflow
   ✅ read:org
   ✅ read:user
   ✅ user:email
   ```

4. Click **Generate token**

5. **QUAN TRỌNG:** Copy token ngay (chỉ hiện 1 lần!)
   ```
   ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

### Bước 2.2: Cấu Hình .env File

```bash
# Copy file mẫu
cp .env.example .env

# Mở file để chỉnh sửa
# Windows
notepad .env

# Linux/Mac
nano .env
# hoặc
code .env
```

**Nội dung file .env:**

```bash
# ============================================================================
# GitHub Token (BẮT BUỘC)
# ============================================================================
GITHUB_TOKEN=ghp_paste_token_của_bạn_vào_đây

# ============================================================================
# Custom LLM Configuration (Nếu bạn có self-hosted LLM)
# ============================================================================
CUSTOM_LLM_BASE_URL=http://localhost:20128/v1
CUSTOM_LLM_API_KEY=your_api_key_here

# Model cho từng task
LLM_MODEL_ANALYSIS=ag/gemini-3.1-pro-high
LLM_MODEL_CODE_GEN=gh/claude-sonnet-4.6
LLM_MODEL_REVIEW=cx/gpt-5.4
LLM_MODEL_VALIDATION=kr/claude-sonnet-4.5
LLM_MODEL_ISSUE_SOLVER=gh/claude-sonnet-4.6
LLM_MODEL_COMPRESSION=gh/claude-sonnet-4.6

# ============================================================================
# HOẶC dùng Gemini (Google AI) - Dễ nhất cho người mới
# ============================================================================
# Tạo API key tại: https://makersuite.google.com/app/apikey
# GEMINI_API_KEY=your_gemini_api_key_here

# ============================================================================
# HOẶC dùng OpenAI
# ============================================================================
# Tạo API key tại: https://platform.openai.com/api-keys
# OPENAI_API_KEY=your_openai_api_key_here

# ============================================================================
# HOẶC dùng Anthropic (Claude)
# ============================================================================
# Tạo API key tại: https://console.anthropic.com/settings/keys
# ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

**Lưu ý:**
- Chỉ cần chọn 1 trong các options (Custom, Gemini, OpenAI, hoặc Anthropic)
- Nếu mới bắt đầu, khuyến nghị dùng **Gemini** (dễ setup, giá rẻ)

### Bước 2.3: Cấu Hình config.yaml

```bash
# Copy file mẫu
cp config.example.yaml config.yaml

# Mở để chỉnh sửa
nano config.yaml
```

**Cấu hình cho người mới (dùng Gemini):**

```yaml
github:
  token: ""  # Để trống, sẽ đọc từ .env
  max_repos_per_run: 3  # Bắt đầu với số nhỏ
  max_prs_per_day: 5    # Không spam

llm:
  provider: "gemini"
  model: "gemini-2.5-flash"
  api_key: ""  # Để trống, sẽ đọc từ .env
  temperature: 0.3
  max_tokens: 8192

analysis:
  enabled_analyzers:
    - security      # Tìm lỗi bảo mật
    - code_quality  # Tìm lỗi code
    - docs          # Tìm thiếu docs
  severity_threshold: "medium"  # Chỉ báo lỗi từ medium trở lên
  max_context_tokens: 30000

contribution:
  enabled_types:
    - security_fix
    - code_quality
    - docs_improve
  max_files_per_pr: 5  # Giới hạn số file mỗi PR

discovery:
  languages:
    - python  # Bắt đầu với Python
  stars_range: [100, 1000]  # Repos vừa phải
  min_last_activity_days: 30  # Active gần đây

pipeline:
  max_concurrent_repos: 1  # Xử lý từng repo một
  human_review: false      # Tự động tạo PR
```

**Cấu hình cho người dùng Custom LLM:**

```yaml
llm:
  provider: "custom"
  # Các settings khác sẽ đọc từ .env
```

### Bước 2.4: Test Cấu Hình

```bash
# Kiểm tra cấu hình
contribai info

# Kết quả mong đợi:
# ✅ GitHub token: Valid (user: your-username)
# ✅ LLM provider: gemini (model: gemini-2.5-flash)
# ✅ Database: ~/.contribai/memory.db
# ✅ Config: ./config.yaml
```

**Nếu thấy lỗi:**

```bash
# Lỗi: GitHub token invalid
# → Kiểm tra lại token trong .env

# Lỗi: LLM API key not found
# → Kiểm tra GEMINI_API_KEY trong .env

# Lỗi: Config file not found
# → Đảm bảo có file config.yaml trong thư mục hiện tại
```

---

## 3. Các Lệnh Cơ Bản

### 3.1: Phân Tích 1 Repository (Dry Run)

```bash
# Cú pháp
contribai target <github-url> --dry-run

# Ví dụ
contribai target https://github.com/psf/requests --dry-run
```

**Quá trình:**
1. ✅ Fetch repo info
2. ✅ Analyze code (security, quality, docs)
3. ✅ Generate findings
4. ✅ Generate code fixes
5. ✅ Self-review
6. ✅ Show preview (KHÔNG tạo PR)

**Output mẫu:**
```
📦 Processing: psf/requests
🔬 Analyzing code...
Found 3 issues (analyzed 45 files in 12.3s)
🛠️ Generating fix for: Missing error handling in timeout
✅ Generated contribution: 🔒 Security: Missing error handling in timeout (1 files changed)
🏃 [DRY RUN] Would create PR: 🔒 Security: Missing error handling in timeout
```

### 3.2: Tạo PR Thật

```bash
# Sau khi review dry run OK, tạo PR thật
contribai target https://github.com/owner/repo

# Hoặc với options
contribai target https://github.com/owner/repo \
  --language python \
  --severity high
```

**Quá trình:**
1. Fork repository
2. Create branch: `contribai/fix/security/...`
3. Commit changes
4. Push to fork
5. Create Pull Request
6. Monitor CI/CD

**Output mẫu:**
```
📤 Creating PR...
✅ PR created: https://github.com/owner/repo/pull/123
🔍 Checking PR compliance...
⏳ Waiting for CI checks...
✅ CI passed for PR #123 (3 checks)
```

### 3.3: Hunt Mode - Tự Động Tìm Repos

```bash
# Hunt với dry run (test trước)
contribai hunt --rounds 1 --dry-run

# Hunt thật
contribai hunt --rounds 3 --delay 60

# Options:
# --rounds: Số vòng quét (mỗi vòng tìm ~5-10 repos)
# --delay: Thời gian chờ giữa các vòng (giây)
# --mode: analysis | issues | both
# --dry-run: Preview không tạo PR
```

**Ví dụ output:**
```
🔥 Hunt round 1/3 — python, ★ 100-1000
Found 8 candidate repositories
✅ owner/repo1 — 5 merged PRs, good target!
✅ owner/repo2 — 3 merged PRs, good target!
Processing 2 repos (max 1 concurrent)
📦 Processing: owner/repo1
...
🔥 Hunt round 2/3 — javascript, ★ 500-3000
...
```

### 3.4: Giải Quyết Issues

```bash
# Tìm và giải quyết open issues
contribai solve https://github.com/owner/repo

# Với dry run
contribai solve https://github.com/owner/repo --dry-run
```

**Quá trình:**
1. Fetch open issues (label: good first issue, help wanted, bug)
2. Classify issues (bug, feature, docs, etc.)
3. Analyze codebase
4. Generate multi-file solution
5. Create PR with "Closes #issue_number"

### 3.5: Xem Thống Kê

```bash
# Xem tổng quan
contribai stats

# Output:
# 📊 ContribAI Statistics
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Total PRs created: 45
# ✅ Merged: 32 (71%)
# ⏳ Open: 8 (18%)
# ❌ Closed: 5 (11%)
# 🎯 Success rate: 71%
# 
# Top repositories:
# 1. owner/repo1 - 5 PRs (4 merged)
# 2. owner/repo2 - 3 PRs (3 merged)
# ...
```

### 3.6: Xem Trạng Thái PRs

```bash
# Xem tất cả PRs
contribai status

# Output:
# 📋 Your Pull Requests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PR #123 - owner/repo1
#   Title: 🔒 Security: Fix SQL injection vulnerability
#   Status: ✅ Merged (2 days ago)
#   URL: https://github.com/owner/repo1/pull/123
#
# PR #124 - owner/repo2
#   Title: 📝 Docs: Add missing docstrings
#   Status: ⏳ Open (under review)
#   URL: https://github.com/owner/repo2/pull/124
# ...
```

### 3.7: Dọn Dẹp Forks

```bash
# Xóa forks không còn open PRs
contribai cleanup

# Output:
# 🧹 Cleaning up stale forks...
# Found 5 forks
# ✅ Deleted: owner/repo1 (no open PRs)
# ⏭️ Skipped: owner/repo2 (has 1 open PR)
# ...
# Deleted 3 forks
```

### 3.8: Web Dashboard

```bash
# Khởi động dashboard
contribai serve

# Với custom port
contribai serve --port 9000

# Output:
# 🌐 Starting ContribAI Dashboard...
# ✅ Server running at http://127.0.0.1:8787
# Press Ctrl+C to stop
```

Truy cập: http://localhost:8787

**Dashboard features:**
- 📊 Statistics overview
- 📋 PRs list với status
- 🔍 Findings explorer
- 📝 Logs viewer
- ⚙️ Settings

---

## 4. Workflow Thực Tế

### Workflow 1: Người Mới Bắt Đầu

**Mục tiêu:** Tạo PR đầu tiên thành công

```bash
# Bước 1: Tìm 1 repo nhỏ của chính bạn để test
# Tạo 1 test repo trên GitHub với code có lỗi đơn giản

# Bước 2: Dry run
contribai target https://github.com/your-username/test-repo --dry-run

# Bước 3: Review kết quả
# - Xem findings có đúng không
# - Xem code fix có hợp lý không

# Bước 4: Nếu OK, tạo PR thật
contribai target https://github.com/your-username/test-repo

# Bước 5: Vào GitHub check PR
# - Review code changes
# - Merge nếu OK
# - Hoặc close nếu không OK

# Bước 6: Check stats
contribai stats
```

### Workflow 2: Đóng Góp Vào Open Source

**Mục tiêu:** Tạo PRs cho các repos open source thật

```bash
# Bước 1: Cấu hình discovery
# Chỉnh config.yaml:
discovery:
  languages:
    - python
  stars_range: [100, 1000]  # Repos vừa phải
  min_last_activity_days: 30

# Bước 2: Hunt với dry run
contribai hunt --rounds 1 --dry-run

# Bước 3: Review findings
# - Xem repos nào được chọn
# - Xem findings có hợp lý không

# Bước 4: Hunt thật (bắt đầu với 1 round)
contribai hunt --rounds 1

# Bước 5: Monitor PRs
contribai status

# Bước 6: Respond to reviews
# - Vào GitHub check comments
# - Update code nếu cần
# - Trả lời maintainers

# Bước 7: Tăng dần số rounds
contribai hunt --rounds 3 --delay 60
```

### Workflow 3: Giải Quyết Good First Issues

**Mục tiêu:** Giải quyết issues được label "good first issue"

```bash
# Bước 1: Tìm repos có good first issues
# Trên GitHub search:
# label:"good first issue" language:python stars:100..1000

# Bước 2: Chọn 1 repo và solve
contribai solve https://github.com/owner/repo --dry-run

# Bước 3: Review solution
# - Xem issue được hiểu đúng không
# - Xem solution có hợp lý không

# Bước 4: Solve thật
contribai solve https://github.com/owner/repo

# Bước 5: Check PR
contribai status

# Bước 6: Monitor và respond
# - Check comments từ maintainers
# - Update nếu cần
```

### Workflow 4: Chạy Tự Động Hàng Ngày

**Mục tiêu:** Setup để ContribAI chạy tự động

```bash
# Bước 1: Cấu hình scheduler trong config.yaml
scheduler:
  enabled: true
  cron: "0 9 * * *"  # Chạy lúc 9h sáng mỗi ngày
  timezone: "Asia/Ho_Chi_Minh"
  max_concurrent: 2

# Bước 2: Khởi động scheduler
contribai schedule

# Hoặc dùng systemd (Linux)
# Tạo file: /etc/systemd/system/contribai.service
[Unit]
Description=ContribAI Scheduler
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/path/to/ContribAI
ExecStart=/path/to/venv/bin/contribai schedule
Restart=always

[Install]
WantedBy=multi-user.target

# Enable và start
sudo systemctl enable contribai
sudo systemctl start contribai

# Check status
sudo systemctl status contribai
```

---

## 5. Xử Lý Lỗi

### Lỗi 1: "Bad credentials"

```
Error: Bad credentials
```

**Nguyên nhân:** GitHub token không hợp lệ hoặc hết hạn

**Cách fix:**
```bash
# 1. Kiểm tra token trong .env
cat .env | grep GITHUB_TOKEN

# 2. Test token bằng curl
curl -H "Authorization: token ghp_your_token" \
     https://api.github.com/user

# 3. Nếu lỗi, tạo token mới
# Xem: docs/GITHUB_TOKEN_SETUP.md

# 4. Update .env với token mới
nano .env

# 5. Test lại
contribai info
```

### Lỗi 2: "API rate limit exceeded"

```
Error: API rate limit exceeded for user
```

**Nguyên nhân:** Vượt quá 5000 requests/hour

**Cách fix:**
```bash
# Option 1: Đợi rate limit reset (1 giờ)
# Check khi nào reset:
curl -H "Authorization: token ghp_your_token" \
     https://api.github.com/rate_limit

# Option 2: Giảm concurrent repos
# Chỉnh config.yaml:
pipeline:
  max_concurrent_repos: 1  # Giảm từ 3 xuống 1
  inter_repo_delay_sec: 10.0  # Tăng delay

# Option 3: Giảm số PRs per day
github:
  max_prs_per_day: 5  # Giảm từ 10 xuống 5
```

### Lỗi 3: "LLM connection refused"

```
LLMError: Custom LLM error: Connection refused
```

**Nguyên nhân:** LLM endpoint không chạy hoặc sai URL

**Cách fix:**
```bash
# 1. Kiểm tra LLM server có chạy không
# Nếu dùng custom LLM:
curl http://localhost:20128/v1/models

# 2. Kiểm tra URL trong .env
cat .env | grep CUSTOM_LLM_BASE_URL

# 3. Test endpoint
curl -X POST http://localhost:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your_key" \
  -d '{
    "model": "test",
    "messages": [{"role": "user", "content": "hi"}],
    "max_tokens": 10
  }'

# 4. Nếu dùng Gemini/OpenAI, check API key
# Test Gemini:
curl -X POST "https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key=YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"Hello"}]}]}'
```

### Lỗi 4: "No findings"

```
INFO: No findings for owner/repo
```

**Nguyên nhân:** Code quá tốt hoặc analyzers không tìm thấy vấn đề

**Cách fix:**
```bash
# 1. Thử repo khác (có nhiều lỗi hơn)

# 2. Giảm severity threshold
# Chỉnh config.yaml:
analysis:
  severity_threshold: "low"  # Từ "medium" xuống "low"

# 3. Enable thêm analyzers
analysis:
  enabled_analyzers:
    - security
    - code_quality
    - docs
    - ui_ux
    - performance
    - refactor
    - testing  # Thêm testing analyzer

# 4. Test lại
contribai target https://github.com/owner/repo --dry-run
```

### Lỗi 5: "Fork already exists"

```
Error: Fork already exists
```

**Nguyên nhân:** Đã fork repo này trước đó

**Cách fix:**
```bash
# Option 1: Xóa fork cũ trên GitHub
# Vào https://github.com/your-username/repo
# Settings → Delete this repository

# Option 2: Dùng cleanup command
contribai cleanup

# Option 3: Skip repo này và thử repo khác
```

### Lỗi 6: "Permission denied"

```
Error: Permission denied (publickey)
```

**Nguyên nhân:** SSH key chưa setup hoặc không có quyền

**Cách fix:**
```bash
# ContribAI dùng HTTPS, không cần SSH key
# Nhưng nếu gặp lỗi này:

# 1. Kiểm tra git config
git config --global user.name
git config --global user.email

# 2. Set nếu chưa có
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"

# 3. Test lại
contribai target https://github.com/owner/repo --dry-run
```

### Debug Mode

```bash
# Bật debug logging để xem chi tiết
export LOG_LEVEL=DEBUG

# Chạy command
contribai target https://github.com/owner/repo --dry-run

# Xem logs
tail -f ~/.contribai/logs/contribai.log

# Hoặc xem toàn bộ log
cat ~/.contribai/logs/contribai.log | less

# Tìm lỗi cụ thể
grep ERROR ~/.contribai/logs/contribai.log
grep "Custom LLM call" ~/.contribai/logs/contribai.log
```

---

## 6. Tips & Tricks

### Tip 1: Bắt Đầu Nhỏ

```bash
# Đừng hunt ngay, test trước với 1 repo
contribai target https://github.com/your-test-repo --dry-run

# Sau khi quen, mới hunt
contribai hunt --rounds 1
```

### Tip 2: Monitor Thường Xuyên

```bash
# Check PRs mỗi ngày
contribai status

# Check stats mỗi tuần
contribai stats

# Respond to reviews nhanh
```

### Tip 3: Backup Configuration

```bash
# Backup config
cp config.yaml config.yaml.backup
cp .env .env.backup

# Backup database
cp ~/.contribai/memory.db ~/.contribai/memory.db.backup
```

### Tip 4: Sử Dụng Profiles

```bash
# Tạo nhiều config cho các mục đích khác nhau
cp config.yaml config.security.yaml
cp config.yaml config.docs.yaml

# Dùng config cụ thể
contribai --config config.security.yaml hunt
```

### Tip 5: Giới Hạn Scope

```yaml
# Chỉ focus vào 1 loại contribution
contribution:
  enabled_types:
    - security_fix  # Chỉ fix security

# Hoặc chỉ 1 ngôn ngữ
discovery:
  languages:
    - python  # Chỉ Python
```

---

## 7. Tài Liệu Tham Khảo

- [QUICKSTART.md](QUICKSTART.md) - Quick start guide (English)
- [README.md](README.md) - Project overview
- [docs/GITHUB_TOKEN_SETUP.md](docs/GITHUB_TOKEN_SETUP.md) - GitHub token setup
- [docs/CUSTOM_LLM_SETUP.md](docs/CUSTOM_LLM_SETUP.md) - Custom LLM setup
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - System architecture

---

**Chúc bạn thành công với ContribAI! 🚀**

Nếu có vấn đề, mở issue tại: https://github.com/chinhkrb113/ContribAI/issues
