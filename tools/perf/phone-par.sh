#!/bin/bash
# 容器内：phone-par.sh <N 并发> <每进程几局> <标签>
cd /root/battle || exit 1
export PATH=/root/.bun/bin:$PATH
W=nn-training/weights/x20-noexplore/x20-noexplore.it181.20260922-083734.json
N=${1:-4}; GAMES=${2:-1}; TAG=${3:-par}
SEEDS=$( [ "$GAMES" = "1" ] && echo 0 || echo 0-3 )
S=$(date +%s%N)
for i in $(seq 1 "$N"); do
  (
    s=$(date +%s%N)
    bun tools/sim/export-rl-rollout.ts --weights "$W" --out "/tmp/ro-$TAG$i" --stages 0 --seeds "$SEEDS" \
      --difficulty hard --lives-override 1 --max-ticks 12900 > "/tmp/$TAG$i.log" 2>&1
    e=$(date +%s%N)
    echo "  worker$i wall_ms=$(( (e-s)/1000000 ))"
  ) &
done
wait
E=$(date +%s%N)
echo "[$TAG] N=$N games_per_worker=$GAMES total_wall_ms=$(( (E-S)/1000000 ))"
echo "[$TAG] 平均每局 = $(( (E-S)/1000000 / (N*GAMES) )) ms/局"
