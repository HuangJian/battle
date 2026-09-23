#!/bin/bash
# 容器内：phone-eng.sh <标签> <native|wasm|ts> [max-ticks]
cd /root/battle || exit 1
export PATH=/root/.bun/bin:$PATH
W=nn-training/weights/x20-noexplore/x20-noexplore.it181.20260922-083734.json
LBL=$1; ENG=$2; MT=${3:-2000}
WASM=src/nn/conv/prebuilt/wasm/conv.wasm
case "$ENG" in
  native) ;;
  wasm) export NN_NATIVE=0 ;;
  ts)   export NN_NATIVE=0; mv "$WASM" /tmp/conv.wasm.bak ;;
esac
S=$(date +%s%N)
bun tools/sim/export-rl-rollout.ts --weights "$W" --out "/tmp/ro-$LBL" --stages 0 --seeds 0 \
  --difficulty hard --lives-override 1 --max-ticks "$MT" > "/tmp/eng-$LBL.log" 2>&1
RC=$?
E=$(date +%s%N)
[ "$ENG" = "ts" ] && mv /tmp/conv.wasm.bak "$WASM"
printf '[%s/%s] rc=%s wall_ms=%s feat=' "$LBL" "$ENG" "$RC" "$(( (E-S)/1000000 ))"
tr -d ' \n' < "/tmp/ro-$LBL/rl_s0_seed0/manifest.json" 2>/dev/null | grep -o '"feat":"[a-z]*"'
grep -E "^\[OK\]" "/tmp/eng-$LBL.log" | sed 's/^/  /'
