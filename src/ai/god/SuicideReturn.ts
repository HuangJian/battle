import type { GodAIInput } from '../GodAIInput'
import type { Bullet, Tank } from '../../types'
import { GRID, CELL } from '../../constants'
import { PLAYER_PROGRESSION } from '../../config/combat'
import { enemyCanShootBase } from './SmartThreatModel'

// ============================================================
// SuicideReturn — 自杀秒回 (suicide quick-return, user request 2026-08-04).
//
// Strategy: when the player is far from a base-threatening enemy and about
// to die from a bullet anyway, intentionally take the bullet to respawn at
// the spawn point — which can kill the threat enemy in 0-1 turns — instead
// of dodging and failing to save the base.
//
// All functions are pure World-state reads (AGENTS §2.1/§2.3) — no RNG, no
// hidden state. The candidate (think.ts SUICIDE_RETURN) calls these to check
// the 5 preconditions before committing.
// ============================================================

/** Lives of the tank the God AI controls (P1 in single-player, P2 in coop). */
export function controlledLives(self: GodAIInput): number {
  const w = self.world
  return self.controlledTank(w) === w.player ? w.lives : w.lives2
}

/**
 * Check if a bullet hit would KILL the player (not just damage).
 * Star shield: a max-level (3★) player survives a lethal hit by spending
 * its top star — no death, no respawn. Below max level, damage >= hp kills.
 */
export function bulletWouldKillPlayer(p: Tank, bullet: Bullet): boolean {
  if (p.isPlayer && (p.level ?? 0) >= PLAYER_PROGRESSION.maximumLevel) return false
  return bullet.damage >= p.hp
}

/**
 * hasLethalBulletWithinWindow: scan ALL enemy bullets for one that is aligned
 * with + approaching the player AND would hit within `windowTicks` ticks AND
 * is lethal (would kill the player on impact). Used by the §116 candidate for
 * condition 5 (the task: 可能多发 — the nearest bullet may not be the lethal
 * one). Pure World-state read — no RNG, no hidden state.
 */
export function hasLethalBulletWithinWindowImpl(
  w: { bullets: Bullet[] },
  p: Tank,
  pcx: number,
  pcy: number,
  windowTicks: number,
): boolean {
  const bullets = w.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.isPlayer) continue
    if (!bulletWouldKillPlayer(p, b)) continue
    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    const vertical = b.dir === 'up' || b.dir === 'down'
    const aligned = vertical ? Math.abs(bcx - pcx) < 32 : Math.abs(bcy - pcy) < 32
    if (!aligned) continue
    const approaching =
      (b.dir === 'down' && bcy < pcy) ||
      (b.dir === 'up' && bcy > pcy) ||
      (b.dir === 'right' && bcx < pcx) ||
      (b.dir === 'left' && bcx > pcx)
    if (!approaching) continue
    const bdist = vertical ? Math.abs(bcy - pcy) : Math.abs(bcx - pcx)
    const bt = b.speed > 0 ? bdist / b.speed : Infinity
    if (bt <= windowTicks) return true
  }
  return false
}

/**
 * canShootEnemyFrom: check if a tank at (fc, fr) has a CLEAR shot at an
 * enemy at (ec, er) — aligned (same row or col) AND no brick/steel in
 * between. Mirrors canShootBaseFrom (SmartThreatModel) but targets an enemy
 * cell instead of the base. Static terrain check — no movement prediction.
 */
export function canShootEnemyFrom(
  self: GodAIInput,
  fc: number,
  fr: number,
  ec: number,
  er: number,
): boolean {
  const tm = self.world.tileMap
  if (fc === ec) {
    const step = fr < er ? 1 : -1
    for (let r = fr + step; r !== er; r += step) {
      if (r < 0 || r >= GRID) return false
      const t = tm.get(fc, r)
      if (t === 'brick' || t === 'steel' || t === 'base') return false
    }
    return true
  }
  if (fr === er) {
    const step = fc < ec ? 1 : -1
    for (let c = fc + step; c !== ec; c += step) {
      if (c < 0 || c >= GRID) return false
      const t = tm.get(c, fr)
      if (t === 'brick' || t === 'steel' || t === 'base') return false
    }
    return true
  }
  return false
}

/** 2×2 footprint fully passable (no brick/steel/water/base). Used for the
 * firing position — the tank must be able to STAND there (not break brick). */
function footprintPassable(tm: { grid: string[][] }, col: number, row: number): boolean {
  if (col < 0 || col + 1 >= GRID || row < 0 || row + 1 >= GRID) return false
  const g = tm.grid
  for (let dr = 0; dr <= 1; dr++) {
    const grow = g[row + dr]
    for (let dc = 0; dc <= 1; dc++) {
      const t = grow[col + dc]
      if (t === 'brick' || t === 'steel' || t === 'water' || t === 'base') return false
    }
  }
  return true
}

/** 2×2 footprint clear or breakable (no steel/water/base; brick is OK —
 * the player fires to break through while moving). Used for the movement
 * path — brick in the path is acceptable (the player clears it en route). */
function footprintClearOrBreakable(tm: { grid: string[][] }, col: number, row: number): boolean {
  if (col < 0 || col + 1 >= GRID || row < 0 || row + 1 >= GRID) return false
  const g = tm.grid
  for (let dr = 0; dr <= 1; dr++) {
    const grow = g[row + dr]
    for (let dc = 0; dc <= 1; dc++) {
      const t = grow[col + dc]
      if (t === 'steel' || t === 'water' || t === 'base') return false
    }
  }
  return true
}

/** Straight-line path (horizontal or vertical) clear for a 2×2 tank.
 * Intermediate positions allow brick (break-through); the destination is
 * checked separately by the caller. Returns false for non-straight paths. */
function straightPathClear(
  tm: { grid: string[][] },
  fromCol: number,
  fromRow: number,
  toCol: number,
  toRow: number,
): boolean {
  if (fromRow === toRow) {
    const step = fromCol < toCol ? 1 : -1
    for (let c = fromCol + step; c !== toCol; c += step) {
      if (!footprintClearOrBreakable(tm, c, fromRow)) return false
    }
    return true
  }
  if (fromCol === toCol) {
    const step = fromRow < toRow ? 1 : -1
    for (let r = fromRow + step; r !== toRow; r += step) {
      if (!footprintClearOrBreakable(tm, fromCol, r)) return false
    }
    return true
  }
  return false
}

/**
 * spawnCanHitEnemy: check if the player's spawn point can hit an enemy at
 * (ec, er) — either IMMEDIATELY (spawn aligned + clear line) or with ONE
 * TURN (move straight to a firing position, then turn to face the enemy).
 *
 * "One turn" (转一个弯) = an L-shaped path: move along the spawn's row to
 * the enemy's column (then fire vertically), or move along the spawn's
 * column to the enemy's row (then fire horizontally). The firing position
 * must be passable (standable, no brick) and the movement path must be
 * clear or breakable (brick OK — the player fires through it while moving).
 *
 * This ensures the respawned player can kill the threat enemy FAST (0-1
 * turns of movement), which is the whole point of the suicide trade.
 */
export function spawnCanHitEnemyImpl(self: GodAIInput, ec: number, er: number): boolean {
  const w = self.world
  const tm = w.tileMap
  const sc = w.playerSpawnPoint.col
  const sr = w.playerSpawnPoint.row

  // Bounds: a 2×2 tank needs col ∈ [0, GRID-2], row ∈ [0, GRID-2].
  if (ec < 0 || ec > GRID - 2 || er < 0 || er > GRID - 2) return false
  if (sc < 0 || sc > GRID - 2 || sr < 0 || sr > GRID - 2) return false

  // Immediate: spawn point aligned with enemy + clear line of fire.
  if (canShootEnemyFrom(self, sc, sr, ec, er)) return true

  // One turn — case 1: move horizontally to enemy's column, fire vertically.
  // Firing position: (ec, sr). The player moves from (sc, sr) to (ec, sr),
  // then turns up/down to face the enemy.
  if (
    ec !== sc &&
    footprintPassable(tm, ec, sr) &&
    straightPathClear(tm, sc, sr, ec, sr) &&
    canShootEnemyFrom(self, ec, sr, ec, er)
  ) {
    return true
  }

  // One turn — case 2: move vertically to enemy's row, fire horizontally.
  // Firing position: (sc, er). The player moves from (sc, sr) to (sc, er),
  // then turns left/right to face the enemy.
  if (
    er !== sr &&
    footprintPassable(tm, sc, er) &&
    straightPathClear(tm, sc, sr, sc, er) &&
    canShootEnemyFrom(self, sc, er, ec, er)
  ) {
    return true
  }

  return false
}

/**
 * Weak re-check for an IN-PROGRESS suicide trade (modes 2/3, §117): any live
 * enemy currently at a threat point (condition ① only — can directly shoot
 * the base). The committed branch continues the trade as long as such an
 * enemy exists — it deliberately does NOT re-check conditions ②/④ (spawn
 * usefulness, player distance): aborting a charge/stand mid-way just because
 * the player has closed the distance would waste the maneuver. Pure
 * World-state read — no RNG, no hidden state.
 */
export function anyThreatPointEnemyImpl(self: GodAIInput): Tank | null {
  if (!self.hasBase) return null
  const enemies = self._enemies.length > 0 ? self._enemies : self.world.tanks
  for (let ei = 0; ei < enemies.length; ei++) {
    const e = enemies[ei]
    if (!e.alive || e.spawnTimer > 0) continue
    if (enemyCanShootBase(self, e)) return e
  }
  return null
}

/**
 * Find a threat-point enemy that satisfies the suicide-return preconditions
 * (conditions 1, 2, 4):
 *   1. The enemy can directly shoot the base (enemyCanShootBase).
 *   2. The spawn point can hit this enemy (spawnCanHitEnemyImpl).
 *   4. The player is too far to reach it in time (> suicideReturnEnemyDistTicks
 *      at full speed).
 *
 * Returns the qualifying enemy farthest from the player (worst case — the
 * one the player is least able to deal with from the current position), or
 * null when no enemy qualifies. Pure World-state read — no RNG, no hidden
 * state.
 */
export function findSuicideTargetImpl(self: GodAIInput, pcx: number, pcy: number): Tank | null {
  const w = self.world
  if (!self.hasBase) return null
  const enemies = self._enemies.length > 0 ? self._enemies : w.tanks
  const playerSpeed = self.controlledTank(w)?.speed ?? 1
  const enemyDistTicks = self.params.suicideReturnEnemyDistTicks

  let best: Tank | null = null
  let bestDist = -1

  for (let ei = 0; ei < enemies.length; ei++) {
    const e = enemies[ei]
    if (!e.alive || e.spawnTimer > 0) continue
    // Condition 1: enemy at a threat point (can shoot base).
    if (!enemyCanShootBase(self, e)) continue
    // enemyCanShootBase calls self.tankCell(e) internally; re-read into
    // locals before any other tankCell call overwrites the reusable buffer.
    const ec = self.tankCell(e)
    const ecCol = ec.col
    const ecRow = ec.row
    // Condition 2: the spawn is positioned to deal with this enemy better
    // than the player's current (doomed) position. Respawning is only a win
    // when it puts the player CLOSER to the base threat. Three acceptable
    // cases:
    //   a. a clear 0-1-turn shot from the spawn (spawnCanHitEnemyImpl), OR
    //   b. the spawn is within suicideReturnSpawnDistCells of the enemy, OR
    //   c. the spawn is strictly closer to the enemy than the player (a
    //      positional win — the respawned player reaches the threat sooner).
    const spawnDist =
      Math.abs(w.playerSpawnPoint.col - ecCol) + Math.abs(w.playerSpawnPoint.row - ecRow)
    const playerCol = Math.round(pcx / CELL)
    const playerRow = Math.round(pcy / CELL)
    const playerDist = Math.abs(playerCol - ecCol) + Math.abs(playerRow - ecRow)
    const spawnUseful =
      spawnCanHitEnemyImpl(self, ecCol, ecRow) ||
      (self.params.suicideReturnSpawnDistCells > 0 &&
        spawnDist <= self.params.suicideReturnSpawnDistCells) ||
      spawnDist < playerDist
    if (!spawnUseful) continue
    // Condition 4: player is too far to reach this enemy in time.
    const ecx = e.x + e.w / 2
    const ecy = e.y + e.h / 2
    const distPx = Math.abs(ecx - pcx) + Math.abs(ecy - pcy)
    const timeTicks = playerSpeed > 0 ? distPx / playerSpeed : Infinity
    if (timeTicks <= enemyDistTicks) continue
    // Prefer the farthest enemy (the one the player is worst positioned for).
    if (timeTicks > bestDist) {
      bestDist = timeTicks
      best = e
    }
  }

  return best
}
