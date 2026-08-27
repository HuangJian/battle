#!/usr/bin/env bun
/**
 * rollout-intent-probe.ts — rollout 意图分布探针（M7② / intent RL 冷启动前风险评估）。
 *
 * 对给定一组意图权重文件，在**同一确定性 (stage,seed) 网格**上各自跑 ~N 局
 * intent-exec 策略的真实 rollout（复用 runSimulation 精确环路，保证与 m1-eval
 * 同策略同口径），按局记录每 replan 的原始 argmax 意图，聚合出该网络的：
 *   - 意图分布（8 类直方图 / 归一化概率）
 *   - 意图分布熵（Shannon，bits）
 *   - HUNT 占比（进攻主力意图 HUNT 的份额）
 *
 * 用途：比较 B′（71.7% 胜率）与 SS（60.1%）进入 M8 RL 前的行为差异——SS 是否
 * 更防御 / 更不集中（熵更高）/ HUNT 更少。对 RL 冷启动质量是决定性信号。
 *
 * 口径说明：
 *   - 主口径 = 每 replan 的**原始 argmax**（含未承诺切换的候选）——这正是自馈注入
 *     prev 序列推进的意图流，反映网络自身意图偏好，最接近 RL 冷启动时策略会产出的
 *     意图分布。
 *   - 次口径 = 实际**承诺**意图 trace（margin 门控后真正驱动玩法的一支）。
 *   - 每意向一次投票、等权聚合（分窗口决策），跨局汇总成单一经验分布。
 *
 * 用法（示例，两臂各 N 局，同一网格）：
 *   bun tools/sim/rollout-intent-probe.ts \
 *     --arm B tmp/intent-weights-Bp.json --arm SS tmp/intent-weights-SS.json \
 *     --games 50 --out tmp/rollout_intent_probe.json
 */
import { existsSync, writeFileSync } from 'node:fs'
import { STAGES } from '../../src/config/stages'
import { runSimulation } from './simulation-runner'
import { INTENT_IDS } from '../../src/ai/intent/vocab'

const HUNT_IDX = INTENT_IDS.indexOf('HUNT')

interface ArmSpec {
  name: string
  weightsPath: string
}

function shannonBits(counts: number[]): number {
  const total = counts.reduce((a, b) => a + b, 0)
  if (total <= 0) return 0
  let h = 0
  for (let i = 0; i < counts.length; i++) {
    const p = counts[i] / total
    if (p > 0) h -= p * Math.log2(p)
  }
  return h
}

function aggregate(
  traces: number[][],
  size: number,
): {
  counts: number[]
  dist: number[]
  entropyBits: number
  prop: number[]
  huntShare: number
  frames: number
} {
  const counts = new Array<number>(size).fill(0)
  let frames = 0
  for (const t of traces) {
    for (const it of t) {
      if (it >= 0 && it < size) {
        counts[it]++
        frames++
      }
    }
  }
  const dist = counts.map((c) => (frames ? c / frames : 0))
  const prop = counts.map((c) => c / counts.length)
  void prop // dist suffices for the report
  return {
    counts,
    dist,
    entropyBits: shannonBits(counts),
    prop,
    huntShare: frames ? counts[HUNT_IDX] / frames : 0,
    frames,
  }
}

function main(): void {
  const args = process.argv.slice(2)
  const arms: ArmSpec[] = []
  let games = 50
  let maxTicks = 36000
  let difficulty = 'hard'
  let outPath = 'tmp/rollout_intent_probe.json'
  let replan = 0
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--arm') {
      const name = args[++i]
      const p = args[++i]
      if (!name || !p) throw new Error('--arm needs <name> <weights.json>')
      arms.push({ name, weightsPath: p })
    } else if (args[i] === '--games') games = parseInt(args[++i], 10)
    else if (args[i] === '--max-ticks') maxTicks = parseInt(args[++i], 10)
    else if (args[i] === '--difficulty') difficulty = args[++i]
    else if (args[i] === '--out') outPath = args[++i]
    else if (args[i] === '--replan') replan = parseInt(args[++i], 10)
  }
  for (const a of arms) {
    if (!existsSync(a.weightsPath)) throw new Error(`weights not found: ${a.weightsPath}`)
  }
  if (arms.length === 0) {
    process.stderr.write(
      'usage: rollout-intent-probe.ts --arm <name> <weights> [--arm ...] [--games N]\n',
    )
    process.exit(2)
  }

  // 确定性网格：k → (stage, stageIndex, seed)。先铺满全部 35 关（seed=1），再多过几遍
  // 用递增 seed 换深度（两臂严格共用同一网格）。
  interface Cell {
    stageIdx: number
    seed: number
  }
  const cells: Cell[] = []
  for (let k = 0; k < games; k++) {
    const stageIdx = k % STAGES.length
    const seed = 1 + Math.floor(k / STAGES.length)
    cells.push({ stageIdx, seed })
  }

  process.stderr.write(
    `[intent-probe] arms=${arms.map((a) => a.name).join(',')} games=${games} grid=${STAGES.length} stages × few seeds difficulty=${difficulty}\n`,
  )

  interface ArmResult {
    name: string
    weightsPath: string
    games: number
    wins: number
    winsOnly: boolean
    winRate: number
    replanFrames: number
    records: {
      game: number
      stage: string
      stageIdx: number
      seed: number
      outcome: string
      win: boolean
      committed: number[]
    }[]
    replan: {
      counts: number[]
      dist: number[]
      entropyBits: number
      huntShare: number
      frames: number
    }
    committed: {
      counts: number[]
      dist: number[]
      entropyBits: number
      huntShare: number
      frames: number
    }
  }

  // Run one arm serially (deterministic; identical loop as m1-eval via runSimulation).
  function runArm(spec: ArmSpec): ArmResult {
    const records: ArmResult['records'] = []
    const replanTraces: number[][] = []
    const committedTraces: number[][] = []
    let wins = 0
    const t0 = Date.now()
    for (let g = 0; g < cells.length; g++) {
      const { stageIdx, seed } = cells[g]
      const stage = STAGES[stageIdx]
      const res = runSimulation({
        seed,
        stage,
        stageIndex: stageIdx,
        difficulty,
        policy: 'intent-exec',
        intentWeightsDir: spec.weightsPath,
        replanEvery: replan || undefined,
        maxTicks,
        collectMetrics: false,
        collectEvents: false,
        recordIntentTrace: true,
      })
      const win = res.outcome === 'stage_clear'
      if (win) wins++
      replanTraces.push((res.replanIntentTrace ?? []).map((r) => r.intent))
      committedTraces.push(res.committedIntentTrace ?? [])
      records.push({
        game: g,
        stage: stage.name,
        stageIdx,
        seed,
        outcome: res.outcome,
        win,
        committed: res.committedIntentTrace ?? [],
      })
    }
    const elapsed = Date.now() - t0
    const isWinOnly = wins === games
    process.stderr.write(
      `[intent-probe] ${spec.name}: ${wins}/${games} wins (${(wins / games) * 100}%), intent frames=${replanTraces.reduce((a, t) => a + t.length, 0)} in ${(elapsed / 1000).toFixed(1)}s\n`,
    )
    void isWinOnly
    return {
      name: spec.name,
      weightsPath: spec.weightsPath,
      games: cells.length,
      wins,
      winsOnly: isWinOnly,
      winRate: wins / cells.length,
      replanFrames: replanTraces.reduce((a, t) => a + t.length, 0),
      records,
      replan: aggregate(replanTraces, INTENT_IDS.length),
      committed: aggregate(committedTraces, INTENT_IDS.length),
    }
  }

  const results = arms.map(runArm)

  // ---- comparison report ----
  const label = (i: number): string => INTENT_IDS[i] ?? `idx${i}`
  const fmtP = (v: number): string => `${(v * 100).toFixed(1)}%`
  for (const r of results) {
    console.log(
      `\n=== arm ${r.name} (${r.weightsPath}) — WIN ${r.wins}/${r.games} (${(r.winRate * 100).toFixed(1)}%) ===`,
    )
    console.log(
      `  replan 分布熵=${r.replan.entropyBits.toFixed(3)} bits · HUNT 占比=${fmtP(r.replan.huntShare)} (${r.replan.frames} frames)`,
    )
    console.log(
      `  承诺   分布熵=${r.committed.entropyBits.toFixed(3)} bits · HUNT 占比=${fmtP(r.committed.huntShare)} (${r.committed.frames} frames)`,
    )
    console.log(`  per-class (replan):`)
    r.replan.counts.forEach((c, i) => {
      console.log(
        `    ${label(i).padEnd(15)} ${c.toString().padStart(6)}  ${fmtP(r.replan.dist[i])}`,
      )
    })
  }
  if (results.length >= 2) {
    const [a, b] = results
    console.log(`\n=== 对比 ${a.name} vs ${b.name} ===`)
    console.log(
      `  WIN        ${a.name}=${(a.winRate * 100).toFixed(1)}%  ${b.name}=${(b.winRate * 100).toFixed(1)}%  Δ=${((a.winRate - b.winRate) * 100).toFixed(1)}pp`,
    )
    console.log(
      `  replan 熵  ${a.name}=${a.replan.entropyBits.toFixed(3)}  ${b.name}=${b.replan.entropyBits.toFixed(3)}  Δ=${(a.replan.entropyBits - b.replan.entropyBits).toFixed(3)} bits`,
    )
    console.log(
      `  replan HUNT ${fmtP(a.replan.huntShare)} vs ${fmtP(b.replan.huntShare)}  Δ=${((a.replan.huntShare - b.replan.huntShare) * 100).toFixed(1)}pp`,
    )
    console.log(
      `  承诺  熵  ${a.name}=${a.committed.entropyBits.toFixed(3)}  ${b.name}=${b.committed.entropyBits.toFixed(3)}  Δ=${(a.committed.entropyBits - b.committed.entropyBits).toFixed(3)} bits`,
    )
    console.log(
      `  承诺  HUNT ${fmtP(a.committed.huntShare)} vs ${fmtP(b.committed.huntShare)}  Δ=${((a.committed.huntShare - b.committed.huntShare) * 100).toFixed(1)}pp`,
    )
  }

  const output = {
    probe: 'rollout-intent',
    difficulty,
    games,
    grid: { stageCount: STAGES.length, cells },
    intents: INTENT_IDS,
    huntIdx: HUNT_IDX,
    arms: results.map((r) => ({
      name: r.name,
      weightsPath: r.weightsPath,
      games: r.games,
      wins: r.wins,
      winRate: r.winRate,
      replanFrames: r.replanFrames,
      replan: {
        counts: r.replan.counts,
        dist: r.replan.dist,
        entropyBits: r.replan.entropyBits,
        huntShare: r.replan.huntShare,
      },
      committed: {
        counts: r.committed.counts,
        dist: r.committed.dist,
        entropyBits: r.committed.entropyBits,
        huntShare: r.committed.huntShare,
      },
    })),
    comparison:
      results.length >= 2
        ? {
            winRateDelta: results[0].winRate - results[1].winRate,
            replanEntropyDelta: results[0].replan.entropyBits - results[1].replan.entropyBits,
            replanHuntDelta: results[0].replan.huntShare - results[1].replan.huntShare,
            committedEntropyDelta:
              results[0].committed.entropyBits - results[1].committed.entropyBits,
            committedHuntDelta: results[0].committed.huntShare - results[1].committed.huntShare,
          }
        : null,
  }
  writeFileSync(outPath, JSON.stringify(output, null, 2))
  process.stderr.write(`[intent-probe] done -> ${outPath}\n`)
}

main()
