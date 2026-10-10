/**
 * metrics-v11-hotlane.test.ts —— metrics v11 热线族 2 列（idx74–75）的判定单测
 * （plan/metrics-v11-hotlane.plan.md §2/§3.5）。
 *
 * 覆盖：
 *   · `nearSqWeightTick`（idx74）：**独立重实现**对账 + 边界（有效半径 2 格、出生保护期不算、
 *     全敌累加、玩家不在场 = 0）；
 *   · 热线窗（idx75）：开窗/计数/关窗四条路径（换道、源转向、源死亡、局终）+ 同源重开 +
 *     多源并行 + **豁免不计数但窗不掉**（决定 A 的分工）；
 *   · **R10 同进程隔离**：两个窗实例互不影响（严禁模块级状态）；
 *   · mixed-version 分母契约（§3.5 新②，`v11KnownDelta`）；
 *   · 真实 rollout 金标（§3.5 新① **改判**，理由见文末）：采样链 / 评估链各自的确定性
 *     非零金标 + **单实现哨兵**（跨引擎逐值等式作废——采样 ≠ greedy，见文末长注）。
 *
 * 体例仿 `tests/sim/metrics-v10-exposure.test.ts`：独立重实现只读 World 原始字段。
 */
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { CELL, GRID } from '../../src/constants'
import type { World } from '../../src/game/World'
import { runOneBench } from '../../tools/sim/export-rl-rollout'
import { runEvalOne } from '../../tools/sim/export-eval-game'
import { STAGES } from '../../src/config/stages'
import {
  K_NEAR_SQ,
  MAX_HOT_LANES,
  createHotLaneWindow,
  hotLaneAxisOf,
  hotLaneOpen,
  hotLaneOpenFromTank,
  hotLaneTick,
  nearSqWeightTick,
} from '../../src/nn/danger-metrics'
import { placeEnemy, positionPlayer, seedWorld, makeTank } from '../helpers'

const PCOL = 8
const PROW = 8

function emptyField(w: World): void {
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) w.tileMap.set(c, r, 'empty')
  }
}

/** 干净世界：有玩家、无敌人、空场地（要测的是谓词本身，不是「那一格砖」）。 */
function worldWithPlayer(seed = 1): World {
  const w = seedWorld(seed)
  w.startGame('hard', 'modern', 0)
  positionPlayer(w, PCOL, PROW)
  emptyField(w)
  w.spawnQueue = []
  w.enemiesRemaining = 0
  w.tanks = w.tanks.filter((t) => t.isPlayer)
  return w
}

/** 独立重实现（刻意不 import 生产内部实现）：只按口径读 World 原始字段。 */
function indepNearSq(w: World): number {
  const p = w.player
  if (!p || !p.alive) return 0
  const pcol = Math.floor((p.x + p.w / 2) / CELL)
  const prow = Math.floor((p.y + p.h / 2) / CELL)
  let sum = 0
  for (const t of w.allTanks) {
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const d = Math.max(
      Math.abs(Math.floor((t.x + t.w / 2) / CELL) - pcol),
      Math.abs(Math.floor((t.y + t.h / 2) / CELL) - prow),
    )
    if (d <= 3) sum += (3 - d) * (3 - d)
  }
  return sum
}

/** 在相对玩家的 (dc, dr) **整格**处置一辆已激活敌车。
 * 锚点用玩家自己的 x/y（不走 `placeEnemy` 的格→像素约定）：本文件测的是**中心格谓词**，
 * 锚点必须与玩家同格心口径，否则测到的是辅助函数的留边。 */
function enemyAt(w: World, dc: number, dr: number): void {
  const p = w.player!
  const e = placeEnemy(w, 0, 0)
  e.x = p.x + dc * CELL
  e.y = p.y + dr * CELL
  e.spawnTimer = 0
  e.alive = true
}

/** 中心格（生产口径 = 像素中心 floor）。 */
function centerCol(t: { x: number; w: number }): number {
  return Math.floor((t.x + t.w / 2) / CELL)
}

describe('idx74 nearSqSum：核 / 半径 / 全敌累加（含独立重实现对账）', () => {
  it('常数冻结：K_NEAR_SQ = 3（有效半径 2 格）', () => {
    expect(K_NEAR_SQ).toBe(3)
  })

  it('d = 0/1/2 分别贡献 9/4/1；d = 3 贡献 0（有效半径 = 2 格，不是「3 格带」）', () => {
    for (const [d, want] of [
      [0, 9],
      [1, 4],
      [2, 1],
      [3, 0],
      [4, 0],
    ] as const) {
      const w = worldWithPlayer()
      enemyAt(w, d, 0)
      expect(nearSqWeightTick(w)).toBe(want)
      expect(indepNearSq(w)).toBe(want)
    }
  })

  it('全敌累加（包夹的第二个也算钱）且与独立重实现逐值相等', () => {
    const w = worldWithPlayer()
    enemyAt(w, 1, 0) // 4
    enemyAt(w, 0, 2) // 1
    enemyAt(w, 9, 9) // 0（远）
    expect(nearSqWeightTick(w)).toBe(5)
    expect(indepNearSq(w)).toBe(5)
  })

  it('出生保护期（spawnTimer > 0）不算近敌；未激活车不计', () => {
    const w = worldWithPlayer()
    const e = placeEnemy(w, PCOL + 1, PROW)
    e.spawnTimer = 30 // 保护期
    expect(nearSqWeightTick(w)).toBe(0)
  })

  it('玩家不在场 / 阵亡 ⇒ 0（与 dangerTicks/threatTicks 同规：死亡帧不算暴露）', () => {
    const w = worldWithPlayer()
    enemyAt(w, 1, 0)
    expect(nearSqWeightTick(w)).toBeGreaterThan(0)
    w.player!.alive = false
    expect(nearSqWeightTick(w)).toBe(0)
  })
})

describe('idx75 热线窗：开 / 计数 / 关（四条路径）/ 重开 / 多源 / 豁免分工', () => {
  /** 开窗 + 逐拍推进的公共动作。 */
  function openFrom(w: World, win = createHotLaneWindow()) {
    const e = w.allTanks.find((t) => t.allegiance === 'enemy')!
    hotLaneOpenFromTank(win, w, e.id)
    return { win, e }
  }

  it('伤害事件（源朝下、玩家同轴）⇒ 开窗，且同轴拍逐拍 +1', () => {
    const w = worldWithPlayer()
    enemyAt(w, 0, -3)
    const e = w.allTanks.find((t) => t.allegiance === 'enemy')!
    e.dir = 'down' // 沿玩家所在列
    const { win } = openFrom(w)
    expect(win.slots.length).toBe(1)
    expect(win.slots[0].lane.axis).toBe(centerCol(e))
    expect(win.slots[0].lane.axis).toBe(centerCol(w.player!)) // 与玩家同列（就是被击中那条轴）
    expect(win.slots[0].lane.vertical).toBe(true) // 源朝下 ⇒ 沿列
    expect(hotLaneTick(win, w, false)).toBe(1)
    expect(hotLaneTick(win, w, false)).toBe(1)
  })

  it('玩家脱离轴 ⇒ 关窗（不再计数，窗也消失）', () => {
    const w = worldWithPlayer()
    enemyAt(w, 0, -3)
    w.allTanks.find((t) => t.allegiance === 'enemy')!.dir = 'down'
    const { win } = openFrom(w)
    expect(hotLaneTick(win, w, false)).toBe(1)
    w.player!.x += 2 * CELL // 换道（脱离该源的火线轴）
    expect(hotLaneTick(win, w, false)).toBe(0)
    expect(win.slots.length).toBe(0)
  })

  it('源转向（朝向轴变了）⇒ 关窗', () => {
    const w = worldWithPlayer()
    enemyAt(w, 0, -3)
    const e = w.allTanks.find((t) => t.allegiance === 'enemy')!
    e.dir = 'down'
    const { win } = openFrom(w)
    expect(hotLaneTick(win, w, false)).toBe(1)
    e.dir = 'right' // 换向换道
    expect(hotLaneTick(win, w, false)).toBe(0)
    expect(win.slots.length).toBe(0)
  })

  it('源死亡 ⇒ 关窗；玩家阵亡 ⇒ 全关（局终）', () => {
    const w = worldWithPlayer()
    enemyAt(w, 0, -3)
    const e = w.allTanks.find((t) => t.allegiance === 'enemy')!
    e.dir = 'down'
    const { win } = openFrom(w)
    e.alive = false
    expect(hotLaneTick(win, w, false)).toBe(0)
    expect(win.slots.length).toBe(0)

    const w2 = worldWithPlayer()
    enemyAt(w2, 0, -3)
    w2.allTanks.find((t) => t.allegiance === 'enemy')!.dir = 'down'
    const o2 = openFrom(w2)
    w2.player!.alive = false
    expect(hotLaneTick(o2.win, w2, false)).toBe(0)
    expect(o2.win.slots.length).toBe(0)
  })

  it('豁免拍：不计数但**窗不掉**（决定 A 的分工——关窗与计数是两件事）', () => {
    const w = worldWithPlayer()
    enemyAt(w, 0, -3)
    w.allTanks.find((t) => t.allegiance === 'enemy')!.dir = 'down'
    const { win } = openFrom(w)
    expect(hotLaneTick(win, w, true)).toBe(0) // 冻/盾道具窗
    expect(win.slots.length).toBe(1) // 窗还在（不是被误关）
    expect(hotLaneTick(win, w, false)).toBe(1) // 恢复后继续计
  })

  it('同源重开 = 替换旧窗（一源一窗；不重复计钱）', () => {
    const w = worldWithPlayer()
    enemyAt(w, 0, -3)
    const e = w.allTanks.find((t) => t.allegiance === 'enemy')!
    e.dir = 'down'
    const { win } = openFrom(w)
    hotLaneOpenFromTank(win, w, e.id)
    expect(win.slots.length).toBe(1)
    expect(win.slots[0].sourceId).toBe(e.id)
  })

  it('多源并行：两条热线各计各的（净值 = 2）', () => {
    const w = worldWithPlayer()
    enemyAt(w, 0, -3) // 同列 ⇒ 沿列
    enemyAt(w, -3, 0) // 同行 ⇒ 沿行
    const es = w.allTanks.filter((t) => t.allegiance === 'enemy')
    es[0].dir = 'down'
    es[1].dir = 'right'
    const win = createHotLaneWindow()
    for (const e of es) hotLaneOpenFromTank(win, w, e.id)
    expect(win.slots.length).toBe(2)
    expect(hotLaneTick(win, w, false)).toBe(2)
  })

  it('R10 同进程隔离：两个窗实例互不影响（严禁模块级状态）', () => {
    const w = worldWithPlayer()
    enemyAt(w, 0, -3)
    w.allTanks.find((t) => t.allegiance === 'enemy')!.dir = 'down'
    const a = createHotLaneWindow()
    const b = createHotLaneWindow() // 「第二局」
    hotLaneOpenFromTank(a, w, w.allTanks.find((t) => t.allegiance === 'enemy')!.id)
    expect(a.slots.length).toBe(1)
    expect(b.slots.length).toBe(0) // 第二局不继承第一局的窗
    expect(hotLaneTick(b, w, false)).toBe(0)
  })

  it('溢出：超 MAX_HOT_LANES 丢弃并计数（warn 即非静默）', () => {
    const win = createHotLaneWindow()
    const t = makeTank({ kind: 'basic' })
    for (let i = 0; i < MAX_HOT_LANES + 3; i++) {
      t.id = 1000 + i
      hotLaneOpen(win, t.id, hotLaneAxisOf(t))
    }
    expect(win.slots.length).toBe(MAX_HOT_LANES)
    expect(win.dropped).toBe(3)
  })
})

describe('mixed-version 分母契约（§3.5 新②，R15）', () => {
  it('缺键整局不入分母；有键局按值入账（见证键 = nearSqSum）', async () => {
    const { v11KnownDelta } = await import('../../tools/sim/eval-course-ckpt')
    // v11 行（两列都在）
    expect(v11KnownDelta({ nearSqSum: 12, postHitLaneTicks: 5 })).toEqual({
      known: 1,
      nearSqSum: 12,
      postHitLaneTicks: 5,
    })
    // v10 及更早的行（整键缺席）⇒ 0 入账、不入分母（不伪造 0 去稀释均值）
    expect(v11KnownDelta({})).toEqual({ known: 0, nearSqSum: 0, postHitLaneTicks: 0 })
    // 混合两局：分母 = 1，均值 = 12/1（而不是 12/2）
    const rows = [{ nearSqSum: 12, postHitLaneTicks: 5 }, {}]
    let known = 0
    let sum = 0
    for (const r of rows) {
      const d = v11KnownDelta(r)
      known += d.known
      sum += d.nearSqSum
    }
    expect(known).toBe(1)
    expect(sum / Math.max(1, known)).toBe(12)
  })
})

/**
 * §3.5 新① **改判**（2026-10-10 实测；plan §8 记录）——跨引擎逐值等式作废。
 *
 * 原判据「同策略同 seed ⇒ rollout 终值 == eval manifest」在两条链上不可满足：训练链
 * （`export-rl-rollout` 决策处 `sampleCat`，**采样**）与评估链（`export-eval-game` 决策处
 * `argmaxCat`，**greedy**）决策模式不同——正是 §15.3「采样 ≠ greedy」写死的那条。
 * 实测同 (stage, seed)：s0/13 rollout ticks 2161 / nearSq 30 vs eval ticks 211 / nearSq 0；
 * s1/7 rollout ticks 441 vs eval ticks 211 ⇒ 等式两侧本就是**两局不同的对局**，逐值相等
 * 既不能证真也不能证伪列语义（改判前那两个 seed 两侧恰好都是 0，测试是**空洞**的）。
 *
 * 替代判据（都抓「接线漂移」，且都可判）：
 *   ① **采样链金标**：固定权重 + 固定 seed ⇒ 逐字节确定（两次跨进程实测相同）⇒ 漏
 *      `exempt` 门 / 漏开窗 / 窗口径漂移都会改值，非零正例 + 零例齐备；
 *   ② **评估链金标**：god 策略确定性，两列非零；
 *   ③ **单实现哨兵**：两导出器的两条累加点各恰好一次 + 都 import 共享核 ⇒ 不许第二份实现。
 */
const MAX_TICKS = 1500

/** 学生权重夹具（与 `eval-game-parity` 的 nn 分支同一份；h=16/d=2 瘦身规格）：
 *  fixture 里是裸 `{h,d,params}` ⇒ 必须包 arch 才喂得进 `buildModelFromText`。 */
const studentWeights = (() => {
  const g = JSON.parse(
    readFileSync(join(import.meta.dir, '..', 'fixtures', 'student-golden.json'), 'utf8'),
  ) as { h: number; d: number; params: Record<string, unknown> }
  return JSON.stringify({ arch: { kind: 'student', h: g.h, d: g.d }, params: g.params })
})()

describe('真实 rollout 金标（采样链，§3.5 新① 改判）', () => {
  const cases = [
    { stage: 1, seed: 7, near: 0, lane: 12, note: 'lane 非零' },
    { stage: 0, seed: 13, near: 2, lane: 0, note: 'near 非零' },
    { stage: 2, seed: 3, near: 56, lane: 0, note: 'near 更大 + 零 lane' },
  ] as const
  for (const c of cases) {
    it(`stage ${c.stage} seed ${c.seed}：near=${c.near} / lane=${c.lane}（${c.note}）`, () => {
      const r = runOneBench(
        c.stage,
        STAGES[c.stage] as never,
        c.seed,
        'hard',
        MAX_TICKS,
        studentWeights,
      )
      expect(r.nearSqSum).toBe(c.near)
      expect(r.postHitLaneTicks).toBe(c.lane)
    })
  }

  it('同 seed 连跑两遍逐值相同（金标可复现；同进程第二局不继承第一局的窗）', () => {
    const a = runOneBench(0, STAGES[0] as never, 13, 'hard', MAX_TICKS, studentWeights)
    const b = runOneBench(0, STAGES[0] as never, 13, 'hard', MAX_TICKS, studentWeights)
    expect(b.nearSqSum).toBe(a.nearSqSum)
    expect(b.postHitLaneTicks).toBe(a.postHitLaneTicks)
    expect(a.nearSqSum).toBe(2) // 非零正例：金标不是 0 == 0
  })
})

describe('评估链金标（god 确定性策略；两列非零）', () => {
  const cases = [
    { stage: 0, seed: 9, near: 138, lane: 202 },
    { stage: 0, seed: 5, near: 88, lane: 135 },
    { stage: 1, seed: 6, near: 177, lane: 84 },
  ] as const
  for (const c of cases) {
    it(`stage ${c.stage} seed ${c.seed}：near=${c.near} / lane=${c.lane}`, () => {
      const r = runEvalOne(
        c.stage,
        STAGES[c.stage] as never,
        c.seed,
        'hard',
        MAX_TICKS,
        '{}',
        'god',
      )
      expect(r.nearSqSum).toBe(c.near)
      expect(r.postHitLaneTicks).toBe(c.lane)
    })
  }
})

describe('单实现哨兵（两导出器共用同一核；第二份实现即红）', () => {
  const flat = (s: string): string => s.replace(/\s+/g, ' ')
  const src = (rel: string): string =>
    flat(readFileSync(join(import.meta.dir, '..', '..', rel), 'utf8'))
  const rolloutSrc = src('tools/sim/export-rl-rollout.ts')
  const evalSrc = src('tools/sim/export-eval-game.ts')

  it('累加点各恰好一次；核只 import，不在导出器里另写一份', () => {
    for (const s of [rolloutSrc, evalSrc]) {
      expect(s.match(/tel\.nearSqSum \+= nearSqWeightTick\(world\)/g)?.length).toBe(1)
      expect(
        s.match(/tel\.postHitLaneTicks \+= hotLaneTick\(hotLane, world, exempt\)/g)?.length,
      ).toBe(1)
      expect(s).toContain("} from '../../src/nn/danger-metrics'")
      expect(s).not.toContain('function nearSqWeightTick')
      expect(s).not.toContain('function hotLaneTick')
    }
  })

  it('nearSqSum 在豁免门内累计（决定 A 拌入；门挪走/删掉即红）', () => {
    for (const s of [rolloutSrc, evalSrc]) {
      expect(s).toContain(
        'if (!exempt) { tel.enclWeightTicks += enclWeightTick(world) tel.cornerWeightTicks += cornerWeightTick(world) tel.nearSqSum += nearSqWeightTick(world)',
      )
    }
  })

  it('热线窗每拍都推进（关窗与计数分离；把它挪进 if (!exempt) 即红）', () => {
    for (const s of [rolloutSrc, evalSrc]) {
      expect(s).toContain('tel.postHitLaneTicks += hotLaneTick(hotLane, world, exempt)')
    }
  })
})
