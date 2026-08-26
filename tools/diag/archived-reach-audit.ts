#!/usr/bin/env bun
/**
 * archived-reach-audit.ts — §272 L2 可达性审计（plan/God-AI-Organization.md §6 C1）。
 *
 * 在 probe-det-baseline.sh 的同一 21 组合语料上逐局运行 runSimulation
 * （branchTotals: true），聚合 branchCounts，断言：所有 OFF 候选（CANDIDATE_SURVIVAL
 * 中 on:false 且有计数器者）的分支计数 === 0。字段级 OFF 由 L1 守卫测试保证；
 * 本审计兜底「字段=0 但代码仍可达」的漏网（评审 P2）。
 *
 * 运行时机：封版落地跑一次 + 每次更新 golden 时随「新纪元三件套」重跑；
 * 不进 per-edit / per-commit 循环。
 *
 * 组合清单须与 tools/probe-det-baseline.sh 保持同步（bash 数组无法 import，改动两处同改）。
 * 已知盲区同 det 语料：单玩家 only；dual/coop 路径由 godai-* 门禁覆盖。
 */
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import type { GodAIParams } from '../../src/ai/god/params.interface'
import { CANDIDATE_SURVIVAL } from '../../src/ai/god/think'
import { runSimulation } from '../sim/simulation-runner'

/** 与 tools/probe-det-baseline.sh COMBOS 同步（corpus v2, 21 rows）。 */
const COMBOS: ReadonlyArray<readonly [difficulty: string, stageIdx1: number, seed: number]> = [
  ['classic', 7, 5],
  ['classic', 22, 31],
  ['hard', 18, 13],
  ['hard', 32, 5],
  ['hard', 4, 42],
  ['chaos', 6, 11],
  ['chaos', 28, 17],
  ['hard', 12, 99],
  ['hard', 11, 934391936],
  ['classic', 11, 14],
  ['hard', 18, 37],
  ['hard', 30, 14],
  ['hard', 30, 71],
  ['hard', 32, 83],
  ['hard', 33, 2],
  ['chaos', 31, 23],
  ['hard', 8, 5],
  ['hard', 13, 12],
  ['hard', 26, 12],
  ['hard', 27, 8],
  ['chaos', 27, 3],
]

const offCandidates = CANDIDATE_SURVIVAL.filter((r) => !r.on).map((r) => r.candidate)
const totals = new Map<string, number>()
let runs = 0

for (const [difficulty, stageNum, seed] of COMBOS) {
  const stage = STAGES[stageNum - 1] // CLI/语料口径为 1-based
  if (!stage) throw new Error(`stage idx ${stageNum} out of range`)
  const res = runSimulation({
    stage,
    seed,
    difficulty,
    godAIParams: DEFAULT_GOD_AI_PARAMS as GodAIParams,
    branchTotals: true,
  })
  runs++
  for (const [k, v] of Object.entries(res.branchTotals ?? {})) {
    totals.set(k, (totals.get(k) ?? 0) + v)
  }
}

// ── 报告 ──
console.log(`[L2 archived-reach audit] ${runs} runs over ${COMBOS.length} combos`)
const nonzero = [...totals.entries()].filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1])
console.log('branch counters observed (>0):')
for (const [k, v] of nonzero) console.log(`  ${k}: ${v}`)

let failed = false
for (const cand of offCandidates) {
  const v = totals.get(cand) ?? 0
  if (v > 0) {
    console.error(`  ✗ OFF candidate '${cand}' reachable: ${v} commits`)
    failed = true
  } else {
    console.log(`  ✓ OFF candidate '${cand}': 0`)
  }
}

if (failed) {
  console.error('[L2] FAIL — archived candidate(s) reachable with defaults; freeze signature is stale.')
  process.exit(1)
}
console.log('[L2] PASS — no archived candidate reachable under frozen defaults.')
