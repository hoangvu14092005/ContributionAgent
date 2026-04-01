# Hướng Dẫn Bảo Mật Token GitHub

## ⚠️ QUAN TRỌNG

**KHÔNG BAO GIỜ** commit các file chứa token/API key lên GitHub:
- `.env`
- `config.yaml`
- Bất kỳ file nào chứa `GITHUB_TOKEN`, `API_KEY`

## Kiểm Tra Trước Khi Push

```bash
# 1. Kiểm tra .gitignore đã có chưa
cat .gitignore | grep -E "\.env|config\.yaml"

# 2. Kiểm tra file nào sẽ được commit
git status

# 3. Kiểm tra xem có file nhạy cảm trong staging không
git diff --cached --name-only | grep -E "\.env|config\.yaml"

# 4. Nếu có, gỡ ra ngay
git reset HEAD .env config.yaml
```

## Nếu Đã Commit Nhầm

```bash
# Xóa file khỏi git history (NGUY HIỂM - backup trước!)
git filter-branch --force --index-filter \
  "git rm --cached --ignore-unmatch .env config.yaml" \
  --prune-empty --tag-name-filter cat -- --all

# Force push (nếu đã push lên remote)
git push origin --force --all
```

## Thu Hồi Token Bị Lộ

1. Vào https://github.com/settings/tokens
2. Tìm token cũ và click "Delete"
3. Tạo token mới với quyền tương tự
4. Cập nhật vào `.env` (KHÔNG commit)

## Cấu Trúc An Toàn

```
✅ .env.example          # Commit được - chỉ có placeholder
✅ config.example.yaml   # Commit được - chỉ có placeholder
❌ .env                  # KHÔNG commit - chứa token thật
❌ config.yaml           # KHÔNG commit - chứa token thật
```

## Kiểm Tra Nhanh

```bash
# Xem file nào đã được track bởi git
git ls-files | grep -E "\.env$|config\.yaml$"

# Nếu có kết quả → ĐÃ BỊ TRACK → Cần xóa khỏi git
```

## GitHub Sẽ Tự Động Thu Hồi

Nếu bạn vô tình push token lên GitHub public repo:
- GitHub sẽ tự động phát hiện và thu hồi token
- Bạn sẽ nhận email cảnh báo
- Token sẽ bị vô hiệu hóa ngay lập tức

## Checklist Trước Mỗi Lần Push

- [ ] Chạy `git status` kiểm tra file nào sẽ commit
- [ ] Đảm bảo `.env` và `config.yaml` KHÔNG có trong danh sách
- [ ] Kiểm tra `.gitignore` đã đầy đủ
- [ ] Chỉ commit `.env.example` và `config.example.yaml`
