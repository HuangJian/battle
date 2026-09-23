#!/bin/bash
# 容器内：phone-roll2.sh <label> <max-ticks> <taskset 前缀，可为空>
cd /root/battle || exit 1
export PATH=/root/.bun/bin:$PATH
W=nn-training/weights/x20-noexplore/x20-noexplore.it181.20260922-083734.json
LBL=$1; MT=$2; PIN=$3
OUT=/tmp/ro-$LBL
S=$(date +%s%N)
$PIN bun tools/sim/export-rl-rollout.ts --weights "$W" --out "$OUT" --stages 0 --seeds 0-3 \
  --difficulty hard --lives-override 1 --max-ticks "$MT" > /tmp/ph-$LBL.log 2>&1
RC=$?
E=$(date +%s%N)
echo "[$LBL] rc=$RC wall_ms=$(( (E-S)/1000000 ))"
grep -cE "^\[OK\]" /tmp/ph-$LBL.log | sed 's/^/  games_ok=/'
grep -E "totalTicks" /tmp/ph-$LBL.log | sed 's/^/  /'
