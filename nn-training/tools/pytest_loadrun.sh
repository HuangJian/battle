#!/usr/bin/env bash
# pytest_loadrun.sh — 满机全量连跑：抓「负载型假红」（门禁剩下的真风险，见 engineering.md §45/§46）
#
# 用法（nn-training 目录，或任意目录——脚本自己 cd）：
#   bash tools/pytest_loadrun.sh <tag> <rounds> [burners]
#   例：bash tools/pytest_loadrun.sh a 6 6      # 6 burners + `-n 12` ⇒ loadavg ≈ burners+12
#
# 为什么这么写（每一条都是踩过的）：
#   * 合成 co-tenant：`burners` 个纯 python 忙循环占核，与 `-n NPROC` 的 worker 抢 CPU。
#     没有它，本机跑出来的 loadavg 只有 ~0.1，而「满机假红」根本不复现（§46.4）。
#   * burners 自带寿命（`timeout LIFE`）：外层被 kill（SIGKILL 不透传 trap）时也不会留下占核孤儿。
#   * **不加 `-x`**：`pyproject.toml` 的 addopts 有 `-x`，门禁语义是首败即停——抓 flake 时要
#     一轮看全所有 FAILED，所以显式 `-rf`（失败摘要）+ 不 `-x`。
#   * **记 load_before**：loadavg 是 1 分钟滑动平均，从 0.1 爬到 20 要 ~1min；不记就分不清
#     「红在满机」还是「红在空机」。
#   * env 与 `nn-python-gate.sh` 同款（内线程封 1，`export` 必须在 python 启动前）。
#   * 每轮 stdout 落 `../tmp/dur/lr-<tag>/rN.log` + `rN.time`（wall/user/sys）。
set -u
TAG=${1:?usage: pytest_loadrun.sh <tag> <rounds> [burners]}
ROUNDS=${2:?usage: pytest_loadrun.sh <tag> <rounds> [burners]}
BURN=${3:-6}
cd "$(dirname "$0")/.."
OUT="../tmp/dur/lr-$TAG"
mkdir -p "$OUT"
# 核数要在 export 之前问：`nproc` 认 OMP_NUM_THREADS，而下面的 export 会把它钉成 1
# （曾因此打出「cores=1」的假象），用 getconf 免疫。
CORES=$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc)

export NN_GATE_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONUTF8=1
PY=./.venv/bin/python

LIFE=$((ROUNDS * 180 + 120))
burn=()
for _i in $(seq 1 "$BURN"); do
  timeout "$LIFE" "$PY" -c 'while True: pass' &
  burn+=("$!")
done
# shellcheck disable=SC2064
trap 'kill "${burn[@]}" 2>/dev/null' EXIT

echo "▶ tag=$TAG rounds=$ROUNDS burners=$BURN cores=$CORES load=$(cut -d' ' -f1-3 /proc/loadavg)"
for r in $(seq 1 "$ROUNDS"); do
  echo "=== round $r  load_before=$(cut -d' ' -f1-3 /proc/loadavg)"
  /usr/bin/time -f "wall=%e user=%U sys=%S" \
    "$PY" -m pytest tests/ e2e/ -n 12 --timeout=60 -rf --tb=line \
    > "$OUT/r$r.log" 2> "$OUT/r$r.time"
  echo "  $($PY -c "
import re
t=open('$OUT/r$r.log',encoding='utf-8',errors='replace').read()
tail=[l for l in t.splitlines() if re.search(r'(passed|failed|error)',l)]
print(tail[-1] if tail else 'NO SUMMARY')
")"
  grep -E "^(FAILED|ERROR) " "$OUT/r$r.log" | sed 's/^/  /' || true
  echo "  $(tail -1 "$OUT/r$r.time")"
done
echo "▶ done: $OUT"
