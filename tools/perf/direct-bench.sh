#!/bin/bash
# direct-bench.sh —— 不经 agent、直接开导出器的 N 局基线（对照 `tools/perf/agent-bench.ts`）。
#
#   spawn : 每局一个 `bun export-rl-rollout.ts` 进程（= 常驻池之前的训练栈口径）
#   batch : 每进程连续跑一段 seeds（批化；启动成本被摊到多局，但仍无长驻池）
#
# 用法：bash tools/perf/direct-bench.sh <spawn|batch> <并发> <局数> <标签>
#
# ⚠ 纪律：**产物不齐就响亮失败**（2026-09-23 踩过：cwd 少退一层 ⇒ 100 次启动失败却打出了
#   "8.6s / 41643 局/h" 的漂亮数字）。所以每局都必须有 `_rl_report.json`，缺失即报错退出。
set -u
cd "$(dirname "$0")/../.." || exit 1
MODE=${1:-spawn}; CONC=${2:-8}; GAMES=${3:-100}; LABEL=${4:-direct-$MODE}
W=nn-training/weights/x20-noexplore/x20-noexplore.it181.20260922-083734.json
OUT="tmp/direct-$LABEL"
TIMES="tmp/direct-$LABEL.times"
rm -rf "$OUT"; mkdir -p "$OUT"; : > "$TIMES"

run_one() { # $1=seeds（单数或 a-b）  $2=该进程的局数
  local seeds=$1 n=$2 s e rc
  s=$(date +%s%N)
  bun tools/sim/export-rl-rollout.ts --weights "$W" --out "$OUT/s$seeds" --stages 0 --seeds "$seeds" \
    --difficulty hard --lives-override 1 --max-ticks 12900 > "$OUT/log-$seeds.log" 2>&1
  rc=$?
  e=$(date +%s%N)
  echo "$rc $(( (e - s) / 1000000 / n ))"
}
export -f run_one
export W OUT

T0=$(date +%s%N)
if [ "$MODE" = "spawn" ]; then
  seq 0 $((GAMES - 1)) | xargs -P "$CONC" -I{} bash -c 'run_one {} 1' > "$TIMES"
else
  per=$(( (GAMES + CONC - 1) / CONC ))
  for ((i = 0; i < CONC; i++)); do
    a=$((i * per)); b=$((a + per - 1))
    [ "$b" -ge "$GAMES" ] && b=$((GAMES - 1))
    [ "$a" -gt "$b" ] && continue
    echo "$a-$b $((b - a + 1))"
  done | xargs -P "$CONC" -n 2 bash -c 'run_one "$0" "$1"' > "$TIMES"
fi
T1=$(date +%s%N)

# ---- 校验：产物齐全吗（按 report 内 games 求和，spawn 与 batch 两种口径都适用）----
REPORTS=$(find "$OUT" -name _rl_report.json | wc -l)
BAD_RC=$(awk '$1 != 0' "$TIMES" | wc -l)
GAMES_SUM=$(find "$OUT" -name _rl_report.json -exec grep -ho '"games": *[0-9]*' {} + 2>/dev/null | grep -o '[0-9]*' | awk '{s+=$1} END {print s+0}')
TICKS=$(find "$OUT" -name _rl_report.json -exec grep -ho '"totalTicks": *[0-9]*' {} + 2>/dev/null | grep -o '[0-9]*' | awk '{s+=$1} END {print s+0}')
if [ "$GAMES_SUM" -ne "$GAMES" ] || [ "$BAD_RC" -ne 0 ]; then
  echo "[$LABEL] ❌ 无效测量：期望 $GAMES 局，report 里只声明了 $GAMES_SUM 局（$REPORTS 份报告）；非零退出进程 $BAD_RC 个" >&2
  grep -l . "$OUT"/log-*.log 2>/dev/null | head -1 | xargs -r head -3 >&2
  exit 1
fi

MS=$(( (T1 - T0) / 1000000 ))
echo "[$LABEL] mode=$MODE 并发=$CONC 局数=$GAMES 总墙钟=$(( MS / 1000 )).$(( (MS % 1000) / 100 ))s"
echo "[$LABEL] 吞吐 = $(awk -v g="$GAMES" -v m="$MS" 'BEGIN{printf "%.2f 局/s = %.0f 局/h", g*1000/m, g*3600*1000/m}')"
echo "[$LABEL] 每局 ms（按进程归一到单局，$REPORTS 进程 / $GAMES_SUM 局）: $(awk '{print $2}' "$TIMES" | sort -n | awk '{a[NR]=$1; s+=$1} END {printf "mean=%.0f p50=%.0f p90=%.0f max=%.0f", s/NR, a[int(NR*0.5)+1], a[int(NR*0.9)+1], a[NR]}')"
echo "[$LABEL] 总 tick=$TICKS（对照 272675）"
