import { describe, it, expect } from 'bun:test'
import { readFileSync, writeFileSync, mkdtempSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { runSimulation } from '../../tools/sim/simulation-runner'
import { runEvalOne } from '../../tools/sim/export-eval-game'
import { STAGES } from '../../src/config/stages'
import { BASE_POS, GRID } from '../../src/constants'
import type { StageData } from '../../src/config/types'

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

/**
 * x2 事件门（plan/new-era-stop.plan.md §6 R2）的部署/judge 对账。
 *
 * 两条断言链分开：
 *   ① **零事件局**（stage 0 / golden 夹具，实测 events=0）：事件模式与均匀模式逐字段相同
 *      ⇒ 「无事件局与旧版逐字节一致」（Readout 里 events=0 就是「无事件」的定义，不是假设）。
 *   ② **有事件局**（自定义关：单敌斜上方，必须转向才能对齐 ⇒ 必出翻沿）：部署链
 *      （runSimulation → NNInput）与判决链（export-eval-game 内联循环）仍同局，且 Δt ≥ 3。
 */
function fixtureWeights(): string {
  const g = JSON.parse(
    readFileSync(join(import.meta.dir, '..', 'fixtures', 'student-golden.json'), 'utf8'),
  ) as { h: number; d: number; params: Record<string, unknown> }
  return JSON.stringify({ arch: { kind: 'student', h: g.h, d: g.d }, params: g.params })
}

/** 26×26 全空 + 基地；玩家/单敌出生点可配（onset 需要「先不在带内、后对齐」的几何）。 */
function onsetStage(): StageData {
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) {
    let row = ''
    for (let c = 0; c < GRID; c++) row += '.'
    if (r === 24 || r === 25) {
      row = row.slice(0, BASE_POS.col) + 'EE' + row.slice(BASE_POS.col + 2)
    }
    tiles.push(row)
  }
  return {
    id: 9997,
    name: 'r2-onset-parity',
    tiles,
    enemies: ['basic'],
    enemyCount: 1,
    playerSpawn: { col: 12, row: 20 },
    enemySpawns: [{ col: 13, row: 15 }],
  }
}

/** 把权重写到临时目录（NNInput 走目录解析）。 */
function weightsDir(weightsText: string): string {
  const base = join(process.cwd(), 'tmp')
  mkdirSync(base, { recursive: true })
  const dir = mkdtempSync(join(base, 'nn-parity-x2-'))
  writeFileSync(join(dir, 'weights.json'), weightsText)
  return dir
}

describe('x2 事件门：部署/judge 同源 + Δt 不变式（R2）', () => {
  it('零事件局：事件模式与均匀模式逐字段相同（= 无事件局与旧版逐字节一致）', () => {
    const weightsText = fixtureWeights()
    const dir = weightsDir(weightsText)
    const uniform = runEvalOne(0, STAGES[0], 1, 'hard', 3000, weightsText, 'nn')
    const events = runEvalOne(
      0,
      STAGES[0],
      1,
      'hard',
      3000,
      weightsText,
      'nn',
      '',
      '',
      0,
      0,
      null,
      null,
      '',
      null,
      true,
    )
    const localEvents = runSimulation({
      seed: 1,
      stage: STAGES[0] as never,
      stageIndex: 0,
      difficulty: 'hard',
      policy: 'nn',
      nnWeightsDir: dir,
      nnDecisionEvents: true,
      maxTicks: 3000,
      collectMetrics: false,
    })
    expect(events.decisionReadout!.events).toBe(0) // 「无事件」是被实测的定义，不是前提假设
    expect(events.decisionReadout).toEqual(uniform.decisionReadout)
    expect(events.outcome).toBe(uniform.outcome)
    expect(events.ticks).toBe(uniform.ticks)
    expect(events.score).toBe(uniform.score)
    expect(events.decisionReadout!.minDt).toBe(10)
    // 部署链（NNInput）与判决链的决策流逐项相同
    expect(localEvents.nnDecisionReadout).toEqual(events.decisionReadout)
    expect(localEvents.ticks).toBe(events.ticks)
  })

  it('有事件局：部署链与判决链同局；事件进场且 Δt ≥ 3', () => {
    const weightsText = fixtureWeights()
    const dir = weightsDir(weightsText)
    const maxTicks = 1200
    const local = runSimulation({
      seed: 1,
      stage: onsetStage() as never,
      stageIndex: 0,
      difficulty: 'hard',
      policy: 'nn',
      nnWeightsDir: dir,
      nnDecisionEvents: true,
      maxTicks,
      collectMetrics: false,
    })
    const remote = runEvalOne(
      0,
      onsetStage(),
      1,
      'hard',
      maxTicks,
      weightsText,
      'nn',
      '',
      '',
      0,
      0,
      null,
      null,
      '',
      null,
      true,
    )
    expect(remote.outcome).toBe(local.outcome)
    expect(remote.ticks).toBe(local.ticks)
    const d = remote.decisionReadout!
    // 比 outcome/ticks 更强的一层：**决策流同源**（同一个 decisionDue）。
    //
    // 已知边界（**本刀之前就有**，不是新增）：本局以 `maxTicks` 截断收尾，本地链在最后一个
    // tick 结束后还会 `endFrame()` 一次（NNInput 的契约是「tick 结束就决下一步」），而判决链
    // 的 `while (t < maxTicks)` 不会观察那个状态 ⇒ 本地至多多出一条**驱动不了任何 tick**的尾部决策。
    // 终局型收尾（state ≠ playing）两边严格相等——见上一条零事件用例（n=22 == 22）。
    const lr = local.nnDecisionReadout!
    expect(d.events).toBeGreaterThan(0) // 事件真的进场（否则本用例什么都没验）
    expect(lr.events).toBe(d.events)
    expect(lr.minDt).toBe(d.minDt)
    expect(lr.maxDt).toBe(d.maxDt)
    expect(lr.n - d.n).toBeGreaterThanOrEqual(0)
    expect(lr.n - d.n).toBeLessThanOrEqual(1)
    expect(Math.abs(lr.avgDt - d.avgDt)).toBeLessThanOrEqual(0.01)
    expect(d.minDt).toBeGreaterThanOrEqual(3) // 最小间隔闸的不变式（R2.3）
    expect(d.maxDt).toBeLessThanOrEqual(10)
    // 事件补充只加决策，不得减少：n ≥ 均匀下界
    expect(d.n).toBeGreaterThanOrEqual(Math.floor(remote.ticks / 10))
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
