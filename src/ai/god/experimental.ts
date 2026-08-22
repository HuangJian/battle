// ============================================================
// experimental.ts — 参数退役归档区（M0.5，plan/God-AI-Redesign-v2 §4.4）
//
// 2026-08-03: 22 个僵尸/否决参数从 GodAIParams interface 移除（评审决议 2：
// "移入收纳区，但 interface 必须移除"——编译器强制清理所有引用）。
// 本文件是这些参数的"收纳区"：
//   - 参数规格（默认值 + 最终 A/B 结论）保留在下方表中；
//   - 独立函数体（威胁评分 / 陷阱回避 / A* 威胁代价 / 路径威胁投影）
//     原样保留，供未来 M3 survive 候选 / EnemyModel 特征复用（设计 §4.4
//     "整合"条款）；
//   - 内联块（think() 的 §68-v2 crossfire、guardBand fastThreat、dodge
//     hysteresis/persistence、canMoveDirFloorSnap、damagedArmorBonus）
//     以注释形式记录出处与结论。
//
// 注意 1: 本文件不被任何生产代码 import（一旦 import，`declare module`
// 增强会把退役字段加回 GodAIParams——这正是我们要避免的）。它仅作为可
// 编译的历史档案存在。重新实验某个族时，把对应代码搬回生产文件 + 把字段
// 加回 interface，然后走 60-seed A/B 纪律。
//
// 注意 2 (2026-08-03 M0.5a): 早期版本用 `declare module './params'` 增强
// 让归档函数通过 `self: GodAIInput` 读取退役字段——但 TS 模块增强是**程序
// 全局生效**的（不限于导入者），导致 GodAIParams 全局多出 optional 字段，
// 破坏了 optimize-godai 的 `params[s.name]`（keyof 索引返回 number|undefined）。
// 现改用结构化 `ArchivedSelf` 接口——归档函数只依赖其真正使用的 GodAIInput
// 表面，与生产 interface 完全解耦。
// ============================================================

import type { Tank, TankKind, Bullet } from '../../types'
import type { World } from '../../game/World'
import type { Cell } from './pathfind'
import { BASE_POS, DIR_VECTORS, GRID, CELL, TANK, FIELD, type Direction } from '../../constants'
import { TileMap } from '../../game/TileMap'
import { BULLET_TRAJECTORY_MAX_CELLS } from './constants'
import { ALL_DIRS } from '../../utils/helpers'

/**
 * 已退役参数规格表（2026-08-03 移除）。
 *
 * | 族 | 参数 | 默认 | 最终结论 |
 * |---|---|---|---|
 * | smartThreatModel | 7 项 | 0 | Phase A 否决（God-AI-Next-Round §3）——新增威胁评分的复杂度未被接受；现有 defenseClearShotBonus（§59）覆盖其用途 |
 * | crossfire | 3 项 | 0 | §68/§69 双否决——v1 中性、v2 时间感知投影在 maze 关回退；路径威胁基础设施移入本文件供 survive 候选复用 |
 * | guardBand + damagedArmor | 4 项 | 0 | D1/D2 否决——base-centric 选位比基线差；damagedArmorBonus 实测 -8.4pp (S32) |
 * | §86 未发布 | dodgeHysteresis/dodgeDirPersistence/canMoveDirFloorSnap | 0 | "never in shipped default"——A/B 分别 -1.1pp / -1.7pp / -2.6pp；§86c 转弯冷却为规范修复，dodgeOscillationCounterFire 为唯一发布项 |
 * | §63/§65 回退 | openT2a1HpMaxRange / armorMazeSuboptimalPathProb | 0 | 60-seed 验证净负（-0.6pp / -1.7pp），已回退 |
 * | trapAvoidance | 3 项 | 0 | 默认 0 未发布；"包围风险"输入并入设计 §3.2 的 survive 候选 |
 */
export interface ExperimentalGodAIParams {
  // smartThreatModel 族
  smartThreatModel: number
  smartThreatThreshold: number
  smartThreatSpeedWeight: number
  smartThreatFacingWeight: number
  smartThreatHpWeight: number
  smartThreatDistRange: number
  smartRushDetectBonus: number
  // crossfire 族
  crossfireAwareness: number
  crossfireOpenObstacleRatio: number
  crossfirePathCost: number
  // guardBand + damagedArmor
  guardBandMode: number
  guardBandRow: number
  guardBandHalfWidth: number
  damagedArmorBonus: number
  // §86 未发布
  dodgeHysteresis: number
  dodgeDirPersistence: number
  canMoveDirFloorSnap: number
  // §63/§65 回退
  openT2a1HpMaxRange: number
  armorMazeSuboptimalPathProb: number
  // trapAvoidance
  trapAvoidance: number
  trapEnemyRadiusCells: number
  trapEnemyCount: number
}

/**
 * 归档函数所需的最小 GodAIInput 表面（结构化类型，M0.5a）。
 *
 * 退役字段通过 `params: ExperimentalGodAIParams` 提供；GodAIInput 的方法
 * 表面（playerCell / tankCell / canMoveDir / controlledTank / _enemies）是
 * 仍然存在的生产 API。归档函数因此可独立编译、可被 v2 survive 候选原样
 * 复用，且不与生产 GodAIParams 发生任何耦合。
 */
interface ArchivedSelf {
  world: World
  params: ExperimentalGodAIParams
  hasBase: boolean
  _enemies: Tank[]
  controlledTank: (w: World) => Tank | null
  playerCell(): Cell
  tankCell(t: Tank): Cell
  canMoveDir(tank: Tank, dir: Direction): boolean
}

// ============================================================
// 归档函数 8 — M3 round-2 dodge pinned（isDodgePinnedImpl，timing-aware）
// 出处: src/ai/god/ThreatAssessor.ts（2026-08-03 移入归档，DECISIONS §101）
//
// M3 counter-fire 三轮门控官方口径全部阴性：distance（§98 -0.5pp）、
// timing-aware（本轮，-0.2pp 级）、terrain-only（接线版 isTerrainPinnedImpl
// 留在 ThreatAssessor，官方口径 chaos -0.2pp）。timing-aware 版本在开阔关把
// "部分闪避的持续移动"换成站定对枪 → 交叉火力关送死，故移入归档；
// think.ts 接线最保守的 terrain-only 版本（默认 0，不执行）。
// ============================================================

/**
 * Archived (DECISIONS §101): timing-aware pinning — NO perpendicular
 * direction can both (a) be moved into AND (b) clear the bullet's hit band
 * before arrival. Official-口径 A/B 阴性（chaos -0.2pp 级），归档供 M3.5
 * 复用。Offset-aware（已离线只需少量横移则仍可闪避）。
 */
export function isDodgePinnedImpl(
  self: ArchivedSelf,
  p: Tank,
  bullet: Bullet,
  pcx: number,
  pcy: number,
): boolean {
  const vertical = bullet.dir === 'up' || bullet.dir === 'down'
  const candA: Direction = vertical ? 'left' : 'up'
  const candB: Direction = vertical ? 'right' : 'down'
  // Lateral offset of the player center from the bullet's travel line.
  const bcx = bullet.x + bullet.w / 2
  const bcy = bullet.y + bullet.h / 2
  const offP = vertical ? Math.abs(pcx - bcx) : Math.abs(pcy - bcy)
  // px the player center must still move laterally to clear the bullet's
  // hitbox (tank half + bullet half along the perpendicular axis).
  const halfT = (vertical ? p.w : p.h) / 2
  const halfB = (vertical ? bullet.w : bullet.h) / 2
  const neededPx = halfT + halfB - offP
  if (neededPx <= 0) return false // already clear laterally — not a threat
  // Ticks to complete the lateral dodge (player moves at p.speed px/tick).
  const ticksToClear = p.speed > 0 ? neededPx / p.speed : Infinity
  // Ticks until the bullet crosses the player's line.
  const distAlong = vertical ? Math.abs(bcy - pcy) : Math.abs(bcx - pcx)
  const ticksToArrival = bullet.speed > 0 ? distAlong / bullet.speed : Infinity
  // A dodge that starts now survives only if it clears before arrival AND
  // the target direction is passable. (Bullet coverage is deliberately NOT
  // considered — see the archived round-3 doc: crossfire keeps moving.)
  let safeA = false
  let safeB = false
  if (ticksToClear < ticksToArrival && self.canMoveDir(p, candA)) safeA = true
  if (ticksToClear < ticksToArrival && self.canMoveDir(p, candB)) safeB = true
  return !(safeA || safeB)
}

// ============================================================
// 归档函数 1 — smartThreatModel（threatScoreImpl / smartIsBaseUnderThreatImpl）
// 出处: src/ai/god/SmartThreatModel.ts（2026-08-03 移除）
// ============================================================

function kindSpeedFactor(kind: TankKind): number {
  switch (kind) {
    case 'fast':
      return 1.0
    case 'power':
      return 0.7
    case 'basic':
      return 0.5
    case 'armor':
      return 0.35
    default:
      return 0
  }
}

/** Archived: type/speed/facing/HP-aware base threat score (Phase A). */
export function threatScoreImpl(self: ArchivedSelf, t: Tank): number {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const tc = self.tankCell(t)
  const distToBase = Math.abs(tc.col - bc) + Math.abs(tc.row - br)
  const speedFactor = kindSpeedFactor(t.kind)
  const maxTime = self.params.smartThreatDistRange
  const timeToBase = speedFactor > 0 ? distToBase / speedFactor : maxTime
  const timeScore = Math.max(0, 1 - timeToBase / maxTime)
  const toBaseDx = bc - tc.col
  const toBaseDy = br - tc.row
  const toBaseLen = Math.sqrt(toBaseDx * toBaseDx + toBaseDy * toBaseDy) || 1
  const fv = DIR_VECTORS[t.dir]
  const dot = (fv.dx * toBaseDx + fv.dy * toBaseDy) / toBaseLen
  const facingScore = (dot + 1) / 2
  const hpScore = t.hp / (t.maxHp || 1)
  return (
    self.params.smartThreatSpeedWeight * timeScore +
    self.params.smartThreatFacingWeight * facingScore +
    self.params.smartThreatHpWeight * hpScore
  )
}

/** Archived: smart isBaseUnderThreat (Phase A). */
export function smartIsBaseUnderThreatImpl(self: ArchivedSelf): boolean {
  if (!self.hasBase) return false
  const threshold = self.params.smartThreatThreshold
  const list = self._enemies.length > 0 ? self._enemies : self.world.tanks
  for (const t of list) {
    if (!t.alive || t.spawnTimer > 0) continue
    if (threatScoreImpl(self, t) >= threshold) return true
  }
  return false
}

// ============================================================
// 归档函数 2 — trapAvoidance（§48-revisit，Navigator.ts 移除）
// ============================================================

/** Archived: passable terrain exits of a grid cell (0-4). */
function countPassableExits(self: ArchivedSelf, col: number, row: number): number {
  const grid = self.world.tileMap.grid
  let n = 0
  for (let di = 0; di < ALL_DIRS.length; di++) {
    const v = DIR_VECTORS[ALL_DIRS[di]]
    const c = col + v.dx
    const r = row + v.dy
    if (c < 0 || c >= GRID || r < 0 || r >= GRID) continue
    if (!TileMap.blocksTank(grid[r][c])) n++
  }
  return n
}

/** Archived: don't walk into surround positions (user idea 2, §48-revisit). */
export function trapAvoidanceImpl(self: ArchivedSelf, p: Tank, moveDir: Direction): Direction {
  const pc = self.playerCell()
  const v = DIR_VECTORS[moveDir]
  const nx = pc.col + v.dx
  const ny = pc.row + v.dy
  if (nx < 0 || nx >= GRID || ny < 0 || ny >= GRID) return moveDir
  const exits = countPassableExits(self, nx, ny)
  if (exits > 2) return moveDir
  const radius = self.params.trapEnemyRadiusCells
  const need = self.params.trapEnemyCount
  let nearby = 0
  const enemies = self._enemies
  for (let i = 0; i < enemies.length; i++) {
    const ec = self.tankCell(enemies[i])
    const d = Math.abs(ec.col - nx) + Math.abs(ec.row - ny)
    if (d <= radius) {
      if (++nearby >= need) break
    }
  }
  if (nearby < need) return moveDir
  const baseCol = BASE_POS.col + 1
  const baseRow = BASE_POS.row + 1
  let bestDir: Direction | null = null
  let bestExits = exits
  let bestBaseDist = Infinity
  for (let di = 0; di < ALL_DIRS.length; di++) {
    const d = ALL_DIRS[di]
    if (d === moveDir) continue
    if (!self.canMoveDir(p, d)) continue
    const dv = DIR_VECTORS[d]
    const cx = pc.col + dv.dx
    const cy = pc.row + dv.dy
    const dExits = countPassableExits(self, cx, cy)
    const baseDist = Math.abs(cx - baseCol) + Math.abs(cy - baseRow)
    if (dExits > bestExits || (dExits === bestExits && baseDist < bestBaseDist)) {
      bestDir = d
      bestExits = dExits
      bestBaseDist = baseDist
    }
  }
  return bestDir ?? moveDir
}

// ============================================================
// 归档函数 3 — crossfire 族（§68-v2 / §69-B）
//   findPathThreatImpl + findSafeMoveDirImpl（think() §68-v2 块）
//   computeThreatCostsImpl（Navigator §69-B）
//   设计 §3.2：survive 候选 / M2+ risk 分将复用此基础设施。
// ============================================================

const PATH_THREAT_LOOKAHEAD = 3

/** Archived: time-aware path threat projection (§68-v2). */
export function findPathThreatImpl(
  self: ArchivedSelf,
  pcx: number,
  pcy: number,
  moveDir: Direction,
  playerSpeed: number,
): Bullet | null {
  const w = self.world
  const v = DIR_VECTORS[moveDir]
  const ps = playerSpeed > 0.1 ? playerSpeed : 1.0
  let bestBullet: Bullet | null = null
  let bestThreatTick = Infinity
  const bullets = w.bullets
  for (let i = 1; i <= PATH_THREAT_LOOKAHEAD; i++) {
    const ccx = pcx + v.dx * i * CELL
    const ccy = pcy + v.dy * i * CELL
    const playerArrivalTick = (i * CELL) / ps
    const threatWindow = 10
    const playerDepartureTick = playerArrivalTick + threatWindow
    const playerEnterTick = playerArrivalTick - threatWindow
    for (let bi = 0; bi < bullets.length; bi++) {
      const b = bullets[bi]
      if (!b.alive || b.isPlayer) continue
      const bcx = b.x + b.w / 2
      const bcy = b.y + b.h / 2
      const vertical = b.dir === 'up' || b.dir === 'down'
      const aligned = vertical ? Math.abs(bcx - ccx) < TANK : Math.abs(bcy - ccy) < TANK
      if (!aligned) continue
      const approaching =
        (b.dir === 'down' && bcy < ccy) ||
        (b.dir === 'up' && bcy > ccy) ||
        (b.dir === 'right' && bcx < ccx) ||
        (b.dir === 'left' && bcx > ccx)
      if (!approaching) continue
      const dist = vertical ? Math.abs(bcy - ccy) : Math.abs(bcx - ccx)
      const bulletArrivalTick = dist / b.speed
      if (bulletArrivalTick >= playerEnterTick && bulletArrivalTick <= playerDepartureTick) {
        if (bulletArrivalTick < bestThreatTick) {
          bestThreatTick = bulletArrivalTick
          bestBullet = b
        }
      }
    }
  }
  return bestBullet
}

/** Archived: safe alternative move direction when the path is threatened (§68-v2). */
export function findSafeMoveDirImpl(
  self: ArchivedSelf,
  pcx: number,
  pcy: number,
  threatenedDir: Direction,
  playerSpeed: number,
): Direction | null {
  const p = self.controlledTank(self.world)
  const w = self.world
  const ps = playerSpeed > 0.1 ? playerSpeed : 1.0
  const arrivalTick = CELL / ps
  const threatWin = 10
  const departTick = arrivalTick + threatWin
  const enterTick = arrivalTick - threatWin
  function isCell1Safe(dir: Direction): boolean {
    const v = DIR_VECTORS[dir]
    const ccx = pcx + v.dx * CELL
    const ccy = pcy + v.dy * CELL
    const bullets = w.bullets
    for (let bi = 0; bi < bullets.length; bi++) {
      const b = bullets[bi]
      if (!b.alive || b.isPlayer) continue
      const bcx = b.x + b.w / 2
      const bcy = b.y + b.h / 2
      const vertical = b.dir === 'up' || b.dir === 'down'
      const aligned = vertical ? Math.abs(bcx - ccx) < TANK : Math.abs(bcy - ccy) < TANK
      if (!aligned) continue
      const approaching =
        (b.dir === 'down' && bcy < ccy) ||
        (b.dir === 'up' && bcy > ccy) ||
        (b.dir === 'right' && bcx < ccx) ||
        (b.dir === 'left' && bcx > ccx)
      if (!approaching) continue
      const dist = vertical ? Math.abs(bcy - ccy) : Math.abs(bcx - ccx)
      const bat = dist / b.speed
      if (bat >= enterTick && bat <= departTick) return false
    }
    return true
  }
  const threatenedVertical = threatenedDir === 'up' || threatenedDir === 'down'
  const perpA: Direction = threatenedVertical ? 'left' : 'up'
  const perpB: Direction = threatenedVertical ? 'right' : 'down'
  const back = threatenedVertical
    ? threatenedDir === 'up'
      ? 'down'
      : 'up'
    : threatenedDir === 'left'
      ? 'right'
      : 'left'
  if (p && self.canMoveDir(p, perpA) && isCell1Safe(perpA)) return perpA
  if (p && self.canMoveDir(p, perpB) && isCell1Safe(perpB)) return perpB
  if (p && self.canMoveDir(p, back) && isCell1Safe(back)) return back
  return null
}

/** Archived: per-cell A* threat costs (§69-B). */
export function computeThreatCostsImpl(
  self: ArchivedSelf,
  fromCell: { col: number; row: number },
  playerSpeed: number,
): Float64Array | undefined {
  if (self.params.crossfirePathCost <= 0) return undefined
  const w = self.world
  const ps = playerSpeed > 0.1 ? playerSpeed : 1.0
  const penalty = self.params.crossfirePathCost
  const threatWin = 10
  const buf = new Float64Array(GRID * GRID)
  const bullets = w.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.isPlayer) continue
    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    const v = DIR_VECTORS[b.dir]
    for (let d = 0; d <= BULLET_TRAJECTORY_MAX_CELLS * CELL; d += CELL) {
      const fx = bcx + v.dx * d
      const fy = bcy + v.dy * d
      if (fx < 0 || fx > FIELD || fy < 0 || fy > FIELD) break
      const col = Math.floor(fx / CELL)
      const row = Math.floor(fy / CELL)
      const terrain = w.tileMap.get(col, row)
      if (terrain === 'brick' || terrain === 'steel') break
      const bulletArrivalTick = d / b.speed
      const playerArrivalTick =
        ((Math.abs(col - fromCell.col) + Math.abs(row - fromCell.row)) * CELL) / ps
      if (
        bulletArrivalTick >= playerArrivalTick - threatWin &&
        bulletArrivalTick <= playerArrivalTick + threatWin
      ) {
        const idx = row * GRID + col
        if (buf[idx] < penalty) buf[idx] = penalty
      }
    }
  }
  return buf
}

// ============================================================
// 归档记录 — 内联块（不在本文件保留代码体，仅记录出处与结论）:
//
// 1. think.ts guardBand fastThreat（D1）:
//    `const fastThreat = guardBandMode > 0 && hasFastThreatNearBase()`
//    → skipT2aForDefense 的 fastThreat 分支。否决（base-centric 选位更差）。
// 2. think.ts §68-v2 crossfire 块（crossfireAwareness > 0 时对 _moveDir 做
//    路径威胁规避）——v1 中性、v2 在 S6/S12/S14/S22/S26 回退。
// 3. think.ts §48-revisit trap 块（trapAvoidance > 0 时调用 trapAvoidanceImpl）。
// 4. ThreatAssessor §86 dodgeHysteresis（对齐阈值 TANK+2 于近期威胁）——
//    A/B -1.1pp，未发布。
// 5. ThreatAssessor §86 dodgeDirPersistence（同威胁保持上次闪避方向）——
//    A/B -1.7pp，未发布。
// 6. Navigator canMoveDirFloorSnap（Math.floor 替代 Math.round 的 snap）——
//    A/B -2.6pp（S6 -21.7pp），未发布。
// 7. FireControl damagedArmorBonus（找方向时对残血 armor 加权）——
//    S32 -8.4pp，否决。
// 8. params.computeStageAdaptedParams §63/§65/§69 适配块：
//    openT2a1HpMaxRange（S1/S7 + 但 S8/S11/S30/S33 回退，60-seed -0.6pp）、
//    armorMazeSuboptimalPathProb（30-seed +3pp 但 60-seed -1.7pp）、
//    crossfireOpenObstacleRatio（地形门控 crossfire 自动开启）。
// ============================================================
