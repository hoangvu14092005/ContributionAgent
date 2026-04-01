# GitHub Token Setup Guide

## Tạo GitHub Personal Access Token cho ContribAI

ContribAI cần GitHub token với các quyền phù hợp để có thể:
- Đọc thông tin repositories
- Fork repositories
- Tạo branches và commits
- Tạo Pull Requests
- Đọc và comment trên issues

## Bước 1: Truy cập GitHub Settings

1. Đăng nhập vào GitHub
2. Click vào avatar (góc trên bên phải) → **Settings**
3. Scroll xuống dưới, click **Developer settings** (menu bên trái)
4. Click **Personal access tokens** → **Tokens (classic)**
5. Click **Generate new token** → **Generate new token (classic)**

**Hoặc truy cập trực tiếp:** https://github.com/settings/tokens/new

## Bước 2: Cấu hình Token

### Token Name (Note)
```
ContribAI - Autonomous Contributor
```

### Expiration
Chọn thời hạn phù hợp:
- **30 days** - Nếu bạn test ngắn hạn
- **90 days** - Khuyến nghị cho sử dụng thường xuyên
- **No expiration** - Chỉ dùng nếu bạn hiểu rủi ro bảo mật

### Select Scopes (Quyền cần thiết)

#### ✅ Bắt buộc (Required):

**`repo` (Full control of private repositories)**
- ✅ `repo:status` - Access commit status
- ✅ `repo_deployment` - Access deployment status
- ✅ `public_repo` - Access public repositories
- ✅ `repo:invite` - Access repository invitations
- ✅ `security_events` - Read and write security events

**Lý do:** ContribAI cần fork repos, tạo branches, commits và PRs

**`workflow` (Update GitHub Action workflows)**
- ✅ `workflow` - Update GitHub Action workflows

**Lý do:** Một số repos có CI/CD workflows cần được trigger

**`write:discussion` (Write access to discussions)**
- ✅ `read:discussion` - Read team discussions
- ✅ `write:discussion` - Write team discussions

**Lý do:** Để tương tác với discussions nếu repo sử dụng

#### ✅ Khuyến nghị (Recommended):

**`read:org` (Read org and team membership)**
- ✅ `read:org` - Read org and team membership

**Lý do:** Để hiểu cấu trúc organization và team permissions

**`gist` (Create gists)**
- ✅ `gist` - Create gists

**Lý do:** Hữu ích để share code snippets khi cần

**`read:user` (Read user profile data)**
- ✅ `read:user` - Read ALL user profile data
- ✅ `user:email` - Access user email addresses

**Lý do:** Để sign commits với thông tin user chính xác

#### ❌ Không cần thiết (Not Required):

- ❌ `admin:repo_hook` - Không cần quản lý webhooks
- ❌ `admin:org` - Không cần quyền admin organization
- ❌ `delete_repo` - Không bao giờ xóa repos
- ❌ `admin:gpg_key` - Không cần quản lý GPG keys
- ❌ `admin:ssh_signing_key` - Không cần SSH signing

## Bước 3: Generate Token

1. Scroll xuống dưới cùng
2. Click **Generate token**
3. **QUAN TRỌNG:** Copy token ngay lập tức (chỉ hiển thị 1 lần!)

Token sẽ có dạng:
```
ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

## Bước 4: Lưu Token vào .env

```bash
# Mở file .env trong project
nano .env

# Hoặc
code .env
```

Thêm token vào file:
```bash
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**Lưu ý bảo mật:**
- ❌ KHÔNG commit file `.env` vào git
- ❌ KHÔNG share token với người khác
- ❌ KHÔNG đăng token lên public
- ✅ File `.env` đã được thêm vào `.gitignore`

## Bước 5: Kiểm tra Token

### Cách 1: Dùng curl
```bash
curl -H "Authorization: token ghp_your_token_here" \
     https://api.github.com/user
```

Nếu thành công, bạn sẽ thấy thông tin user của mình.

### Cách 2: Dùng GitHub CLI
```bash
# Nếu đã cài gh CLI
gh auth status
```

### Cách 3: Test với ContribAI
```bash
# Test xem token có hoạt động không
contribai info

# Hoặc test với một repo cụ thể
contribai target https://github.com/owner/repo --dry-run
```

## Quyền Token Chi Tiết

### Tóm tắt quyền tối thiểu:

```
✅ repo (full)
✅ workflow
✅ read:org
✅ read:user
✅ user:email
```

### Giải thích chi tiết:

| Scope | Mục đích | Bắt buộc? |
|-------|----------|-----------|
| `repo` | Fork, clone, push code, tạo PRs | ✅ Bắt buộc |
| `workflow` | Trigger GitHub Actions | ✅ Bắt buộc |
| `read:org` | Đọc thông tin organization | ⚠️ Khuyến nghị |
| `read:user` | Đọc profile để sign commits | ⚠️ Khuyến nghị |
| `user:email` | Lấy email để sign commits | ⚠️ Khuyến nghị |
| `write:discussion` | Comment trên discussions | ⚠️ Tùy chọn |
| `gist` | Tạo gists để share code | ⚠️ Tùy chọn |

## Fine-grained Personal Access Token (Beta)

GitHub cũng hỗ trợ Fine-grained tokens với quyền chi tiết hơn:

### Tạo Fine-grained Token:

1. Vào **Settings** → **Developer settings**
2. Click **Personal access tokens** → **Fine-grained tokens**
3. Click **Generate new token**

### Cấu hình:

**Token name:** `ContribAI`

**Expiration:** 90 days

**Repository access:**
- ✅ **Public Repositories (read and write)**
- Hoặc chọn specific repositories nếu chỉ muốn test

**Permissions:**

**Repository permissions:**
- ✅ **Contents:** Read and write
- ✅ **Pull requests:** Read and write
- ✅ **Issues:** Read and write
- ✅ **Metadata:** Read-only (auto-selected)
- ✅ **Workflows:** Read and write
- ✅ **Commit statuses:** Read and write

**Account permissions:**
- ✅ **Email addresses:** Read-only
- ✅ **Profile:** Read-only

## Troubleshooting

### Lỗi: "Bad credentials"
```
Error: Bad credentials
```
**Giải pháp:**
- Kiểm tra token có đúng không (copy đầy đủ)
- Token có bị expire chưa
- Tạo token mới nếu cần

### Lỗi: "Resource not accessible by integration"
```
Error: Resource not accessible by integration
```
**Giải pháp:**
- Token thiếu quyền `repo` hoặc `workflow`
- Tạo lại token với đầy đủ quyền

### Lỗi: "API rate limit exceeded"
```
Error: API rate limit exceeded
```
**Giải pháp:**
- Đợi 1 giờ (rate limit reset)
- Hoặc giảm `max_concurrent_repos` trong config.yaml
- Token authenticated có limit 5000 requests/hour

### Token bị revoke
Nếu GitHub phát hiện token bị leak (commit lên public repo), token sẽ tự động bị revoke.

**Giải pháp:**
- Tạo token mới
- Đảm bảo `.env` trong `.gitignore`
- Không bao giờ commit `.env` vào git

## Best Practices

### 1. Sử dụng nhiều tokens cho nhiều mục đích
```bash
# Token cho development
GITHUB_TOKEN_DEV=ghp_dev_token

# Token cho production
GITHUB_TOKEN_PROD=ghp_prod_token
```

### 2. Rotate tokens định kỳ
- Tạo token mới mỗi 90 ngày
- Revoke token cũ sau khi migrate

### 3. Giới hạn quyền
- Chỉ cấp quyền cần thiết
- Dùng Fine-grained tokens nếu có thể

### 4. Monitor token usage
- Kiểm tra **Settings** → **Developer settings** → **Personal access tokens**
- Xem "Last used" để biết token có đang được dùng

### 5. Backup token
- Lưu token vào password manager (1Password, Bitwarden, etc.)
- KHÔNG lưu trong plaintext file trên máy

## Alternative: GitHub CLI

Nếu bạn đã cài GitHub CLI (`gh`), ContribAI có thể tự động lấy token:

```bash
# Cài GitHub CLI
# macOS
brew install gh

# Windows
winget install GitHub.cli

# Linux
sudo apt install gh

# Authenticate
gh auth login

# ContribAI sẽ tự động dùng token từ gh CLI
# Không cần set GITHUB_TOKEN trong .env
```

ContribAI sẽ tự động fallback theo thứ tự:
1. `GITHUB_TOKEN` trong `.env`
2. `gh auth token` (nếu có gh CLI)
3. Báo lỗi nếu không tìm thấy

## Security Checklist

- [ ] Token được lưu trong `.env` (không commit)
- [ ] `.env` có trong `.gitignore`
- [ ] Token có expiration date hợp lý
- [ ] Chỉ cấp quyền cần thiết
- [ ] Token được backup an toàn
- [ ] Đã test token hoạt động
- [ ] Biết cách revoke token nếu cần

## Revoke Token

Nếu token bị leak hoặc không dùng nữa:

1. Vào **Settings** → **Developer settings** → **Personal access tokens**
2. Tìm token cần revoke
3. Click **Delete** hoặc **Revoke**
4. Tạo token mới nếu cần

## Tham khảo

- [GitHub Docs: Creating a personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token)
- [GitHub Docs: Token scopes](https://docs.github.com/en/developers/apps/building-oauth-apps/scopes-for-oauth-apps)
- [GitHub CLI](https://cli.github.com/)
