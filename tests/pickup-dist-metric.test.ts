import { describe, expect, it } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import type { Direction } from '../src/constants'
import { CELL, GRID } from '../src/constants'
import {
  buildMetricsRow,
  METRICS_DIM,
  PICKUP_DIST_SENTINEL,
  type Telemetry,
} from '../tools/sim/export-rl-rollout'
import { createTestWorld, clearArena, positionPlayer, makePowerUp } from './helpers'
import { allEnemiesCleared } from '../src/game/SimulationEffects'

/**
 * pickup-dist-metric.test.ts —— plan/pickup-shaping.plan.md Phase 0 的 metric 等价性测试。
 *
 * 「codec 独立重实现」体例（仿 tests/stages.test.ts / tests/sim/phase0-census.test.ts）：
 * 不从生产侧 import `nearestPickupDist`，而是用本文件局部重写一份独立实现
 * （与生产实现不共享代码，抓得住口径漂移），并断言「buildMetricsRow 写进 shard
 * 的第 40 列 == 独立重算」**每步同刻**逐位一致。
 *
 * 口径（metrics v16）：**BFS 路径距离**（2×2 车身能覆盖的格 = 可走；brick/steel/water/base
 * 阻车），不是曼哈顿直距 —— 墙另一侧的道具不能被读成「就在隔壁」。
 *
 * 边界局必须进用例（残差桶教训，x5⑪）：
 *   ① 多拾取局 —— 多拾取铺场，最近者随玩家移动切换（nearest 语义）；
 *   ② 炸弹局 —— bomb 型拾取在场且被玩家拾取（距离 0 → 收集跳变 → 次近/哨兵；
 *      收集跳变是势能法的真实行为，§5 趋近熔断盯的就是这类形态，metric 必须忠实）；
 *   ③ BONUS 窗口局 —— 敌人全灭（allEnemiesCleared=true）但场上还有存活拾取，
 *      玩家在窗口期继续朝道具走，距离列必须照常输出（战前/窗口不分账的前提是列可用）。
 * 外加 determinism：同 seed 双跑，整行（含 idx40）逐位相同。
 */

// ---- 独立重实现（刻意不 import 生产 helper）----

/** 阻车地形（本文件自持一份字面表，不从生产侧 import 谓词）。 */
function indepBlocksTank(t: string): boolean {
  return t === 'brick' || t === 'steel' || t === 'water' || t === 'base'
}

/** 2×2 车身能否覆盖格 (col,row)：四个候选锚点里有一个整块可走即可。 */
function indepCoverable(w: World, col: number, row: number): boolean {
  for (let dr = 0; dr >= -1; dr--) {
    for (let dc = 0; dc >= -1; dc--) {
      const ac = col + dc
      const ar = row + dr
      if (ac < 0 || ac + 1 >= GRID || ar < 0 || ar + 1 >= GRID) continue
      let ok = true
      for (let r = ar; r <= ar + 1 && ok; r++) {
        for (let c = ac; c <= ac + 1; c++) {
          if (indepBlocksTank(w.tileMap.grid[r][c])) {
            ok = false
            break
          }
        }
      }
      if (ok) return true
    }
  }
  return false
}

/** BFS 路径距离（格步）；玩家格为起点（即使车身此刻覆盖不全，也从脚下出发）。 */
function indepPickupPathDist(w: World): number {
  const p = w.player
  if (!p || !p.alive) return PICKUP_DIST_SENTINEL
  const pcol = Math.floor((p.x + p.w / 2) / CELL)
  const prow = Math.floor((p.y + p.h / 2) / CELL)
  const dist = new Int16Array(GRID * GRID).fill(-1)
  const start = prow * GRID + pcol
  dist[start] = 0
  const queue: number[] = [start]
  const stepCol = [-1, 1, 0, 0]
  const stepRow = [0, 0, -1, 1]
  for (let head = 0; head < queue.length; head++) {
    const idx = queue[head]
    const col = idx % GRID
    const row = (idx / GRID) | 0
    for (let s = 0; s < 4; s++) {
      const nc = col + stepCol[s]
      const nr = row + stepRow[s]
      if (nc < 0 || nc >= GRID || nr < 0 || nr >= GRID) continue
      const n = nr * GRID + nc
      if (dist[n] !== -1) continue
      if (!indepCoverable(w, nc, nr)) continue
      dist[n] = dist[idx] + 1
      queue.push(n)
    }
  }
  let best = PICKUP_DIST_SENTINEL
  for (const pu of w.powerUps) {
    if (!pu.alive) continue
    const col = Math.floor((pu.x + pu.w / 2) / CELL)
    const row = Math.floor((pu.y + pu.h / 2) / CELL)
    const d = dist[row * GRID + col]
    if (d >= 0 && (best < 0 || d < best)) best = d
  }
  return best
}

function mkTel(): Telemetry {
  return {
    enemyTotal: 0,
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
    // metrics v11（idx74–75；plan/metrics-v11-hotlane.plan.md）。
    nearSqSum: 0,
    postHitLaneTicks: 0,
    freezeShieldWindowTicks: 0,
    hitsInFreezeShieldWindow: 0,
    deathDerivedShieldTicks: 0,
    stuckTicksCum: 0,
    wallPressTicks: 0,
    cooldownRejectTicks: 0,
    thShotsAtBaseCum: 0,
    thCostBreach: 0,
    thCostBreachAimed: 0,
    thCostDirect: 0,
    thCostDirectAimed: 0,
  }
}

/** 极简输入桩（InputLike 最小实现）：恒定朝一个方向走（null = 原地不动）、不开火。 */
class StubInput {
  dir: Direction | null = 'right'
  getMoveDirection(): Direction | null {
    return this.dir
  }
  isFiring(): boolean {
    return false
  }
  wasItemPressed(): false {
    return false
  }
  endFrame(): void {}
  reset(): void {}
}

/** 清空敌人（队列 + 场上 + remaining），构造可控空场 —— 只有玩家和手摆拾取。 */
function neutralizeEnemies(w: World): void {
  w.spawnQueue = []
  w.enemiesRemaining = 0
  w.tanks = w.tanks.filter((t) => t.isPlayer)
}

/** 空场 world：hard 开局 → clearArena → 敌人清空。 */
function openWorld(seed: number): World {
  const w = createTestWorld({ rngSeed: seed })
  w.startGame('hard', 'modern', 0)
  clearArena(w)
  neutralizeEnemies(w)
  return w
}

/** 迷你采样循环（镜像 export-rl-rollout 的决策节拍：每 K=10 tick 采一行 + 终局一行）。
 * 同步捕获「同一世界状态」下的独立重算值 —— 供逐行对账。 */
function sampleLoop(
  world: World,
  maxTicks: number,
  dir: Direction | null = 'right',
): { rows: number[][]; indep: number[] } {
  const stub = new StubInput()
  stub.dir = dir
  const sim = new Simulation(world, stub as never)
  const rows: number[][] = []
  const indep: number[] = []
  let t = 0
  while (t < maxTicks && world.state === 'playing') {
    if (t % 10 === 0) {
      rows.push(buildMetricsRow(t, world, mkTel()))
      indep.push(indepPickupPathDist(world))
    }
    sim.tick()
    t++
  }
  rows.push(buildMetricsRow(t, world, mkTel())) // 终局行
  indep.push(indepPickupPathDist(world))
  return { rows, indep }
}

/** 逐行断言：shard 列（idx40）== 同刻独立重算（口径对账核心断言）。 */
function expectColumnMatchesIndep(rows: number[][], indep: number[]): void {
  expect(rows.length).toBe(indep.length)
  for (let i = 0; i < rows.length; i++) {
    expect(rows[i][40]).toBe(indep[i])
  }
}

describe('pickupDist：独立重实现 vs shard 列（每步同刻逐位一致）', () => {
  it('多拾取局：最近者随玩家移动切换，列值 == 独立重算', () => {
    const w = openWorld(1)
    positionPlayer(w, 8, 8, 'right')
    w.powerUps.push(makePowerUp(13, 8, 'star'), makePowerUp(8, 16, 'bomb'))
    const { rows, indep } = sampleLoop(w, 160)
    expect(rows.length).toBeGreaterThan(3)
    expectColumnMatchesIndep(rows, indep)
    // 初始最近 = star（距 5）；bomb 距 8 ⇒ 取小者
    expect(rows[0][40]).toBe(5)
  })

  it('炸弹局：bomb 拾取在场（不收集），距离列忠实指向 bomb（较远拾取不干扰最近者）', () => {
    const w = openWorld(2)
    // 炸弹型道具在场 = 炸弹局形态；玩家不动，bomb 距 4 格、shield 距 8 格。
    positionPlayer(w, 4, 8, 'right')
    w.powerUps.push(makePowerUp(8, 8, 'bomb'), makePowerUp(12, 8, 'shield'))
    const { rows, indep } = sampleLoop(w, 60, null) // 玩家原地不动 ⇒ bomb 全程不收集
    expect(rows.length).toBeGreaterThan(3)
    expectColumnMatchesIndep(rows, indep)
    // 全程最近者 = bomb（距 4），不因较远拾取移动而变
    for (const row of rows) expect(row[40]).toBe(4)
    // 收尾 sanity：run 结束前 bomb 未被收集（最近者仍是 4 → 场上有存活拾取）
    expect(allEnemiesCleared(w)).toBe(true)
  })

  it('BONUS 窗口局：敌人全灭（allEnemiesCleared）但拾取仍在，距离列照常输出', () => {
    const w = openWorld(3)
    positionPlayer(w, 6, 6, 'right')
    w.powerUps.push(makePowerUp(10, 6, 'freeze'), makePowerUp(6, 12, 'tank'))
    expect(allEnemiesCleared(w)).toBe(true) // 预置 = 全灭态（BONUS 窗口前提）
    const { rows, indep } = sampleLoop(w, 120)
    expect(rows.length).toBeGreaterThan(3)
    expectColumnMatchesIndep(rows, indep)
    expect(rows[0][40]).toBe(4) // freeze 距 4，tank 距 6
  })

  it('墙后道具：曼哈顿 4 格、路径 28 格（绕行），列值 == 路径距离', () => {
    const w = openWorld(4)
    positionPlayer(w, 8, 8, 'right') // 中心格 (9,9)
    w.powerUps.push(makePowerUp(8, 12, 'star')) // 中心格 (9,13)：直距 4，隔着一堵墙
    // 横墙 rows 10–11 / cols 0–20，右侧 cols 21–25 留 2 格宽通道（2×2 车身可过）。
    for (let r = 10; r <= 11; r++) for (let c = 0; c <= 20; c++) w.tileMap.grid[r][c] = 'brick'
    const { rows, indep } = sampleLoop(w, 40, null)
    expect(rows[0][40]).toBeGreaterThan(20)
    expectColumnMatchesIndep(rows, indep)
  })

  it('不可达拾取（封闭盒）：不参与最近者；全不可达 ⇒ 哨兵', () => {
    const w = openWorld(5)
    positionPlayer(w, 6, 6, 'right') // 中心格 (7,7)
    // 砖砌封闭盒 rows 16–21 × cols 16–21，盒中心 (18,18) 放一个道具（车身进不去）。
    for (let c = 16; c <= 21; c++) {
      w.tileMap.grid[16][c] = 'brick'
      w.tileMap.grid[21][c] = 'brick'
    }
    for (let r = 16; r <= 21; r++) {
      w.tileMap.grid[r][16] = 'brick'
      w.tileMap.grid[r][21] = 'brick'
    }
    const boxed = makePowerUp(18, 18, 'star')
    w.powerUps.push(boxed, makePowerUp(8, 6, 'freeze')) // freeze 中心格 (9,7)：可达，距 2
    const a = sampleLoop(w, 40, null)
    expect(a.rows[0][40]).toBe(2)
    expectColumnMatchesIndep(a.rows, a.indep)

    // 只剩盒里的那个 ⇒ 全部行都是哨兵（不是「贴着盒子 2 格」）
    boxed.alive = true
    const onlyBoxed = openWorld(6)
    positionPlayer(onlyBoxed, 6, 6, 'right')
    for (let c = 16; c <= 21; c++) {
      onlyBoxed.tileMap.grid[16][c] = 'brick'
      onlyBoxed.tileMap.grid[21][c] = 'brick'
    }
    for (let r = 16; r <= 21; r++) {
      onlyBoxed.tileMap.grid[r][16] = 'brick'
      onlyBoxed.tileMap.grid[r][21] = 'brick'
    }
    onlyBoxed.powerUps.push(makePowerUp(18, 18, 'star'))
    const b = sampleLoop(onlyBoxed, 40, null)
    for (const row of b.rows) expect(row[40]).toBe(PICKUP_DIST_SENTINEL)
    expectColumnMatchesIndep(b.rows, b.indep)
  })

  it('determinism：同 seed 双跑，整行 metrics（含 idx40）逐位相同', () => {
    const a = sampleLoop(openWorld(7), 160)
    const b = sampleLoop(openWorld(7), 160)
    expect(a.rows.length).toBe(b.rows.length)
    for (let i = 0; i < a.rows.length; i++) {
      expect(a.rows[i].length).toBe(METRICS_DIM)
      expect(a.rows[i][40]).toBe(b.rows[i][40])
    }
  })
})
