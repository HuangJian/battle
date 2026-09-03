#!/bin/sh
# nn-python-gate.sh — nn-training Python 质量门禁（并行：ruff + mypy + pytest xdist）
#
# 日常开发（仓库根或任意目录均可，脚本自定位）：
#   bash tools/githook/nn-python-gate.sh
# pre-commit hook（tools/githook/pre-commit）复用同一入口——提交前与日常跑
# 的是同一套门禁，不会出现"hook 严、日常松"的漂移。
#
# 并行架构（v3.15 2026-09-03）：
#   ruff(~1s) / mypy(~4s 热缓存) / pytest xdist -n 4 全量(~17s) 三路并行，总 ~17s。
#   全量含 heavy/integration（xdist worker 各自 FakeServer 实例隔离，无竞态）。
#   换用 pytest-xdist（colorama 已修复 + conftest tmp_path 覆盖消除沙箱问题）。
#   4 worker 为本机最优点（16 核，但 torch import 开销 + 单函数 test_integration
#   13.9s 不可再分，更多 worker 反而更慢）。
#
# 跳过单个工具（逗号分隔）：
#   NN_GATE_SKIP=ruff,mypy bash tools/githook/nn-python-gate.sh
#
# 沙箱注意：脚本内部**不**把子进程输出重定向到 /dev/null——MSYS 伪设备与
# Windows 子进程继承存在兼容问题（实测间歇性失败）。输出直通。
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(dirname -- "$(dirname -- "$SCRIPT_DIR")")
NN_ROOT="$REPO_ROOT/nn-training"

if [ -x "$NN_ROOT/.venv/Scripts/python.exe" ]; then
  NN_PY="$NN_ROOT/.venv/Scripts/python.exe"
elif [ -x "$NN_ROOT/.venv/bin/python" ]; then
  NN_PY="$NN_ROOT/.venv/bin/python"
else
  echo "✗ nn-training/.venv 不存在（$NN_ROOT/.venv）——请先 python -m venv .venv && pip install -r requirements.txt" >&2
  exit 1
fi

SKIP_LIST=${NN_GATE_SKIP:-}
has_skip() {
  case ",$SKIP_LIST," in
    *",$1,"*) return 0 ;;
    *) return 1 ;;
  esac
}

cd "$NN_ROOT"
echo "▶ nn-training python gate（ruff + mypy + pytest xdist -n 4, parallel）"
# 门禁前清理过期测试临时目录（python -S 绕过沙箱删除保护，仅限 tmp/pytest-tmp
# 下 KEEP_DAYS 天前的子目录；NN_TMP_KEEP_DAYS 可调，默认 7）。失败静默（清理
# 是锦上添花，不阻塞门禁）。
"$NN_PY" -S ../tools/githook/nn-clean-tmp.py >/dev/null 2>&1 || true
PIDS=""
if has_skip ruff; then
  echo " ▸ ruff skipped（NN_GATE_SKIP=$SKIP_LIST）"
else
  "$NN_PY" -m ruff check . & PIDS="$PIDS $!"
fi
if has_skip mypy; then
  echo " ▸ mypy skipped（NN_GATE_SKIP=$SKIP_LIST）"
else
  "$NN_PY" -m mypy . --config-file pyproject.toml & PIDS="$PIDS $!"
fi
if has_skip pytest; then
  echo " ▸ pytest skipped（NN_GATE_SKIP=$SKIP_LIST）"
else
  # v3.15 全量：xdist -n 4 跑全量（含 heavy/integration），~17s。
  "$NN_PY" -m pytest tests/ -n 4 -q & PIDS="$PIDS $!"
fi

t0=$(date +%s)
RC=0
for p in $PIDS; do
  wait "$p" || RC=1
done
t1=$(date +%s)
if [ "$RC" -eq 0 ]; then
  echo "✓ nn-training python gate done in $((t1 - t0))s"
else
  echo "✗ nn-training python gate FAILED in $((t1 - t0))s"
fi
exit "$RC"
