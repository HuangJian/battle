#!/bin/bash
# Determinism-signature batch (plan/refactor.zcode.md B3 gate).
# Per-tick world signatures over a fixed (difficulty, stage, seed) grid;
# concatenated and hashed. Byte-identical before/after each AI refactor step.
set -e
cd "$(dirname "$0")/.."
OUT=tmp/det-batch.txt
: > "$OUT"
for combo in "classic 7 5" "classic 22 31" "hard 18 13" "hard 32 5" "hard 4 42" "chaos 6 11" "chaos 28 17" "hard 12 99"; do
  set -- $combo
  echo "== $1 S$2 seed$3" >> "$OUT"
  bun tools/diag/per-seed-diff.ts dump "$2" "$3" --difficulty "$1" >> "$OUT"
done
shasum -a 256 "$OUT"
