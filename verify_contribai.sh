#!/usr/bin/env bash
# ============================================================================
# verify_contribai.sh — Thu thập số liệu vận hành thật cho ContribAI
#
# Chạy trên máy Mac của bạn (nơi có .venv gốc), tại thư mục gốc dự án:
#     bash verify_contribai.sh 2>&1 | tee contribai_verify.log
#
# Script này CHỈ ĐỌC — không sửa file, không gọi GitHub API ghi, không tạo PR.
# Gửi lại contribai_verify.log để phân tích tiếp.
# ============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PY=".venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3.11 || command -v python3)"

hr() { printf '\n%s\n' "════════════════════════════════════════════════════════════════"; }
sec() { hr; echo ">>> $1"; hr; }

sec "0. Môi trường"
"$PY" -V
echo "python: $PY"
uname -a
echo "--- cấu hình phần cứng (để đối chiếu số liệu hiệu suất) ---"
sysctl -n hw.model hw.ncpu hw.memsize 2>/dev/null || nproc

sec "1. Lint — ruff check"
"$PY" -m ruff check contribai/ tests/ --statistics 2>&1 | tail -30
echo "--- tổng số lỗi ---"
"$PY" -m ruff check contribai/ tests/ 2>&1 | tail -3

sec "2. Format — ruff format --check"
"$PY" -m ruff format --check contribai/ tests/ 2>&1 | tail -5

sec "3. Test suite đầy đủ (đây là số liệu quan trọng nhất)"
time "$PY" -m pytest -q --timeout=300 2>&1 | tail -60

sec "4. Danh sách test THẤT BẠI (nếu có)"
"$PY" -m pytest -q 2>&1 | grep -E "^(FAILED|ERROR)" | head -50
echo "(nếu trống nghĩa là toàn bộ test pass)"

sec "5. Coverage THẬT (bỏ qua danh sách omit để thấy bức tranh đầy đủ)"
"$PY" -m pytest -q \
  --cov=contribai --cov-report=term-missing:skip-covered \
  --cov-config=/dev/null 2>&1 | tail -60

sec "6. Coverage theo cấu hình dự án (để đối chiếu ngưỡng CI 50%)"
"$PY" -m pytest -q --cov=contribai --cov-report=term 2>&1 | tail -30

sec "7. Test chạy chậm nhất (điểm nghẽn hiệu suất trong test)"
"$PY" -m pytest -q --durations=25 2>&1 | tail -32

sec "8. Thời gian khởi động CLI (cold start)"
for i in 1 2 3; do
  /usr/bin/time -p "$PY" -m contribai.cli.main --help >/dev/null 2>>/tmp/_cli_time.txt
done
tail -12 /tmp/_cli_time.txt; rm -f /tmp/_cli_time.txt

sec "9. Bộ nhớ khi import toàn bộ package"
"$PY" - <<'PYEOF'
import importlib, resource, sys, time
t0 = time.perf_counter()
mods = [
    "contribai.core.config", "contribai.core.models", "contribai.orchestrator.pipeline",
    "contribai.orchestrator.steps", "contribai.analysis.analyzer", "contribai.generator.engine",
    "contribai.github.client", "contribai.pr.manager", "contribai.pr.patrol",
    "contribai.llm.provider", "contribai.llm.fallback", "contribai.web.server",
]
for m in mods:
    try: importlib.import_module(m)
    except Exception as e: print(f"  IMPORT FAIL {m}: {type(e).__name__}: {e}")
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
unit = 1024**2 if sys.platform == "darwin" else 1024
print(f"  import {len(mods)} module: {time.perf_counter()-t0:.2f}s, RSS đỉnh = {rss/unit:.1f} MB")
print(f"  tổng module đã nạp: {len(sys.modules)}")
PYEOF

sec "10. Đo điểm nghẽn _fuzzy_replace (báo cáo ước tính ~7,2s cho file 3000 dòng)"
"$PY" - <<'PYEOF'
import time, difflib
def fuzzy(content, search, threshold=0.8):
    cl, sl = content.split("\n"), search.split("\n")
    n = len(sl); best = 0.0
    for i in range(len(cl) - n + 1):
        r = difflib.SequenceMatcher(None, "\n".join(sl), "\n".join(cl[i:i+n])).ratio()
        if r > best: best = r
    return best
for nlines in (500, 1500, 3000):
    content = "\n".join(f"    line_{i} = compute_value({i}) + offset" for i in range(nlines))
    search = "\n".join(f"    line_{i} = compute_value({i}) + offsetX" for i in range(900, 915))
    t0 = time.perf_counter(); fuzzy(content, search)
    print(f"  file {nlines:>5} dòng, search 15 dòng → {time.perf_counter()-t0:6.2f}s")
PYEOF

sec "11. Xác minh cổng an toàn — dry_run có thực sự chặn create_pr không?"
"$PY" - <<'PYEOF'
import asyncio, inspect
from unittest.mock import AsyncMock, MagicMock
try:
    from contribai.orchestrator import steps as S
    src = inspect.getsource(S.generate_contribution_step)
    i_append = src.find("state.contributions.append")
    i_review = src.find("ctx.reviewer.review")
    print(f"  vị trí 'contributions.append' trong hàm: {i_append}")
    print(f"  vị trí 'reviewer.review'       trong hàm: {i_review}")
    if 0 <= i_append < i_review:
        print("  ❌ XÁC NHẬN LỖI P0-1: contribution được thêm TRƯỚC khi review →")
        print("     submit_pr_step sẽ tạo PR ngay cả khi người dùng từ chối.")
    else:
        print("  ✅ Thứ tự đúng — lỗi P0-1 đã được sửa.")
except Exception as e:
    print("  không kiểm tra được:", e)
PYEOF

sec "12. Xác minh middleware chain / quality scorer có được gọi không"
grep -rn "MiddlewareChain(" contribai/ --include=*.py || echo "  ❌ XÁC NHẬN: MiddlewareChain KHÔNG BAO GIỜ được khởi tạo trong production"
grep -rn "QualityScorer" contribai/ --include=*.py || echo "  ❌ XÁC NHẬN: QualityScorer KHÔNG BAO GIỜ được dùng trong production"

sec "13. Docker build (kiểm chứng lỗi thiếu LICENSE)"
if command -v docker >/dev/null 2>&1; then
  docker build -t contribai-verify:test . 2>&1 | tail -15
else
  echo "  docker không có sẵn — bỏ qua"
  ls -la LICENSE* 2>&1 || echo "  ❌ XÁC NHẬN: không có file LICENSE → Dockerfile:8 'COPY LICENSE .' sẽ thất bại"
fi

sec "14. Kiểm tra secrets có bị track trong git không"
git ls-files | grep -E '^\.env$|^config\.yaml$' && echo "  ❌ CẢNH BÁO: secret đang được track — thu hồi token ngay!" || echo "  ✅ .env và config.yaml KHÔNG được track"
git log --all --oneline -- .env 2>/dev/null | head -3 || true

hr
echo "HOÀN TẤT. Gửi lại toàn bộ output (contribai_verify.log) để phân tích tiếp."
hr
