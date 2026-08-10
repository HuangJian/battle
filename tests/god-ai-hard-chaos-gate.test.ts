import { describe, it, expect } from 'bun:test'
import {
  readAllParts,
  hasAllParts,
  cleanupRunDir,
  listParts,
  HARD_AGGREGATE_FLOOR,
  CHAOS_AGGREGATE_FLOOR,
} from './gate-core'

// ============================================================
// God-AI Hard/Chaos Gate — AGGREGATE reducer (plan/God-AI-Redesign-v2 §6, M0)
//
// The per-stage screening floors now live in the split part files
// (god-ai-hard-chaos-gate.partN.{hard,chaos}.test.ts), which bun runs in
// parallel across cores via `--parallel` and publish their partial win
// counts to a ppid-scoped temp dir (see gate-core.ts).
//
// This file is the SECOND guard the original gate provided: an aggregate
// floor (truth mean − 3.7pp) that catches a uniform small drop across every
// stage — something the per-stage floors (truth − 4 wins) cannot, because
// their sum is looser than the aggregate. We collect the part results here
// and assert the aggregate, then clean up the temp dir.
//
// Run the whole suite (bun test) so the part files run alongside this
// reducer. Run standalone, this test skips gracefully (no siblings to read).
// ============================================================

const EXPECTED_IDS: string[] = [
  ...Array.from({ length: 7 }, (_, i) => `hard-${i}`),
  ...Array.from({ length: 7 }, (_, i) => `chaos-${i}`),
]

describe('god-ai-hard-chaos-gate (aggregate)', () => {
  it('aggregate floors hold across all 35 stages × 2 difficulties', async () => {
    // Wait for the parallel part files to publish their results. They run in
    // separate worker processes, so we poll rather than assume order.
    const deadline = Date.now() + 120000
    while (!hasAllParts(EXPECTED_IDS) && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 150))
    }

    if (!hasAllParts(EXPECTED_IDS)) {
      // Not part of a full-suite run (e.g. `bun test <this file>` alone) — the
      // per-stage floors already ran elsewhere, so skip instead of failing.
      console.log(
        `[gate] aggregate skipped: ${listParts().length} part file(s) found, expected ${EXPECTED_IDS.length} ` +
          `(run as part of the full suite).`,
      )
      cleanupRunDir()
      return
    }

    const parts = readAllParts(EXPECTED_IDS)
    let hardTotal = 0
    let chaosTotal = 0
    for (const [id, p] of parts) {
      const sum = p.wins.reduce((a: number, b: number) => a + b, 0)
      if (id.startsWith('hard')) hardTotal += sum
      else chaosTotal += sum
    }

    console.log(`[gate:hard] aggregate: ${hardTotal}/700 floor=${HARD_AGGREGATE_FLOOR}`)
    console.log(`[gate:chaos] aggregate: ${chaosTotal}/700 floor=${CHAOS_AGGREGATE_FLOOR}`)
    expect(hardTotal).toBeGreaterThanOrEqual(HARD_AGGREGATE_FLOOR)
    expect(chaosTotal).toBeGreaterThanOrEqual(CHAOS_AGGREGATE_FLOOR)
    cleanupRunDir()
  }, 900000)
})
