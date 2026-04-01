# Hướng Dẫn Setup Nhanh

## ✅ Đã hoàn thành:
1. ✅ Cài đặt Python dependencies
2. ✅ Cài đặt ContribAI package
3. ✅ Tạo file config.yaml
4. ✅ Tạo file .env

## ⚠️ Cần làm tiếp:

### Bước 1: Tạo GitHub Token

1. Truy cập: https://github.com/settings/tokens/new

2. Điền thông tin:
   - **Token name**: `ContribAI`
   - **Expiration**: `90 days`
   
3. Chọn scopes (quyền):
   ```
   ✅ repo (full control)
   ✅ workflow
   ✅ read:org
   ✅ read:user
   ✅ user:email
   ```

4. Click **Generate token**

5. Copy token (dạng: `ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`)

### Bước 2: Cập nhật file .env

Mở file `.env` và thay thế:

```bash
# Thay dòng này:
GITHUB_TOKEN=ghp_your_github_token_here

# Bằng token thật của bạn:
GITHUB_TOKEN=ghp_paste_token_của_bạn_vào_đây
```

**Lưu file .env**

### Bước 3: Test cấu hình

```bash
# Kiểm tra config
contribai config

# Nếu thấy lỗi "GitHub token not configured", kiểm tra lại .env
```

### Bước 4: Chạy Hunt Mode (Dry Run)

```bash
# Test với dry-run (không tạo PR thật)
contribai hunt --rounds 1 --dry-run
```

**Kết quả mong đợi:**
- ✅ Tìm repos trên GitHub
- ✅ Phân tích code
- ✅ Tìm findings
- ✅ Sinh code fix
- ✅ Hiển thị preview (không tạo PR)

### Bước 5: Chạy Hunt Mode Thật

```bash
# Tạo PR thật
contribai hunt --rounds 1
```

**Quá trình:**
1. Tìm repos phù hợp
2. Fork repos
3. Phân tích code
4. Sinh code fix
5. Tạo branch
6. Commit changes
7. Tạo Pull Request
8. Monitor CI/CD

### Bước 6: Xem kết quả

```bash
# Xem PRs đã tạo
contribai status

# Xem thống kê
contribai stats
```

## 🔧 Troubleshooting

### Lỗi: "GitHub token not configured"

**Nguyên nhân:** Token chưa được set trong .env

**Giải pháp:**
1. Kiểm tra file .env có tồn tại không
2. Kiểm tra token có đúng format không (bắt đầu bằng `ghp_`)
3. Đảm bảo không có khoảng trắng thừa

### Lỗi: "Bad credentials"

**Nguyên nhân:** Token không hợp lệ hoặc hết hạn

**Giải pháp:**
1. Tạo token mới tại: https://github.com/settings/tokens/new
2. Cập nhật vào .env
3. Test lại

### Lỗi: "LLM connection refused"

**Nguyên nhân:** LLM endpoint không chạy

**Giải pháp:**
1. Kiểm tra LLM server có đang chạy tại `http://localhost:20128/v1`
2. Test endpoint:
```bash
curl http://localhost:20128/v1/models
```
3. Nếu không có LLM server, đổi sang dùng Gemini:
   - Tạo API key tại: https://makersuite.google.com/app/apikey
   - Cập nhật config.yaml:
   ```yaml
   llm:
     provider: "gemini"
     model: "gemini-2.5-flash"
   ```
   - Cập nhật .env:
   ```bash
   GEMINI_API_KEY=your_gemini_api_key
   ```

## 📚 Tài liệu chi tiết

- [QUICKSTART.md](QUICKSTART.md) - Quick start guide
- [HUONG_DAN_SU_DUNG.md](HUONG_DAN_SU_DUNG.md) - Hướng dẫn chi tiết
- [docs/GITHUB_TOKEN_SETUP.md](docs/GITHUB_TOKEN_SETUP.md) - Setup GitHub token
- [docs/CUSTOM_LLM_SETUP.md](docs/CUSTOM_LLM_SETUP.md) - Setup custom LLM

## 🚀 Commands hữu ích

```bash
# Xem tất cả commands
contribai --help

# Xem config hiện tại
contribai config

# Test với 1 repo cụ thể
contribai target https://github.com/owner/repo --dry-run

# Hunt mode (tự động tìm repos)
contribai hunt --rounds 3 --delay 60

# Giải quyết issues
contribai solve https://github.com/owner/repo

# Xem PRs
contribai status

# Xem stats
contribai stats

# Web dashboard
contribai serve
# Truy cập: http://localhost:8787
```

## ✨ Bước tiếp theo

Sau khi setup xong:

1. **Test với repo nhỏ của bạn:**
   ```bash
   contribai target https://github.com/your-username/test-repo --dry-run
   ```

2. **Hunt repos Python:**
   ```bash
   contribai hunt --rounds 1
   ```

3. **Monitor PRs:**
   ```bash
   contribai status
   ```

4. **Respond to reviews:**
   - Vào GitHub check comments
   - Update code nếu cần

**Chúc bạn thành công! 🎉**
