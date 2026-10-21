/**
 * paired-serial.ts — 串行配对 A/B（真实 stageIndex、CRN）。
 *
 * 为什么不用 `tools/eval/eval-suite.ts --compare`：`runCorpus` 不往 `SimTask` 传
 * `stageIndex`（缺省 0），而 `SimTask` 自己的注释就写明 index 0 与 33 是不同的一局。
 * 实证：某参数在 s35 seeds1-20 上 stageIndex=34 时 +1 胜、stageIndex=0 时 −1 胜，
 * **符号翻转**（DECISIONS §2026-10-21-godai-a4-return-hysteresis 第六节）。
 * 所以凡是「执行层行为」的 A/B，都走本工具：逐 (stage, seed) 串行、把真实
 * stageIndex 交给 `runSimulation`，两臂共用同一 CRN 种子。
 *
 * 口径与 eval-suite 一致：v7 评分（`V7_SCORE_CONFIG`）+ `DEFAULT_STAGE_REFS`，
 * 胜率按 `outcome === 'stage_clear'`，配对统计用 `comparePaired`。
 *
 * 用法：
 *   bun tools/eval/paired-serial.ts <difficulty> <seeds> <jsonA> <jsonB> [--stages]
 *   bun tools/eval/paired-serial.ts hard 60 '{}' '{"directMoveFallbackHold":40}'
 *   bun tools/eval/paired-serial.ts chaos 20 '{"a":1}' '{"a":2}' --stages
 *
 * 成本：35×60×2 局 ≈ 6 min（hard）、1.5 min（classic）、9 min（chaos），单核串行。
 * `--stages` 追加逐关明细（胜率 + 平均分 A→B）。
 */
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../../src/ai/GodAIInput'
import { runSimulation } from '../sim/simulation-runner'
import {
  scoreRun,
  aggregateStage,
  aggregateSuite,
  comparePaired,
  DEFAULT_STAGE_REFS,
  V7_SCORE_CONFIG,
  type RunScore,
  type ScorableRun,
} from './godai-score'

const difficulty = process.argv[2] ?? 'hard'
const seeds = Number(process.argv[3] ?? 60)
const aJson = process.argv[4] ?? '{}'
const bJson = process.argv[5] ?? '{}'
const a = {
  ...DEFAULT_GOD_AI_PARAMS,
  ...(JSON.parse(aJson) as Partial<GodAIParams>),
} as GodAIParams
const b = {
  ...DEFAULT_GOD_AI_PARAMS,
  ...(JSON.parse(bJson) as Partial<GodAIParams>),
} as GodAIParams

function runOne(stageIndex: number, seed: number, params: GodAIParams): ScorableRun {
  const r = runSimulation({
    seed,
    stage: STAGES[stageIndex],
    difficulty,
    stageIndex,
    godAIParams: params,
    maxTicks: 18000,
    collectMetrics: false,
    collectEvents: false,
    telemetry: true,
  })
  return {
    outcome: r.outcome,
    ticks: r.ticks,
    finalState: {
      killCount: r.finalState.killCount,
      lives: r.finalState.lives,
      baseAlive: r.finalState.baseAlive,
    },
    firstKillTick: r.firstKillTick,
    telemetry: r.telemetry,
  }
}

const cfg = { ...V7_SCORE_CONFIG, refs: DEFAULT_STAGE_REFS }
const scA: RunScore[] = []
const scB: RunScore[] = []
const perStageA = new Map<number, RunScore[]>()
const perStageB = new Map<number, RunScore[]>()
let winA = 0
let winB = 0
let killsA = 0
let killsB = 0

const t0 = performance.now()
for (let s = 0; s < STAGES.length; s++) {
  for (let k = 0; k < seeds; k++) {
    const ra = runOne(s, k, a)
    const rb = runOne(s, k, b)
    const sa = scoreRun(ra, cfg)
    const sb = scoreRun(rb, cfg)
    scA.push(sa)
    scB.push(sb)
    if (!perStageA.has(s)) perStageA.set(s, [])
    if (!perStageB.has(s)) perStageB.set(s, [])
    perStageA.get(s)!.push(sa)
    perStageB.get(s)!.push(sb)
    if (ra.outcome === 'stage_clear') winA++
    if (rb.outcome === 'stage_clear') winB++
    killsA += ra.finalState.killCount
    killsB += rb.finalState.killCount
  }
}

const names = STAGES.map((_s, i) => `s${i + 1}`)
const aggA = names.map((n, i) => aggregateStage(n, perStageA.get(i)!))
const aggB = names.map((n, i) => aggregateStage(n, perStageB.get(i)!))
const suiteA = aggregateSuite(aggA)
const suiteB = aggregateSuite(aggB)
const cmp = comparePaired(
  scA.map((x) => x.score),
  scB.map((x) => x.score),
)
const winCmp = comparePaired(
  scA.map((x) => (x.cleared ? 1 : 0)),
  scB.map((x) => (x.cleared ? 1 : 0)),
)
const n = STAGES.length * seeds
console.log(
  `${difficulty} ${STAGES.length}x${seeds}=${n} runs, ${((performance.now() - t0) / 1000).toFixed(1)}s`,
)
console.log(`A ${JSON.stringify(aJson)}`)
console.log(`B ${JSON.stringify(bJson)}`)
console.log(
  `win  A ${winA}/${n} = ${((100 * winA) / n).toFixed(1)}%   B ${winB}/${n} = ${((100 * winB) / n).toFixed(1)}%   Δ ${(((winB - winA) / n) * 100).toFixed(2)}pp  (paired Δ ${winCmp.meanDelta.toFixed(4)} ±${winCmp.se.toFixed(4)}, t=${winCmp.t.toFixed(2)}, +${winCmp.wins}/-${winCmp.losses}/=${winCmp.ties})`,
)
console.log(
  `kills A ${killsA} B ${killsB} (Δ ${killsB - killsA})   suite A ${suiteA.suite.toFixed(4)} B ${suiteB.suite.toFixed(4)}   score A ${(scA.reduce((x, y) => x + y.score, 0) / n).toFixed(4)} B ${(scB.reduce((x, y) => x + y.score, 0) / n).toFixed(4)}`,
)
console.log(
  `paired score Δ ${cmp.meanDelta.toFixed(4)} ±${cmp.se.toFixed(4)}  t=${cmp.t.toFixed(2)}  +${cmp.wins}/-${cmp.losses}/=${cmp.ties}`,
)
if (process.argv[6] === '--stages') {
  for (let i = 0; i < STAGES.length; i++) {
    const wa = aggA[i].winRate
    const wb = aggB[i].winRate
    console.log(
      `  s${i + 1} ${STAGES[i].name}: A ${(100 * wa).toFixed(0)}% B ${(100 * wb).toFixed(0)}%  mean ${aggA[i].mean.toFixed(3)}→${aggB[i].mean.toFixed(3)}`,
    )
  }
}
