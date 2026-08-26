#!/bin/bash
# Determinism-signature batch gate (DECISIONS §254 flow; corpus v2 — 遗留 #12).
#
# Dumps per-tick world signatures over a fixed (difficulty, stage, seed) grid,
# concatenated into tmp/det-batch.txt and hashed. Compare the sha256 before vs
# after each AI-touching refactor step: MUST be byte-identical unless the change
# intentionally alters behavior (then re-run godai gates + re-capture truth).
#
# Runtime ~100s for the full grid (was ~35s at 7 rows) — run once per batch,
# not per micro-edit.
#
# Corpus rationale (rows marked `idx=` take the RAW STAGES index that
# per-seed-diff consumes; comments cite the incident each row guards):
#   legacy grid — the original 7 rows, kept verbatim for continuity;
#   Lattice(idx11)     §74/§152-W1 steel-path (seed 934391936) + classic contrast;
#   Frozen Field(18)   powerupStuck autopsy seed37;
#   Eagle Nest(30)     navBreakStuck seeds 14 / 71 (§186);
#   Diamond(32)        T2a-camp seed83 (known structural hard case);
#   Battlement(33)     base-l3-t25-seed2 autopsy (→ §178 dual breach);
#   Star Fort(31)      chokepoint A/B round-3 seed23;
#   Twin Towers(8)     stuck-at-center root cause;
#   Steel Web(13)      central-breach-negative stage;
#   Ice Palace(26)     ice-glide path;
#   Brick Maze(27)     brick-dense adaptation (classic + chaos arms).
#
# Known blind spot: single-player only (per-seed-diff has no spectateDual/coop
# wiring) — dual-central-breach/coop paths stay covered by the godai-* gates.
#
# --golden mode (DECISIONS §272, plan/God-AI-Organization.md §7): after the run,
# compare the full-grid sha256 against tools/det-golden.v1.sha256. Mismatch →
# print per-combo hashes + non-zero exit. A red gate is NOT an error per se —
# it forces the explicit "new era" triple (new DECISIONS entry + re-run 60-seed
# baseline + update golden), or a rollback.
set -e
cd "$(dirname "$0")/.."
OUT=tmp/det-batch.txt
GOLDEN=tools/det-golden.v1.sha256

# Portable sha256: git's bundled-sh hook environment on Windows often lacks
# shasum/sha256sum — fall back to bun (already a hard dependency of this script).
hash256() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    bun -e 'const {createHash}=require("node:crypto");const {readFileSync}=require("node:fs");process.stdout.write(createHash("sha256").update(readFileSync(process.argv[1])).digest("hex"))' "$1"
  fi
}
# NB: `set -- $combo` inside the loop clobbers positional params — capture flags first.
MODE="${1:-}"
: > "$OUT"

COMBOS=(
  # ---- legacy grid (raw idx, kept from corpus v1) ----
  "classic 7 5"
  "classic 22 31"
  "hard 18 13"
  "hard 32 5"
  "hard 4 42"
  "chaos 6 11"
  "chaos 28 17"
  "hard 12 99"
  # ---- incident grid (corpus v2) ----
  "hard 11 934391936"   # Lattice · §152-W1 steel-path W1 seed
  "classic 11 14"       # Lattice · instant-model contrast arm
  "hard 18 37"          # Frozen Field · powerupStuck autopsy
  "hard 30 14"          # Eagle Nest · navBreakStuck seed14
  "hard 30 71"          # Eagle Nest · §186 seed71
  "hard 32 83"          # Diamond · T2a camp seed83
  "hard 33 2"           # Battlement · base autopsy seed2 (§178)
  "chaos 31 23"         # Star Fort · chokepoint A/B r3 seed23
  "hard 8 5"            # Twin Towers · stuck-at-center
  "hard 13 12"          # Steel Web · central-breach negative case
  "hard 26 12"          # Ice Palace · ice glide
  "hard 27 8"           # Brick Maze · brick-dense adapt (pool model)
  "chaos 27 3"          # Brick Maze · chaos arm
)

for combo in "${COMBOS[@]}"; do
  set -- $combo
  echo "== $1 idx$2 seed$3" >> "$OUT"
  bun tools/diag/per-seed-diff.ts dump "$2" "$3" --difficulty "$1" >> "$OUT"
done

echo "corpus: ${#COMBOS[@]} runs, $(wc -l < "$OUT") signature lines"
HASH=$(hash256 "$OUT")
echo "$HASH  $OUT"

if [ "$MODE" = "--golden" ]; then
  EXPECT=$(grep -Ev '^[[:space:]]*#|^[[:space:]]*$' "$GOLDEN" | head -1 | cut -d' ' -f1)
  if [ "$HASH" = "$EXPECT" ]; then
    echo "FROZEN-SIGNATURE OK ($HASH)"
  else
    echo "FROZEN-SIGNATURE MISMATCH — current behavior ≠ frozen God AI v1 (§272)"
    echo "  golden : $EXPECT"
    echo "  current: $HASH"
    echo "per-combo sha256 (compare against the previous run to localize drift):"
    sec_name=""; sec_file=""
    while IFS= read -r line; do
      case "$line" in
        "== "*)
          if [ -n "$sec_file" ]; then printf '%s  %s\n' "$(hash256 "$sec_file")" "$sec_name"; rm -f "$sec_file"; fi
          sec_name="$line"
          sec_file=$(mktemp)
          ;;
        *) [ -n "$sec_file" ] && printf '%s\n' "$line" >> "$sec_file" ;;
      esac
    done < "$OUT"
    if [ -n "$sec_file" ]; then printf '%s  %s\n' "$(hash256 "$sec_file")" "$sec_name"; rm -f "$sec_file"; fi
    echo "next step: either roll back, or run the new-era triple (DECISIONS entry + 60-seed baseline + golden update)"
    exit 1
  fi
fi
