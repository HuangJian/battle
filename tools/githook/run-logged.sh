#!/bin/sh
# run-logged.sh — 长任务日志「一次落盘、绿则自清」包装器（AGENTS §16.7）
#
# 为什么存在（2026-09-10 实测）：门禁 / 仿真 / 探针这类长任务，输出不落盘就只能
# 靠反复重跑去看日志——既慢又会污染环境（本仓实测：一个会话里为拿日志重跑全量
# 十几次，把沙箱「每 turn 批量删除配额」刷爆，之后所有删除被拒、删除类用例集体
# 转红，看起来像回归其实全是环境假象）。本脚本把「跑一次 → 日志落盘 → 红了就地
# 排查 → 绿了自动删」变成一条命令。
#
# 用法：
#   sh tools/githook/run-logged.sh <log-path> -- <command...>
#   KEEP_LOG=1 sh tools/githook/run-logged.sh <log-path> -- <command...>   # 绿也留档
#
# 行为：
#   * 全量输出（stdout+stderr）写入 <log-path>，不往终端刷屏
#   * 成功 -> 一行摘要；KEEP_LOG 未设 1 时**删除日志**（无用即清）
#   * 失败 -> 打印日志路径 + 末尾 40 行，**日志保留**供事后排查
#
# 例：
#   sh tools/githook/run-logged.sh tmp/logs/gate.log -- \
#       bash tools/githook/nn-python-gate.sh
#   sh tools/githook/run-logged.sh tmp/logs/probe.log -- \
#       nn-training/.venv/Scripts/python.exe nn-training/tools/tpu-probe.py --device cpu
#   KEEP_LOG=1 sh tools/githook/run-logged.sh tmp/logs/bench.log -- <cmd>   # 要存档证据时
set -u

if [ "$#" -lt 3 ] || [ "$2" != "--" ]; then
  echo "usage: $0 <log-path> -- <command...>" >&2
  exit 2
fi

LOG=$1
shift 2

mkdir -p "$(dirname -- "$LOG")" 2>/dev/null || true
START=$(date +%s)
"$@" > "$LOG" 2>&1
RC=$?
ELAPSED=$(( $(date +%s) - START ))

if [ "$RC" -eq 0 ]; then
  if [ "${KEEP_LOG:-0}" = "1" ]; then
    echo "✓ ${ELAPSED}s — log kept: $LOG"
  else
    rm -f "$LOG"
    echo "✓ ${ELAPSED}s — log clean (KEEP_LOG=1 to keep)"
  fi
else
  echo "✗ exit=$RC after ${ELAPSED}s — log kept: $LOG" >&2
  echo "--- last 40 lines ---" >&2
  tail -n 40 "$LOG" >&2
fi
exit "$RC"
