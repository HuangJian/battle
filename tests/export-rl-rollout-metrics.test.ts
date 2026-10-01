import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  applySuffixPatches,
  buildMetricsRow,
  METRICS_COLUMN_NAMES,
  METRICS_DIM,
  PICKUP_DIST_SENTINEL,
  resolveLivesFlag,
  type Telemetry,
} from '../tools/sim/export-rl-rollout'
import { seedWorld, positionPlayer, makePowerUp } from './helpers'

/**
 * metrics 行宽护栏（2026-09-12 P0 回归）。
 *
 * 背景：给指标向量加第 31 列 `clearTick` 时只改了 `buildMetricsRow` 的行、没改
 * `METRICS_DIM`（仍是 30），`writeRlShard` 的 `metrics.set(row, i * METRICS_DIM)`
 * 于是在**每一局**的终局行越界抛 `RangeError` —— 整条 RL 采集腿零产出，而
 * `bun run typecheck` 看不见（`number[]` 无长度类型）、也没有任何断言覆盖行宽。
 *
 * 这里锁两件事：
 *   1. 行宽 == `METRICS_DIM`（加/删列必须同步常量）；
 *   2. `METRICS_DIM` == Python `biz/reward_library.py::METRICS` 的条目数（跨语言 SSOT，
 *      manifest 的 metrics_version 与之绑定）。
 */

function makeTelemetry(over: Partial<Telemetry> = {}): Telemetry {
  return {
    enemyTotal: 20,
    startLives: 1,
    playerDeaths: 0,
    playerHits: 0,
    playerDamageTaken: 0,
    playerShots: 0,
    powerUpsSpawned: 0,
    powerUpsCollected: 0,
    starsCollected: 0,
    puSpawnBomb: 0,
    puSpawnTank: 0,
    puSpawnFreeze: 0,
    puSpawnShield: 0,
    puGotBomb: 0,
    puGotTank: 0,
    puGotFreeze: 0,
    puGotShield: 0,
    puGotOther: 0,
    puSpawnStar: 0,
    baseWallTotal: 8,
    baseWallIntact: 8,
    basePressureSum: 0,
    basePressureSamples: 0,
    cellsVisited: new Set<number>(),
    firstKillTick: undefined,
    clearTick: undefined,
    enemyHits: 0,
    killsByKind: [0, 0, 0, 0],
    hitsByKind: [0, 0, 0, 0],
    stuckTicks: 0,
    dangerTicks: 0,
    threatTicks: 0,
    dmgFirst600: 0,
    backHits: 0,
    sideHits: 0,
    frontHitsExempt: 0,
    farHits: 0,
    geoFallback: 0,
    onLaneTicks: 0,
    onLaneExemptTicks: 0,
    onLaneMoveTicks: 0,
    onLaneHoldFireTicks: 0,
    stopTicks: 0,
    fireHeldTicks: 0,
    idleTicks: 0,
    nearEnemy4Ticks: 0,
    damageWhileLow: 0,
    damageTicks: [],
    damageAmounts: [],
    encl1Ticks: 0,
    encl2Ticks: 0,
    encl3pTicks: 0,
    enclMax: 0,
    aimHits: 0,
    aimHitDistSum: 0,
    aimBricks: 0,
    aimIgnited: 0,
    aimMisses: 0,
    hurtWeight: 0,
    enclWeightTicks: 0,
    cornerWeightTicks: 0,
    ...over,
  }
}

describe('export-rl-rollout metrics 行宽', () => {
  it('buildMetricsRow 行宽 == METRICS_DIM', () => {
    const world = seedWorld(1)
    const row = buildMetricsRow(0, world, makeTelemetry())
    expect(row.length).toBe(METRICS_DIM)
    expect(row.every((v) => Number.isFinite(v))).toBe(true)
  })

  it('未清场 / 已清场两种哨兵都写进第 30 列，且不影响行宽', () => {
    const world = seedWorld(1)
    const nz = buildMetricsRow(7, world, makeTelemetry({ clearTick: 123 }))
    const sentinel = buildMetricsRow(7, world, makeTelemetry())
    expect(nz.length).toBe(METRICS_DIM)
    expect(sentinel.length).toBe(METRICS_DIM)
    // clearTick 固定 idx30（v5）；v6 在尾部追加分敌种列，不再假设「最后一列」
    expect(nz[30]).toBe(123)
    expect(sentinel[30]).toBe(-1)
  })

  it('METRICS_DIM 与 Python METRICS 列数一致（跨语言 SSOT）', () => {
    // 2026-09-30（刀 4）：奖励公式引擎是**纯逻辑** ⇒ 随整族从 `nn-training/rl/` 搬进 `biz/`。
    const pyPath = join(import.meta.dir, '..', 'nn-training', 'biz', 'reward_library.py')
    const py = readFileSync(pyPath, 'utf8')
    const block = py.match(/METRICS: tuple\[str, \.\.\.\] = \(([\s\S]*?)\n\)/)
    expect(block).not.toBeNull()
    const names = [...(block as RegExpMatchArray)[1].matchAll(/"([A-Za-z0-9_]+)"/g)].map(
      (m) => m[1],
    )
    // 列名唯一且与 TS 侧行宽同长
    expect(new Set(names).size).toBe(names.length)
    expect(names.length).toBe(METRICS_DIM)
    expect(names[30]).toBe('clearTick')
    expect(names[31]).toBe('killsBasic')
    expect(names[38]).toBe('hitsArmor')
    expect(names[39]).toBe('puGotOther')
    expect(names[40]).toBe('pickupDist')
    // metrics v8（plan/x20-dodge-avoidance §2）：危险暴露四列，永久追加在尾部
    expect(names[41]).toBe('playerHpRatio')
    expect(names[42]).toBe('dangerTicks')
    expect(names[43]).toBe('threatTicks')
    expect(names[44]).toBe('dmgFirst600')
    // metrics v9（plan/geo-threat-instrumentation §1.1/§1.3）：命中方位 5 列 + 穿越税 4 列，
    // 同样永久追加在尾部（0–44 列号不动）。**普通 front 不单列**（由总数减出）。
    expect(names[45]).toBe('backHits')
    expect(names[46]).toBe('sideHits')
    expect(names[47]).toBe('frontHitsExempt')
    expect(names[48]).toBe('farHits')
    expect(names[49]).toBe('geoFallback')
    expect(names[50]).toBe('onLaneTicks')
    expect(names[51]).toBe('onLaneExemptTicks')
    expect(names[52]).toBe('onLaneMoveTicks')
    expect(names[53]).toBe('onLaneHoldFireTicks')
    // metrics v10（plan/metrics-v10-gap-columns §1）：差距四族 15 列，同样永久追加在尾部。
    expect(names[54]).toBe('stopTicks')
    expect(names[55]).toBe('fireHeldTicks')
    expect(names[56]).toBe('idleTicks')
    expect(names[57]).toBe('enemyDist')
    expect(names[58]).toBe('nearEnemy4Ticks')
    expect(names[59]).toBe('damageBursts')
    expect(names[60]).toBe('maxDamage120')
    expect(names[61]).toBe('damageWhileLow')
    expect(names[62]).toBe('encl1Ticks')
    expect(names[63]).toBe('encl2Ticks')
    expect(names[64]).toBe('encl3pTicks')
    // v10 批次内整理（aim-dodge-levers）：enclExempt 三列移除，enclMax 顺位 68→65；
    // aim-dodge 8 列追加 66–73。
    expect(names[65]).toBe('enclMax')
    expect(names[66]).toBe('aimHits')
    expect(names[67]).toBe('aimHitDistSum')
    expect(names[68]).toBe('aimBricks')
    expect(names[69]).toBe('aimIgnited')
    expect(names[70]).toBe('aimMisses')
    expect(names[71]).toBe('hurtWeight')
    expect(names[72]).toBe('enclWeightTicks')
    expect(names[73]).toBe('cornerWeightTicks')
    // 列数变更必须 bump 版本（旧 shard 靠它响亮报错，不静默错读）
    expect(py).toContain('METRICS_VERSION = 10')
  })

  it('差距四族 15 列写入 idx54–68（metrics v10）', () => {
    const world = seedWorld(1)
    const row = buildMetricsRow(
      0,
      world,
      makeTelemetry({
        stopTicks: 1,
        fireHeldTicks: 2,
        idleTicks: 3,
        nearEnemy4Ticks: 5,
        damageWhileLow: 8,
        encl1Ticks: 9,
        encl2Ticks: 10,
        encl3pTicks: 11,
        enclMax: 3,
      }),
    )
    expect(row.length).toBe(METRICS_DIM)
    expect(row[54]).toBe(1) // stopTicks
    expect(row[55]).toBe(2) // fireHeldTicks
    expect(row[56]).toBe(3) // idleTicks
    // idx57 是**每行采样**：最近敌车距或哨兵 -1（不读 telegraph，独立重算）
    expect([-1, ...Array.from({ length: 600 }, (_, i) => i)]).toContain(row[57])
    expect(row[58]).toBe(5) // nearEnemy4Ticks
    // idx59/60 由 tel.damageTicks/damageAmounts 派生（此处空序列 ⇒ 全 0）
    expect(row[59]).toBe(0)
    expect(row[60]).toBe(0)
    expect(row[61]).toBe(8) // damageWhileLow
    // v10 批次内整理：enclExempt 三列移除；enclMax 顺位 68→65
    expect(row.slice(62, 66)).toEqual([9, 10, 11, 3])
  })

  it('aim-dodge 8 列写入 idx66–73（v10 批次内追加）', () => {
    const row = buildMetricsRow(
      0,
      seedWorld(1),
      makeTelemetry({
        aimHits: 1,
        aimHitDistSum: 2,
        aimBricks: 3,
        aimIgnited: 4,
        aimMisses: 5,
        hurtWeight: 6,
        enclWeightTicks: 7,
        cornerWeightTicks: 8,
      }),
    )
    expect(row.length).toBe(METRICS_DIM)
    expect(row.slice(66, 74)).toEqual([1, 2, 3, 4, 5, 6, 7, 8])
  })

  it('METRICS_COLUMN_NAMES 与 Python METRICS 逐位相等（跨语言列序 oracle，sb P2-D）', () => {
    const pyPath = join(import.meta.dir, '..', 'nn-training', 'biz', 'reward_library.py')
    const py = readFileSync(pyPath, 'utf8')
    const block = py.match(/METRICS: tuple\[str, \.\.\.\] = \(([\s\S]*?)\n\)/)
    expect(block).not.toBeNull()
    const names = [...(block as RegExpMatchArray)[1].matchAll(/"([A-Za-z0-9_]+)"/g)].map(
      (m) => m[1],
    )
    // 逐位（非集合）——顺序错位是最难察觉的静默回归。
    expect(names).toEqual([...METRICS_COLUMN_NAMES])
  })

  it('伤害成簇两列按事件序列重算（独立重实现对账）', () => {
    // 逐笔事件：t=0/60 相连（≤120 ⇒ burst 1）；t=50/300 与 -1 哨兵位无关。
    // 独立重实现：max120 = 任意起点 120t 窗内最大和。
    const world = seedWorld(1)
    const tel = makeTelemetry({ damageTicks: [10, 60, 200, 290], damageAmounts: [5, 7, 9, 11] })
    const row = buildMetricsRow(0, world, tel)
    // 连击：10→60 与 200→290 各 1 次（60→200 > 120）
    expect(row[59]).toBe(2)
    // 最大 120t 窗：10..60 与 200..290 各 12 / 20 ⇒ 20（不跨 60→200）
    expect(row[60]).toBe(20)
  })

  it('v10 批次内整理：enclExempt 三列已移除（列宽 69 → 74 的删/增）', () => {
    // 这三列是 v10 未签入批次的净价对，被决定 A（豁免拌入、零豁免列）取代；
    // 本断言防止有人「顺手」把它们加回来（加回 = 列序漂移 + Python 对账红）。
    const pyNames = METRICS_COLUMN_NAMES as readonly string[]
    expect(pyNames.includes('enclExempt1Ticks')).toBe(false)
    expect(pyNames.includes('enclExempt2Ticks')).toBe(false)
    expect(pyNames.includes('enclExempt3pTicks')).toBe(false)
  })

  it('命中方位 5 列 + 穿越税 4 列写入 idx45–53（metrics v9）', () => {
    const world = seedWorld(1)
    const row = buildMetricsRow(
      0,
      world,
      makeTelemetry({
        backHits: 1,
        sideHits: 2,
        frontHitsExempt: 3,
        farHits: 4,
        geoFallback: 5,
        onLaneTicks: 60,
        onLaneExemptTicks: 7,
        onLaneMoveTicks: 50,
        onLaneHoldFireTicks: 8,
      }),
    )
    expect(row.length).toBe(METRICS_DIM)
    expect(row.slice(45, 54)).toEqual([1, 2, 3, 4, 5, 60, 7, 50, 8])
  })

  it('豁免列是 raw 的子集（onLaneExemptTicks ≤ onLaneTicks 恒成立）', () => {
    // 交集恒包含性（plan §3）：豁免列是 raw 的加法列，**不是**从 raw 里扣出来的 ——
    // 两个累计量各自干净，公式侧才差得出净价。这条断言把「子集」关系钉死。
    const world = seedWorld(1)
    const row = buildMetricsRow(
      0,
      world,
      makeTelemetry({
        onLaneTicks: 10,
        onLaneExemptTicks: 4,
        onLaneMoveTicks: 9,
        onLaneHoldFireTicks: 1,
      }),
    )
    expect(row[51]).toBeLessThanOrEqual(row[50])
    expect(row[52] + row[53]).toBeLessThanOrEqual(row[50]) // 移动/架枪互斥且 ⊆ raw
  })

  it('危险暴露四列写入 idx41–44（metrics v8）', () => {
    const w = seedWorld(3)
    w.startGame('hard', 'modern', 0)
    w.player!.maxHp = 200
    w.player!.hp = 50 // hpRatio = 0.25
    const row = buildMetricsRow(
      0,
      w,
      makeTelemetry({ dangerTicks: 7, threatTicks: 9, dmgFirst600: 42 }),
    )
    expect(row.length).toBe(METRICS_DIM)
    // idx41 独立重算（不看生产 helper）：clamp01(hp/maxHp)
    const p = w.player!
    expect(row[41]).toBe(Math.max(0, Math.min(1, p.hp / p.maxHp)))
    expect(row[41]).toBe(0.25)
    expect(row[42]).toBe(7) // dangerTicks
    expect(row[43]).toBe(9) // threatTicks
    expect(row[44]).toBe(42) // dmgFirst600
  })

  it('无玩家时 idx41 = 0（哨兵语义），且不影响行宽', () => {
    const w = seedWorld(4) // 未 startGame ⇒ 无玩家
    const row = buildMetricsRow(0, w, makeTelemetry())
    expect(row.length).toBe(METRICS_DIM)
    expect(row[41]).toBe(0)
    expect(row[42]).toBe(0)
    expect(row[43]).toBe(0)
    expect(row[44]).toBe(0)
  })

  it('分敌种击杀/命中写入 idx31–38（metrics v6）', () => {
    const world = seedWorld(1)
    const row = buildMetricsRow(
      0,
      world,
      makeTelemetry({ killsByKind: [1, 2, 3, 4], hitsByKind: [5, 6, 7, 8] }),
    )
    expect(row.length).toBe(METRICS_DIM)
    expect(row.slice(31, 39)).toEqual([1, 2, 3, 4, 5, 6, 7, 8])
  })

  it('pickupDist 写入 idx40（metrics v7）：独立重算 vs buildMetricsRow 逐位一致', () => {
    // 独立重实现（不看生产 helper）：中心格曼哈顿，跳过非存活拾取。
    const indepPickupDist = (w: ReturnType<typeof seedWorld>): number => {
      const p = w.player
      if (!p || !p.alive) return PICKUP_DIST_SENTINEL
      const pc = { col: Math.floor((p.x + p.w / 2) / 16), row: Math.floor((p.y + p.h / 2) / 16) }
      let best = PICKUP_DIST_SENTINEL
      for (const pu of w.powerUps) {
        if (!pu.alive) continue
        const d =
          Math.abs(Math.floor((pu.x + pu.w / 2) / 16) - pc.col) +
          Math.abs(Math.floor((pu.y + pu.h / 2) / 16) - pc.row)
        if (best < 0 || d < best) best = d
      }
      return best
    }
    // 带玩家的 world（startGame 才会建玩家坦克）。
    const withPlayer = (seed: number): ReturnType<typeof seedWorld> => {
      const w = seedWorld(seed)
      w.startGame('hard', 'modern', 0)
      return w
    }

    // 多拾取局：取最近者（含炸弹型道具在场）
    const w1 = withPlayer(1)
    positionPlayer(w1, 8, 8)
    w1.powerUps.push(
      makePowerUp(12, 8, 'bomb'), // 距 4 格
      makePowerUp(8, 20, 'star'), // 距 12 格
      makePowerUp(5, 5, 'tank'), // 距 6 格
    )
    const r1 = buildMetricsRow(0, w1, makeTelemetry())
    expect(r1[40]).toBe(4)
    expect(r1[40]).toBe(indepPickupDist(w1))

    // 同格 = 0；非存活拾取被跳过
    const w2 = withPlayer(2)
    positionPlayer(w2, 5, 5)
    w2.powerUps.push(makePowerUp(5, 5, 'freeze'), makePowerUp(5, 9, 'shield', { alive: false }))
    const r2 = buildMetricsRow(0, w2, makeTelemetry())
    expect(r2[40]).toBe(0)
    expect(r2[40]).toBe(indepPickupDist(w2))

    // 无存活拾取 → 哨兵 -1
    const w3 = withPlayer(3)
    positionPlayer(w3, 3, 3)
    w3.powerUps.push(makePowerUp(3, 9, 'star', { alive: false }))
    const r3 = buildMetricsRow(0, w3, makeTelemetry())
    expect(r3[40]).toBe(PICKUP_DIST_SENTINEL)
    expect(r3[40]).toBe(indepPickupDist(w3))

    // 玩家不在场（阵亡）→ 哨兵 -1
    const w4 = withPlayer(4)
    positionPlayer(w4, 2, 2)
    w4.player!.alive = false
    w4.powerUps.push(makePowerUp(2, 2, 'star'))
    const r4 = buildMetricsRow(0, w4, makeTelemetry())
    expect(r4[40]).toBe(PICKUP_DIST_SENTINEL)
    expect(r4[40]).toBe(indepPickupDist(w4))
  })

  it('puGotOther 写入 idx39（metrics v7）：四桶外拾取的残差记账', () => {
    const world = seedWorld(1)
    const row = buildMetricsRow(
      0,
      world,
      makeTelemetry({ puGotBomb: 1, puGotTank: 2, puGotOther: 3, starsCollected: 4 }),
    )
    expect(row.length).toBe(METRICS_DIM)
    expect(row[25]).toBe(1) // puGotBomb
    expect(row[26]).toBe(2) // puGotTank
    expect(row[39]).toBe(3) // puGotOther
    expect(row[10]).toBe(4) // starsCollected
  })
})

describe('applySuffixPatches：回写补丁（plan §3.4/§7.1）', () => {
  it('4 行 × 2 列玩具：开火 i=1、结算 i=2 ⇒ Δr[1]=+1、其余 0', () => {
    // 区间补丁 [1,2) = 结算时已推出去的「开火行之后」的唯一一行；结算行本身
    // （i=2）与之后的行由 buildMetricsRow 读 tel 自然携带 ⇒ 打包时只补历史行。
    const rows = [
      [0, 0],
      [0, 0],
      [0, 0],
      [0, 0],
    ]
    applySuffixPatches(rows, [{ col: 0, from: 1, to: 2, delta: 1 }])
    expect(rows).toEqual([
      [0, 0],
      [1, 0],
      [0, 0],
      [0, 0],
    ])
  })

  it('结算晚于终局行边界：to > rows.length ⇒ clamp，不抛不越界', () => {
    const rows = [[0], [0], [0]]
    applySuffixPatches(rows, [{ col: 0, from: 1, to: 6, delta: 2 }])
    expect(rows).toEqual([[0], [2], [2]])
  })

  it('同 tick 开火+命中：from == to ⇒ 无已推行可补（no-op），增量全由后续行承担', () => {
    const rows = [[0], [0], [5]] // 第 2 行已含 tel 的自然增量
    applySuffixPatches(rows, [{ col: 0, from: 2, to: 2, delta: 5 }])
    expect(rows).toEqual([[0], [0], [5]])
  })

  it('多补丁异列叠加：互不串列；空补丁 = no-op', () => {
    const rows = [
      [0, 0],
      [0, 0],
    ]
    applySuffixPatches(rows, [
      { col: 0, from: 0, to: 1, delta: 1 }, // hit 列补在开火行
      { col: 1, from: 1, to: 2, delta: 2 }, // miss 列补在另一区间
      { col: 0, from: 1, to: 1, delta: 9 }, // 空区间
    ])
    expect(rows).toEqual([
      [1, 0],
      [0, 2],
    ])
  })
})

describe('resolveLivesFlag：命数无默认值（2026-09-19 根因修复）', () => {
  it('显式值直通', () => {
    expect(resolveLivesFlag('1')).toBe(1)
    expect(resolveLivesFlag('3')).toBe(3)
  })

  it('缺席/非法响亮失败（禁静默回落难度默认 3 命）', () => {
    expect(() => resolveLivesFlag('')).toThrow(/lives-override/)
    expect(() => resolveLivesFlag('0')).toThrow(/lives-override/)
    expect(() => resolveLivesFlag('-2')).toThrow(/lives-override/)
    expect(() => resolveLivesFlag('abc')).toThrow(/lives-override/)
  })
})
