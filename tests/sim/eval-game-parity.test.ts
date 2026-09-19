import { describe, it, expect } from 'bun:test'
import { readFileSync, writeFileSync, mkdtempSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { runSimulation } from '../../tools/sim/simulation-runner'
import { runEvalOne } from '../../tools/sim/export-eval-game'
import { STAGES } from '../../src/config/stages'

/**
 * v4.0 远程分发等价性（用户指令 2026-08-29：m1-eval 每批都要利用远程 agents）：
 * export-eval-game 的 god 分支必须与本地 runSimulation(policy god) 产出**同一局**——
 * 远程 agents 跑的是 export-eval-game，本地跑的是 runSimulation；两者不等价时，
 * 分发批与本地批不可配对（门判定作废）。
 *
 * 可区分性冒烟（goal-god 伪影教训）：god 与 goal-god 必须产生可区分的结果——
 * 防止"新 policy 静默回落已知策略"再次发生（回落时两者完全一致）。
 */
function parity(stageIdx: number, seed: number, maxTicks = 8000): void {
  const world = { stage: stageIdx, seed }
  const local = runSimulation({
    seed,
    stage: STAGES[stageIdx] as never,
    stageIndex: stageIdx,
    difficulty: 'hard',
    policy: 'god',
    maxTicks,
    collectMetrics: false,
  })
  const remote = runEvalOne(stageIdx, STAGES[stageIdx], seed, 'hard', maxTicks, '{}', 'god')
  expect(remote.outcome).toBe(local.outcome)
  expect(remote.ticks).toBe(local.ticks)
  expect(remote.win).toBe(local.ticks < maxTicks && local.outcome === 'stage_clear')
  void world
}

describe('export-eval-game god 分支与 runSimulation 等价（远程/本地对账）', () => {
  it('stage 0 seed 1/2/3 同局', () => {
    parity(0, 1)
    parity(0, 2)
    parity(0, 3)
  })

  it('stage 5 seed 1 同局（不同地形族）', () => {
    parity(5, 1)
  })
})

/**
 * nn 分支的远程/本地对账（2026-09-19 补）。
 *
 * 此前只有 god 分支被钉住，nn 的两套实现（远程 export-eval-game 的内联循环 vs 本地
 * runSimulation → NNInput）**从不互相对账**——于是两者的决策时点/相位各走各的：
 * local 在 tick 中途（Simulation 已递减计时器/跑过 updateSpawning 之后）观察并决策，
 * 而远程（以及语料生成器 export-rl-rollout / export-nn-replays）在 `sim.tick()` 之前
 * 用 `t % K === 0` 观察决策 ⇒ 同一 (权重, 关卡, 种子) 6/6 局不同（首个动作分歧在
 * stage 0 seed 1 的 tick 290）。本测试钉住两者逐局同结果。
 *
 * 权重用 tests/fixtures/student-golden.json 的 params（h=16/d=2 瘦身规格，forward 便宜）；
 * NNInput 走目录解析 ⇒ 落一份 `weights.json` 到临时目录（resolveLatestWeights 的兜底名）。
 */
function nnParity(
  stageIdx: number,
  seed: number,
  weightsText: string,
  dir: string,
  maxTicks: number,
): void {
  const local = runSimulation({
    seed,
    stage: STAGES[stageIdx] as never,
    stageIndex: stageIdx,
    difficulty: 'hard',
    policy: 'nn',
    nnWeightsDir: dir,
    maxTicks,
    collectMetrics: false,
  })
  const remote = runEvalOne(stageIdx, STAGES[stageIdx], seed, 'hard', maxTicks, weightsText, 'nn')
  expect(remote.outcome).toBe(local.outcome)
  expect(remote.ticks).toBe(local.ticks)
}

describe('export-eval-game nn 分支与 runSimulation 等价（决策时点/相位同源）', () => {
  it('stage 0 seed 1/2 同局（观察时点 + K 相位一致）', () => {
    const g = JSON.parse(
      readFileSync(join(import.meta.dir, '..', 'fixtures', 'student-golden.json'), 'utf8'),
    ) as { h: number; d: number; params: Record<string, unknown> }
    const weightsText = JSON.stringify({
      arch: { kind: 'student', h: g.h, d: g.d },
      params: g.params,
    })
    // NNInput 走目录解析（resolveLatestWeights）：落一份 `weights.json` 到临时目录。
    // 先建 tmp/ —— 它在 .gitignore 里，干净检出可能不存在。
    const base = join(process.cwd(), 'tmp')
    mkdirSync(base, { recursive: true })
    const dir = mkdtempSync(join(base, 'nn-parity-'))
    writeFileSync(join(dir, 'weights.json'), weightsText)
    nnParity(0, 1, weightsText, dir, 3000)
    nnParity(0, 2, weightsText, dir, 3000)
  })
})

describe('policy 可区分性冒烟（回落检测）', () => {
  it('god 与 goal-god 在同一局产生不同行为（非静默回落）', () => {
    const stageIdx = 0
    const seed = 1
    const maxTicks = 3000
    const god = runSimulation({
      seed,
      stage: STAGES[stageIdx] as never,
      stageIndex: stageIdx,
      difficulty: 'hard',
      policy: 'god',
      maxTicks,
      collectMetrics: false,
    })
    const goalGod = runSimulation({
      seed,
      stage: STAGES[stageIdx] as never,
      stageIndex: stageIdx,
      difficulty: 'hard',
      policy: 'goal-god',
      maxTicks,
      collectMetrics: false,
    })
    const distinct =
      god.outcome !== goalGod.outcome ||
      god.ticks !== goalGod.ticks ||
      god.finalState.killCount !== goalGod.finalState.killCount
    expect(distinct).toBe(true)
  })
})
