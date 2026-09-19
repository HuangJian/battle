/**
 * m1-eval 分派契约单测（2026-09-19）——dist 链交给 Python（BatchEvalRunner）后，
 * 本文件只剩三个必须钉住的纯函数约定：
 *   1. `buildDistSpec`：任务子集 → spec（unit = 内置关 + 种子段；stage 升序、窗口不截断、
 *      单局超时沿用旧公式）——错一处就是「少跑局」或「慢节点上的长局被砍」；
 *   2. `rowToSimResult`：Python 逐局行 → SimTaskResult（0/1 → bool、error 局 ok=false、
 *      `scorable` 原样带回、缺列不伪造成 0）；
 *   3. `DIST_POLICIES`：能分派的 policy 白名单（与 Python `eval_m1_once.DISPATCHABLE` 对齐）。
 */
import { describe, it, expect } from 'bun:test'
import type { SimTask } from '../tools/sim/sim-worker'
import {
  DIST_POLICIES,
  buildDistSpec,
  rowToSimResult,
  type DistCtx,
  type M1DistRow,
} from '../tools/sim/m1-eval'

const ctx: DistCtx = {
  cfgPath: 'nn-training/rl-config.json',
  policy: 'goal',
  weights: 'tmp/gw.json',
  difficulty: 'hard',
  maxTicks: 36000,
  localSlots: 0,
  iterId: 'm1eval-test',
  runDir: 'tmp/x.m1run',
  out: 'tmp/x.m1run/rows.jsonl',
}

const task = (id: number, stageIndex: number, seed: number): SimTask =>
  ({ id, seed, stageIndex, maxTicks: 36000, difficulty: 'hard' }) as unknown as SimTask

describe('m1-eval buildDistSpec', () => {
  it('按内置关分组（stage 升序），种子原样成段', () => {
    const spec = buildDistSpec(
      ctx,
      [task(0, 3, 7), task(1, 1, 1), task(2, 3, 8), task(3, 1, 2)],
      true,
    )
    expect(spec.units).toEqual([
      { stageId: 1, seeds: [1, 2] },
      { stageId: 3, seeds: [7, 8] },
    ])
    expect(spec.fresh).toBe(true)
    expect(spec.policy).toBe('goal')
    expect(spec.weights).toBe('tmp/gw.json')
    expect(spec.distCfgPath).toBe('nn-training/rl-config.json')
    expect(spec.localSlots).toBe(0)
    expect(spec.runDir).toBe('tmp/x.m1run')
    expect(spec.out).toBe('tmp/x.m1run/rows.jsonl')
  })

  it('窗口不截断 + 单局超时沿用 maxTicks/20+120（36000 → 1920s）', () => {
    const spec = buildDistSpec(ctx, [task(0, 0, 1)], false)
    expect(spec.windowSec).toBe(86400)
    expect(spec.taskTimeoutSec).toBe(1920)
    expect(spec.fresh).toBe(false) // 重跑子集不得清台账
    // 小 maxTicks 时公式退化到 120s 下限，不是 0
    expect(buildDistSpec({ ...ctx, maxTicks: 100 }, [task(0, 0, 1)], false).taskTimeoutSec).toBe(
      125,
    )
  })

  it('重跑子集只含失败局（unit 数随子集缩小）', () => {
    const spec = buildDistSpec(ctx, [task(9, 4, 11)], false)
    expect(spec.units).toEqual([{ stageId: 4, seeds: [11] }])
  })
})

describe('m1-eval rowToSimResult', () => {
  const row: M1DistRow = {
    stage: 2,
    seed: 5,
    node: 'mac',
    ok: true,
    outcome: 'stage_clear',
    win: true,
    cleared: true,
    ticks: 1200,
    kills: 20,
    lives: 2,
    baseAlive: true,
    firstKillTick: 300,
    enemyTotal: 20,
    playerDeaths: 1,
    playerShots: 55,
    powerUpsCollected: 2,
    playerLevel: 3,
    cellsVisited: 88,
    scorable: {
      outcome: 'stage_clear',
      ticks: 1200,
      finalState: { killCount: 20, lives: 2, baseAlive: true },
      firstKillTick: 300,
      telemetry: { baseWallIntact: 12, baseWallTotal: 16, playerShots: 55 },
    },
  }

  it('逐局行 → SimTaskResult：标量 + scorable 原样带回', () => {
    const r = rowToSimResult(row, 42)
    expect(r.id).toBe(42)
    expect(r.ok).toBe(true)
    expect(r.outcome).toBe('stage_clear')
    expect(r.killCount).toBe(20)
    expect(r.cleared).toBe(true)
    expect(r.lives).toBe(2)
    expect(r.baseAlive).toBe(true)
    expect(r.firstKillTick).toBe(300)
    // scoreV7 的输入原样（不做字段级搬运）
    expect(r.scorable).toBe(row.scorable as never)
    expect(r.telemetry).toBe((row.scorable as { telemetry: unknown }).telemetry as never)
  })

  it('error 局 ok=false；缺 scorable 时按标量列合成 telemetry（不伪造墙完整度）', () => {
    const r = rowToSimResult({ stage: 0, seed: 1, node: 'a95', outcome: 'error' }, 7)
    expect(r.ok).toBe(false)
    expect(r.outcome).toBe('error')
    expect(r.ticks).toBe(0)
    expect(r.killCount).toBe(0)
    expect(r.scorable).toBeUndefined()
    // 缺列 → 0/空（不是 undefined 崩），lives 缺 → undefined（不伪造成 0 命）
    expect(r.lives).toBeUndefined()
    expect(r.telemetry?.playerShots).toBe(0)
    expect(r.telemetry?.baseWallTotal).toBe(0)
  })

  it('显式 ok:false 的已结算行不因 outcome 正常被当成功', () => {
    const r = rowToSimResult({ stage: 0, seed: 1, ok: false, outcome: 'max_ticks' }, 1)
    expect(r.ok).toBe(false)
  })
})

describe('m1-eval 分派白名单', () => {
  it('只分派 nn/intent-exec/goal/god（goal-god 不再分派）', () => {
    // nn 于 2026-09-19 入列：--weights-dir 解析出最新权重文件后经集群跑
    // （本地池与节点引擎的语义一致性由 tests/sim/eval-game-parity.test.ts 钉住）。
    expect([...DIST_POLICIES]).toEqual(['nn', 'intent-exec', 'goal', 'god'])
  })
})
