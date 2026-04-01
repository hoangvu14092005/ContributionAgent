# Hướng Dẫn Pull Code Và Giữ Custom LLM Configuration

> Tài liệu này hướng dẫn cách pull code mới từ remote repository trong khi vẫn giữ nguyên các customization cho LLM tự host.

## 📋 Tổng Quan

Khi pull code mới, có thể xảy ra conflict giữa code upstream và các thay đổi custom LLM của bạn. Tài liệu này cung cấp quy trình an toàn để xử lý.

## 🔍 Các File Custom LLM Cần Bảo Vệ

### 1. File Cấu Hình
- `config.yaml` - Cấu hình LLM endpoints
- `.env` - API keys và credentials
- `config.example.yaml` - Template cấu hình (có thể bị conflict)

### 2. File Code Đã Custom
- `contribai/llm/provider.py` - Custom LLM provider logic
- `contribai/core/config.py` - Config dataclasses
- `contribai/generator/engine.py` - Code generation với custom LLM
- `contribai/analysis/analyzer.py` - Analysis với custom LLM
- `contribai/analysis/context_compressor.py` - Context compression
- `contribai/orchestrator/pipeline.py` - Pipeline integration
- `contribai/issues/solver.py` - Issue solving
- `contribai/pr/manager.py` - PR management
- `contribai/github/guidelines.py` - Guidelines adaptation

### 3. File Tài Liệu Custom
- `CUSTOM_LLM_CHANGES.md` - Log các thay đổi
- `docs/CUSTOM_LLM_SETUP.md` - Hướng dẫn setup
- `HUONG_DAN_SU_DUNG.md` - Hướng dẫn tiếng Việt
- `QUICKSTART.md` - Quick start guide
- `SETUP_INSTRUCTIONS.md` - Setup instructions
- `docs/GITHUB_TOKEN_SETUP.md` - GitHub token setup
- `.env.example` - Environment template
- `run.txt` - Run commands

## 🚀 Quy Trình Pull Code An Toàn

### Bước 1: Kiểm Tra Trạng Thái

```bash
git status
```

Xem các file đã thay đổi. Nếu có thay đổi chưa commit, tiếp tục bước 2.

### Bước 2: Stash (Lưu Tạm) Các Thay Đổi

```bash
git stash push -m "Backup custom LLM config before pull"
```

Lệnh này sẽ lưu tạm TẤT CẢ các thay đổi của bạn.

### Bước 3: Pull Code Mới

```bash
git pull origin main
```

Hoặc nếu bạn đang ở branch khác:

```bash
git pull origin <branch-name>
```

### Bước 4: Apply Lại Các Thay Đổi

```bash
git stash pop
```

Lệnh này sẽ apply lại các thay đổi đã lưu. Có thể xảy ra conflict.

### Bước 5: Xử Lý Conflicts

Nếu có conflict, Git sẽ báo:
```
CONFLICT (content): Merge conflict in <file>
```

#### 5.1. Xác Định Các File Bị Conflict

```bash
git status
```

Tìm các file có dòng:
```
both modified:   <file>
```

#### 5.2. Hiểu Conflict Markers

Trong file bị conflict, bạn sẽ thấy:

```python
<<<<<<< Updated upstream
# Code từ remote (code mới pull về)
=======
# Code của bạn (custom LLM)
>>>>>>> Stashed changes
```

#### 5.3. Giải Quyết Conflict

**NGUYÊN TẮC QUAN TRỌNG:**
- Giữ code custom LLM của bạn (phần `Stashed changes`)
- Chỉ merge logic mới từ upstream nếu không ảnh hưởng đến custom LLM
- Xóa tất cả conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)

**Ví dụ Conflict Thường Gặp:**

##### Conflict 1: `contribai/generator/engine.py`

```python
<<<<<<< Updated upstream
            response = await self._llm.complete(
                prompt_with_hint, system=system, temperature=0.2
            )
=======
            # Set task type for custom provider
            if hasattr(self._llm, 'set_task'):
                self._llm.set_task('code_gen')

            response = await self._llm.complete(prompt, system=system, temperature=0.2)
>>>>>>> Stashed changes
```

**Giải pháp:** Giữ phần custom (Stashed changes) vì nó có logic `set_task` cho custom LLM:

```python
            # Set task type for custom provider
            if hasattr(self._llm, 'set_task'):
                self._llm.set_task('code_gen')

            response = await self._llm.complete(
                prompt_with_hint, system=system, temperature=0.2
            )
```

##### Conflict 2: `contribai/pr/manager.py`

```python
<<<<<<< Updated upstream
            default_prefixes = [
                "Security:",
                "Quality:",
                ...
            ]
            if any(new_title.startswith(prefix) for prefix in default_prefixes):
=======
            # Always regenerate title in conventional format if guidelines exist
            if guidelines and guidelines.has_guidelines:
>>>>>>> Stashed changes
```

**Giải pháp:** Giữ logic đơn giản hơn từ Stashed changes:

```python
            # Always regenerate title in conventional format if guidelines exist
            if guidelines and guidelines.has_guidelines:
```

##### Conflict 3: `contribai/github/guidelines.py`

```python
<<<<<<< Updated upstream
        # Build title with optional scope
        if (scope and guidelines.requires_scope) or scope:
            return f"{cc_type}({scope}): {finding_title.lower()}"
        else:
            return f"{cc_type}: {finding_title.lower()}"

    # Default: clean text format (no emoji in PR title)
    type_labels = {...}
    return f"{label}: {finding_title}"
=======
    # Build title with optional scope (always use conventional format, no emojis)
    if (scope and guidelines.requires_scope) or scope:
        return f"{cc_type}({scope}): {finding_title.lower()}"
    else:
        return f"{cc_type}: {finding_title.lower()}"
>>>>>>> Stashed changes
```

**Giải pháp:** Giữ version đơn giản từ Stashed changes (không có emoji logic):

```python
    # Build title with optional scope (always use conventional format, no emojis)
    if (scope and guidelines.requires_scope) or scope:
        return f"{cc_type}({scope}): {finding_title.lower()}"
    else:
        return f"{cc_type}: {finding_title.lower()}"
```

### Bước 6: Kiểm Tra Và Sửa Conflict Markers Còn Sót

#### 6.1. Tìm Conflict Markers

```bash
grep -r "^<<<<<<<\|^>>>>>>>\|^=======" --include="*.py" .
```

Hoặc trên Windows PowerShell:
```powershell
Select-String -Path "*.py" -Pattern "^<<<<<<<|^>>>>>>>|^=======" -Recurse
```

#### 6.2. Kiểm Tra Syntax

```bash
python -m py_compile contribai/generator/engine.py
python -m py_compile contribai/pr/manager.py
python -m py_compile contribai/github/guidelines.py
```

Nếu có lỗi syntax, mở file và xóa các conflict markers còn sót.

### Bước 7: Add Các File Đã Resolve

```bash
git add contribai/generator/engine.py
git add contribai/pr/manager.py
git add contribai/github/guidelines.py
```

Hoặc add tất cả:
```bash
git add .
```

### Bước 8: Xóa Stash

```bash
git stash drop
```

### Bước 9: Kiểm Tra Hoạt Động

```bash
contribai --help
```

Nếu CLI chạy được, bạn đã thành công!

## 🔧 Xử Lý Các Trường Hợp Đặc Biệt

### Trường Hợp 1: Quá Nhiều Conflicts

Nếu có quá nhiều conflicts khó giải quyết:

```bash
# Hủy stash pop
git reset --hard HEAD

# Xem lại stash
git stash list

# Apply từng file một
git checkout stash@{0} -- contribai/llm/provider.py
git checkout stash@{0} -- contribai/core/config.py
# ... tiếp tục với các file quan trọng
```

### Trường Hợp 2: Muốn Giữ Toàn Bộ Custom

Nếu bạn muốn giữ 100% custom của mình:

```bash
git stash pop
# Khi có conflict, chọn "ours" (giữ custom)
git checkout --ours contribai/generator/engine.py
git add contribai/generator/engine.py
```

### Trường Hợp 3: Muốn Lấy Code Mới Hoàn Toàn

Nếu muốn lấy code mới và apply lại custom sau:

```bash
# Không pop stash, để nó lưu
git stash list  # Xem stash đã lưu

# Sau khi test code mới, apply lại custom
git stash show -p stash@{0}  # Xem chi tiết thay đổi
```

## 📝 Checklist Sau Khi Pull

- [ ] Tất cả conflicts đã được resolve
- [ ] Không còn conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
- [ ] Syntax check pass cho tất cả file Python
- [ ] CLI chạy được: `contribai --help`
- [ ] Config files vẫn đúng: `config.yaml`, `.env`
- [ ] Custom LLM endpoints vẫn hoạt động
- [ ] Các file tài liệu custom vẫn còn

## 🎯 Các File Cần Đặc Biệt Chú Ý

### File Thường Bị Conflict

1. **`contribai/generator/engine.py`**
   - Chứa logic `set_task()` cho custom LLM
   - Luôn giữ phần custom task setting

2. **`contribai/llm/provider.py`**
   - Core custom LLM logic
   - TUYỆT ĐỐI giữ nguyên custom của bạn

3. **`contribai/core/config.py`**
   - Config dataclasses
   - Giữ các field custom cho LLM endpoints

### File Ít Khi Conflict

- `config.yaml` - Thường không conflict vì là local config
- `.env` - Thường không conflict vì trong .gitignore
- Các file tài liệu custom - Không bị conflict vì là untracked

## 🛡️ Backup Strategy

### Trước Mỗi Lần Pull

```bash
# Tạo backup branch
git branch backup-custom-llm-$(date +%Y%m%d)

# Hoặc copy các file quan trọng
cp contribai/llm/provider.py contribai/llm/provider.py.backup
cp config.yaml config.yaml.backup
```

### Sau Khi Pull Thành Công

```bash
# Xóa backup branch (nếu không cần)
git branch -D backup-custom-llm-20260328
```

## 🔍 Debug Common Issues

### Issue 1: SyntaxError sau khi resolve

**Nguyên nhân:** Còn conflict markers

**Giải pháp:**
```bash
# Tìm conflict markers
grep -n "^<<<<<<<\|^>>>>>>>\|^=======" contribai/generator/engine.py

# Mở file và xóa thủ công
```

### Issue 2: Import Error

**Nguyên nhân:** Thiếu code mới cần thiết

**Giải pháp:**
```bash
# Xem diff giữa upstream và local
git diff origin/main contribai/generator/engine.py

# Merge thủ công các import mới cần thiết
```

### Issue 3: Custom LLM Không Hoạt Động

**Nguyên nhân:** Logic custom bị ghi đè

**Giải pháp:**
```bash
# So sánh với stash
git stash show -p stash@{0} -- contribai/llm/provider.py

# Apply lại phần bị mất
```

## 📚 Tài Liệu Liên Quan

- [CUSTOM_LLM_CHANGES.md](../CUSTOM_LLM_CHANGES.md) - Log các thay đổi custom
- [docs/CUSTOM_LLM_SETUP.md](CUSTOM_LLM_SETUP.md) - Hướng dẫn setup ban đầu
- [HUONG_DAN_SU_DUNG.md](../HUONG_DAN_SU_DUNG.md) - Hướng dẫn sử dụng

## 💡 Tips & Best Practices

1. **Luôn stash trước khi pull** - Tránh mất code
2. **Đọc kỹ conflict markers** - Hiểu rõ đang conflict gì
3. **Test sau mỗi resolve** - Đảm bảo code chạy được
4. **Commit thường xuyên** - Dễ rollback nếu cần
5. **Backup config files** - Tránh mất cấu hình
6. **Document changes** - Ghi lại những gì đã custom

## 🆘 Khi Cần Trợ Giúp

Nếu gặp vấn đề không giải quyết được:

1. Kiểm tra lại tài liệu này
2. Xem git log: `git log --oneline -10`
3. Xem stash: `git stash list`
4. Restore từ backup: `git checkout backup-custom-llm-<date>`
5. Hỏi AI assistant với context đầy đủ về custom LLM

---

**Lưu ý:** Tài liệu này được viết dựa trên kinh nghiệm thực tế pull code ngày 2026-03-28. Các conflict có thể khác nhau tùy theo version code.
