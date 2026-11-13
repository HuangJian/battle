/**
 * P-1b-0 — inference-side fire predicates (plan/nn-input-inference-rules.plan.md §2.3).
 *
 * This module is the SINGLE implementation of `usefulFire` for the P-1b dose
 * probe and (later) P0a's resolver. The lead-aware obs feature
 * (`policy-extra.ts::leadHitForDir`) is the velocity-extrapolated variant of
 * the same geometry; both share `THREAT_ALIGN_BAND_PX` and `laneOccluded`, so
 * terrain semantics never fork.
 *
 * Rules (v1, static lane):
 *   · the enemy body overlaps the bullet's ±19px lane (6px bullet + 32px tank);
 *   · the enemy centre is in front and within 13 cells;
 *   · no brick/steel/base on the lane (water/grass/ice pass bullets);
 *   · spawning enemies, decoys and non-enemy allegiance are not targets.
 *
 * Pure: reads World only, writes nothing, allocates nothing, draws no RNG.
 */
import { CELL, DIR_VECTORS, GRID, POWERUP_TIMEOUT_MS, TICK_MS, type Direction } from '../constants'
import { STEEL_PIERCE_PLAYER_LEVEL } from '../config/combat'
import type { World } from '../game/World'
import { TileMap } from '../game/TileMap'
import type { Tank, TerrainType } from '../types'
import { MOVE4_DECODE } from './action-space-v6'
import {
  THREAT_ALIGN_BAND_PX,
  corridorCells,
  laneOccluded,
  nearestEnemyDistPx,
  threatLaneSources,
} from './danger-metrics'
import { bfsDistance, goalMoveBias } from './goal-mask'
import { isFireReady } from './obs-encoder'
import { leadHitForDir } from './policy-extra'
import { isBaseRingCell } from '../ai/god/ThreatBudget'

/**
 * Frozen predicate version (plan §4.1.5). Any behaviour change must bump this
 * AND re-run the P-1b dose measurement — old readings cannot be reused.
 */
export const USEFUL_FIRE_VERSION = 'u1-static-lane-2026-11-13'

/** B4：冰上滑行“请求反向”的最低速度阈值（与 God `iceGlideMinSpeed` 同值 0.3）。 */
export const ICE_GLIDE_MIN_SPEED = 0.3

/** Maximum axial distance (13 cells, mirroring `leadHitForDir`'s cap). */
export const USEFUL_FIRE_MAX_AXIAL_PX = 13 * CELL

/**
 * True when a shot fired along `dir` can hit an enemy tank in the static lane.
 *
 * `tank` is the shooter (normally `world.player`); null/dead/spawning shooters
 * return false. The predicate deliberately ignores enemy bullets (interception
 * is a post-v1 exemption) and bricks (B2 owns that value).
 */
export function usefulFire(world: World, tank: Tank | null | undefined, dir: Direction): boolean {
  if (!tank || !tank.alive || tank.spawnTimer > 0) return false

  const pcx = tank.x + tank.w / 2
  const pcy = tank.y + tank.h / 2
  const tileMap = world.tileMap
  const vertical = dir === 'up' || dir === 'down'
  const sign = dir === 'down' || dir === 'right' ? 1 : -1
  const shooterCol = Math.floor(pcx / CELL)
  const shooterRow = Math.floor(pcy / CELL)
  const tanks = world.tanks

  for (let i = 0; i < tanks.length; i++) {
    const target = tanks[i]
    if (!target.alive || target.allegiance !== 'enemy') continue
    // Spawning tanks are invulnerable and do not block bullets; decoys are
    // lures, not combat value (plan §2.3 boundary list).
    if (target.spawnTimer > 0 || target.isDecoy) continue

    const tx = target.x + target.w / 2
    const ty = target.y + target.h / 2

    // In front and within the same 13-cell reach as `leadHitForDir`.
    const axial = (vertical ? ty - pcy : tx - pcx) * sign
    if (axial <= 0 || axial > USEFUL_FIRE_MAX_AXIAL_PX) continue

    // Center offset must overlap the bullet lane: 6/2 + 32/2 = 19px.
    const lateral = vertical ? tx - pcx : ty - pcy
    if (Math.abs(lateral) >= THREAT_ALIGN_BAND_PX) continue

    // Target body span in pixels (32px tanks can straddle 3 cells).
    const colLow = Math.floor(target.x / CELL)
    const colHigh = Math.floor((target.x + target.w - 1) / CELL)
    const rowLow = Math.floor(target.y / CELL)
    const rowHigh = Math.floor((target.y + target.h - 1) / CELL)
    const occluded = laneOccluded(
      tileMap,
      shooterCol,
      shooterRow,
      vertical,
      vertical ? rowLow : colLow,
      vertical ? rowHigh : colHigh,
    )
    if (!occluded) return true
  }
  return false
}

/**
 * First bullet-blocking terrain along the centre-cell lane (same walk as
 * `firstBulletBlockerPx`). `null` = nothing blocks before the field edge.
 * Single source for B1 (steel) and B2 (brick): water/grass/ice pass bullets.
 */
export function firstBulletBlocker(
  world: World,
  tank: Tank | null | undefined,
  dir: Direction,
): TerrainType | null {
  if (!tank || !tank.alive) return null
  const v = DIR_VECTORS[dir]
  const col = Math.floor((tank.x + tank.w / 2) / CELL)
  const row = Math.floor((tank.y + tank.h / 2) / CELL)
  for (let i = 1; i <= GRID; i++) {
    const c = col + v.dx * i
    const r = row + v.dy * i
    if (c < 0 || c >= GRID || r < 0 || r >= GRID) return null
    const t = world.tileMap.get(c, r)
    if (TileMap.blocksBullet(t)) return t
  }
  return null
}

/**
 * B2 v1 — is a brick immediately ahead the thing stopping the advance?
 *
 * `corridorCells(..., 1) === 0` = the tank cannot step one cell; the centre
 * lane's first cell must be brick (not steel/base) and not a base-ring brick
 * (breaking those exposes the base; B2 owns path bricks only).
 */
export function brickBlocksAdvance(
  world: World,
  tank: Tank | null | undefined,
  dir: Direction,
): boolean {
  if (!tank || !tank.alive) return false
  return corridorCells(world, tank, dir, 1) === 0 && brickAheadWithin(world, tank, dir, 1)
}

/**
 * B2 v1 — is a non-base-ring brick within `maxCells` on the centre lane?
 * Stops at the first bullet blocker, so a brick behind steel/base is false.
 */
export function brickAheadWithin(
  world: World,
  tank: Tank | null | undefined,
  dir: Direction,
  maxCells: number,
): boolean {
  if (!tank || !tank.alive || maxCells < 1) return false
  const v = DIR_VECTORS[dir]
  const col = Math.floor((tank.x + tank.w / 2) / CELL)
  const row = Math.floor((tank.y + tank.h / 2) / CELL)
  for (let i = 1; i <= maxCells; i++) {
    const c = col + v.dx * i
    const r = row + v.dy * i
    if (c < 0 || c >= GRID || r < 0 || r >= GRID) return false
    const t = world.tileMap.get(c, r)
    if (t === 'brick') return !isBaseRingCell(c, r)
    if (TileMap.blocksBullet(t)) return false
  }
  return false
}

/**
 * B1 v1 — is the shot along `dir` a pure "wasted on steel" shot?
 *
 * True only when:
 *   · the player cannot pierce steel (`level < STEEL_PIERCE_PLAYER_LEVEL`);
 *   · no enemy is hittable in the lane (`usefulFire` false);
 *   · the first bullet-blocking cell along the centre lane is steel.
 * Brick/base block return false (B2 owns brick; base is not a B1 target).
 * The 6px path is approximated by the centre cell row/column (same walk as
 * `firstBulletBlockerPx`), which is sufficient for axis-aligned tanks.
 *
 * 175-pair screening (2026-11-13): win 39.43% → 46.86% (Δ +7.43pp, p=0.142);
 * mechanism 1,410 steel-shot decisions suppressed to 0. Passes the +5pp
 * futility gate. 700-pair confirmation: 36.43% → 42.43% (Δ +6.00pp,
 * p=0.0052, cluster bootstrap 95% CI [+2.57, +9.57]); wired into
 * `export-eval-game` behind `--skip-steel-only-shots` (default off) with 3/3 byte-identical
 * B1-off parity vs HEAD. Training chains stay off; eval/deploy only.
 */
export function steelOnlyShot(
  world: World,
  tank: Tank | null | undefined,
  dir: Direction,
): boolean {
  if (!tank || !tank.alive || (tank.level ?? 0) >= STEEL_PIERCE_PLAYER_LEVEL) return false
  if (usefulFire(world, tank, dir)) return false
  return firstBulletBlocker(world, tank, dir) === 'steel'
}

/** Dominant-axis v6 move index toward (dx, dy); -1 when the vector is zero. */
function dirIndexToward(dx: number, dy: number): number {
  if (dx === 0 && dy === 0) return -1
  const dir: Direction =
    Math.abs(dx) >= Math.abs(dy) ? (dx > 0 ? 'right' : 'left') : dy > 0 ? 'down' : 'up'
  return MOVE4_DECODE.indexOf(dir)
}

/**
 * C4 v1 — during a TRUE shield window (respawn / star / power-up shield),
 * bias the move toward a nearby pickup, else toward the nearest enemy.
 *
 * Returns a v6 move index (0..3) or -1 (no target / blocked / not shielded).
 * Freeze is deliberately excluded: frozen enemies stop moving but in-flight
 * bullets still hit (see `threatLaneExempt`), so freeze belongs to C5, not C4.
 *
 * ⚠ v1 A/B (2026-11-13, 175 pairs) was NEUTRAL on win (39.43% → 38.86%,
 * Δ −0.57pp, p=1.0); clearT improved as a secondary read only. Default off;
 * do not deploy without a new pre-registration.
 *
 * Pure: reads World only, zero allocation, no RNG.
 */
export function c4MoveBias(
  world: World,
  tank: Tank | null | undefined,
  pickupRadiusCells: number,
  enemyRadiusCells: number,
): number {
  if (!tank || !tank.alive || tank.spawnTimer > 0 || (tank.shieldTimer ?? 0) <= 0) return -1
  const pcx = tank.x + tank.w / 2
  const pcy = tank.y + tank.h / 2
  let bestD = Infinity
  let bestDx = 0
  let bestDy = 0

  // 1) Pickup first: invincibility is the only window where chasing a pickup
  //    through a nearby enemy is safe.
  const pickups = world.powerUps
  for (let i = 0; i < pickups.length; i++) {
    const pu = pickups[i]
    if (!pu.alive) continue
    const dx = pu.x + pu.w / 2 - pcx
    const dy = pu.y + pu.h / 2 - pcy
    const d = Math.max(Math.abs(dx), Math.abs(dy))
    if (d < bestD) {
      bestD = d
      bestDx = dx
      bestDy = dy
    }
  }
  if (bestD <= pickupRadiusCells * CELL) {
    const idx = dirIndexToward(bestDx, bestDy)
    if (idx >= 0 && corridorCells(world, tank, MOVE4_DECODE[idx], 1) > 0) return idx
  }

  // 2) Otherwise push toward the nearest enemy (trade damage while invincible).
  bestD = Infinity
  const tanks = world.tanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0 || t.isDecoy) continue
    const dx = t.x + t.w / 2 - pcx
    const dy = t.y + t.h / 2 - pcy
    const d = Math.max(Math.abs(dx), Math.abs(dy))
    if (d < bestD) {
      bestD = d
      bestDx = dx
      bestDy = dy
    }
  }
  if (bestD <= enemyRadiusCells * CELL) {
    const idx = dirIndexToward(bestDx, bestDy)
    if (idx >= 0 && corridorCells(world, tank, MOVE4_DECODE[idx], 1) > 0) return idx
  }
  return -1
}

/**
 * E1 v1 — residual-enemy BFS target bias.
 *
 * Only when the nearest enemy is farther than `farCells` (Chebyshev distance)
 * does this return the 4-direction BFS alignment [up, down, left, right]
 * (values -1/0/+1) toward that enemy. Close combat is left to the policy.
 * Returns null when there is no enemy, the nearest is close, or it is
 * unreachable under the walkable terrain (empty/forest/ice).
 *
 * NOTE: reuses `goal-mask.goalMoveBias` (BFS + small arrays) at decision
 * cadence (1/K ticks), not per tick. A deploy-grade version must switch to a
 * caller-owned scratch buffer (plan §3 E1 / qw §3.3).
 *
 * ⚠ v1 A/B (2026-11-13, 175 pairs) was NEGATIVE: 39.43% → 32.57% (Δ −6.86pp,
 * p=0.162), wall +20%. Default off; do not deploy.
 */
export function e1MoveBias(
  world: World,
  tank: Tank | null | undefined,
  farCells = 8,
): number[] | null {
  if (!tank || !tank.alive || tank.spawnTimer > 0) return null
  const pcx = tank.x + tank.w / 2
  const pcy = tank.y + tank.h / 2
  let bestD = Infinity
  let bestX = 0
  let bestY = 0
  const tanks = world.tanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0 || t.isDecoy) continue
    const tx = t.x + t.w / 2
    const ty = t.y + t.h / 2
    const d = Math.max(Math.abs(tx - pcx), Math.abs(ty - pcy))
    if (d < bestD) {
      bestD = d
      bestX = tx
      bestY = ty
    }
  }
  if (!(bestD > farCells * CELL)) return null
  const dc = Math.max(0, Math.min(GRID - 1, Math.round(bestX / CELL)))
  const dr = Math.max(0, Math.min(GRID - 1, Math.round(bestY / CELL)))
  const align = goalMoveBias(world, dc, dr)
  // [stay, up, down, left, right] → [up, down, left, right]
  return [align[1], align[2], align[3], align[4]]
}

// ---------------------------------------------------------------------------
// backlog / dormant batch (B8 / B10 / B11 / C3 / C5 / C7 / C8 / D1)
// v1 predicates for the 175-pair screening. All default-off in NNInput.
// ---------------------------------------------------------------------------

/** v6 move order used by every deterministic reroute/bias pick. */
const MOVE_DIRS: readonly Direction[] = ['up', 'down', 'left', 'right']

const OPPOSITE_DIR: Record<Direction, Direction> = {
  up: 'down',
  down: 'up',
  left: 'right',
  right: 'left',
}

/**
 * B8 v1 — general wasted shot: nothing hittable in the static lane AND the
 * first bullet blocker is not brick (brick value belongs to B2).
 *
 * Superset of `steelOnlyShot`; the A/B arm runs on top of B1, so only the
 * incremental (empty lane / water-grass-ice lane) shots move.
 */
export function uselessShot(world: World, tank: Tank | null | undefined, dir: Direction): boolean {
  if (!tank || !tank.alive) return false
  if (usefulFire(world, tank, dir)) return false
  return firstBulletBlocker(world, tank, dir) !== 'brick'
}

/**
 * B10 v1 — lead-aware gate: even with velocity extrapolation the shot cannot
 * hit (`leadHitForDir === 0`) and the first bullet blocker is not brick.
 *
 * Stricter than B8 (the extrapolated lane is a subset of the static lane).
 */
export function leadMissShot(world: World, tank: Tank | null | undefined, dir: Direction): boolean {
  if (!tank || !tank.alive) return false
  const pcx = tank.x + tank.w / 2
  const pcy = tank.y + tank.h / 2
  if (leadHitForDir(world, dir, pcx, pcy) !== 0) return false
  return firstBulletBlocker(world, tank, dir) !== 'brick'
}

/**
 * B11 v1 — the selected move cannot step one cell and the lane's first bullet
 * blocker is steel (the tank is being pushed into a steel wall).
 */
export function steelBlockedMove(
  world: World,
  tank: Tank | null | undefined,
  dir: Direction,
): boolean {
  if (!tank || !tank.alive || corridorCells(world, tank, dir, 1) > 0) return false
  return firstBulletBlocker(world, tank, dir) === 'steel'
}

/** B11 v1 — first walkable alternative direction (fixed order, no randomness). */
export function alternativeMoveDir(
  world: World,
  tank: Tank | null | undefined,
  dir: Direction,
): Direction | null {
  if (!tank || !tank.alive) return null
  for (let i = 0; i < MOVE_DIRS.length; i++) {
    const d = MOVE_DIRS[i]
    if (d !== dir && corridorCells(world, tank, d, 1) > 0) return d
  }
  return null
}

/** Nearest live non-spawning enemy within `maxPx` (Chebyshev); reference, no allocation. */
function nearestEnemyWithin(world: World, cx: number, cy: number, maxPx: number): Tank | null {
  const tanks = world.tanks
  let best: Tank | null = null
  let bestD = maxPx
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0 || t.isDecoy) continue
    const d = Math.max(Math.abs(t.x + t.w / 2 - cx), Math.abs(t.y + t.h / 2 - cy))
    if (d <= bestD) {
      bestD = d
      best = t
    }
  }
  return best
}

/**
 * C3 v1 — pincer breakout: enemies within `radiusCells` on both left/right or
 * both up/down sides ⇒ bias one step away from the nearest enemy, if walkable.
 */
export function pincerBreakoutDir(
  world: World,
  tank: Tank | null | undefined,
  radiusCells = 4,
): number {
  if (!tank || !tank.alive || tank.spawnTimer > 0) return -1
  const pcx = tank.x + tank.w / 2
  const pcy = tank.y + tank.h / 2
  const maxPx = radiusCells * CELL
  let left = 0
  let right = 0
  let up = 0
  let down = 0
  let bestD = Infinity
  let bestDx = 0
  let bestDy = 0
  const tanks = world.tanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0 || t.isDecoy) continue
    const dx = t.x + t.w / 2 - pcx
    const dy = t.y + t.h / 2 - pcy
    if (Math.abs(dx) > maxPx || Math.abs(dy) > maxPx) continue
    const d = Math.max(Math.abs(dx), Math.abs(dy))
    if (d < bestD) {
      bestD = d
      bestDx = dx
      bestDy = dy
    }
    if (Math.abs(dx) >= Math.abs(dy)) {
      if (dx < 0) left++
      else right++
    } else if (dy < 0) up++
    else down++
  }
  if (!((left > 0 && right > 0) || (up > 0 && down > 0))) return -1
  const idx = dirIndexToward(-bestDx, -bestDy)
  if (idx < 0) return -1
  return corridorCells(world, tank, MOVE4_DECODE[idx], 1) > 0 ? idx : -1
}

/**
 * C5 v1 — frozen-enemy offense: while `freezeTimer > 0`, bias toward the
 * nearest live enemy within `radiusCells`.
 */
export function freezeAttackDir(
  world: World,
  tank: Tank | null | undefined,
  radiusCells = 8,
): number {
  if (!tank || !tank.alive || tank.spawnTimer > 0 || world.freezeTimer <= 0) return -1
  const target = nearestEnemyWithin(
    world,
    tank.x + tank.w / 2,
    tank.y + tank.h / 2,
    radiusCells * CELL,
  )
  if (!target) return -1
  const idx = dirIndexToward(
    target.x + target.w / 2 - (tank.x + tank.w / 2),
    target.y + target.h / 2 - (tank.y + tank.h / 2),
  )
  if (idx < 0) return -1
  return corridorCells(world, tank, MOVE4_DECODE[idx], 1) > 0 ? idx : -1
}

/**
 * C7 v1 — duel first shot: fire is ready and an enemy within 13 cells is
 * directly in front (19px band) and facing the player.
 */
export function duelShotReady(world: World, tank: Tank | null | undefined): boolean {
  if (!tank || !tank.alive || tank.spawnTimer > 0) return false
  if (!isFireReady(world)) return false
  const pcx = tank.x + tank.w / 2
  const pcy = tank.y + tank.h / 2
  const fx = tank.dir === 'left' ? -1 : tank.dir === 'right' ? 1 : 0
  const fy = tank.dir === 'up' ? -1 : tank.dir === 'down' ? 1 : 0
  const facing = OPPOSITE_DIR[tank.dir]
  const tanks = world.tanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0 || t.isDecoy) continue
    if (t.dir !== facing) continue
    const dx = t.x + t.w / 2 - pcx
    const dy = t.y + t.h / 2 - pcy
    const axial = dx * fx + dy * fy
    if (axial <= 0 || axial > 13 * CELL) continue
    const lateral = Math.abs(dx * fy) + Math.abs(dy * fx)
    if (lateral < THREAT_ALIGN_BAND_PX) return true
  }
  return false
}

/**
 * C8 v1 — crossfire: ≥2 threat lanes and an enemy within `radiusCells` ⇒ bias
 * toward the walkable direction that strictly increases the distance to the
 * nearest enemy (fewer exposed lanes).
 */
export function crossfireEscapeDir(
  world: World,
  tank: Tank | null | undefined,
  radiusCells = 4,
): number {
  if (!tank || !tank.alive || tank.spawnTimer > 0) return -1
  if (threatLaneSources(world) < 2) return -1
  const pcx = tank.x + tank.w / 2
  const pcy = tank.y + tank.h / 2
  const target = nearestEnemyWithin(world, pcx, pcy, radiusCells * CELL)
  if (!target) return -1
  const tx = target.x + target.w / 2
  const ty = target.y + target.h / 2
  let bestIdx = -1
  let bestD = Math.max(Math.abs(tx - pcx), Math.abs(ty - pcy))
  for (let m = 0; m < 4; m++) {
    const dir = MOVE4_DECODE[m]
    if (corridorCells(world, tank, dir, 1) <= 0) continue
    const v = DIR_VECTORS[dir]
    const d = Math.max(Math.abs(tx - (pcx + v.dx * CELL)), Math.abs(ty - (pcy + v.dy * CELL)))
    if (d > bestD) {
      bestD = d
      bestIdx = m
    }
  }
  return bestIdx
}

/**
 * D1 v1 — reachable pickup: an alive pickup with `ETA ≤ TTL` while no enemy is
 * within `enemyClearCells` ⇒ bias toward the soonest-expiring reachable pickup.
 *
 * One BFS from the player per decision (same `bfsDistance` as E1); this is the
 * diagnostic-grade version — a deploy-grade variant needs a caller-owned
 * scratch buffer (plan §3 D1).
 */
export function reachablePickupDir(
  world: World,
  tank: Tank | null | undefined,
  enemyClearCells = 4,
): number {
  if (!tank || !tank.alive || tank.spawnTimer > 0) return -1
  // `nearestEnemyDistPx` returns -1 when no enemy is alive (sentinel), so the
  // "clear" branch must be explicit — otherwise no-enemy maps to "enemy close".
  const nearest = nearestEnemyDistPx(world)
  if (nearest >= 0 && nearest <= enemyClearCells * CELL) return -1
  const pickups = world.powerUps
  let anyAlive = false
  for (let i = 0; i < pickups.length; i++) {
    if (pickups[i].alive) {
      anyAlive = true
      break
    }
  }
  if (!anyAlive) return -1
  const pcx = tank.x + tank.w / 2
  const pcy = tank.y + tank.h / 2
  const col = Math.max(0, Math.min(GRID - 1, Math.floor(pcx / CELL)))
  const row = Math.max(0, Math.min(GRID - 1, Math.floor(pcy / CELL)))
  const dist = bfsDistance(world, col, row)
  if (dist[row * GRID + col] < 0) return -1
  let bestEta = Infinity
  let bestDx = 0
  let bestDy = 0
  for (let i = 0; i < pickups.length; i++) {
    const pu = pickups[i]
    if (!pu.alive) continue
    const dc = Math.floor((pu.x + pu.w / 2) / CELL)
    const dr = Math.floor((pu.y + pu.h / 2) / CELL)
    if (dc < 0 || dc >= GRID || dr < 0 || dr >= GRID) continue
    const steps = dist[dr * GRID + dc]
    if (steps <= 0) continue
    const etaTicks = (steps * CELL) / Math.max(tank.speed, 0.01)
    const remainTicks = (POWERUP_TIMEOUT_MS - pu.lifeTimer) / TICK_MS
    if (etaTicks > remainTicks || etaTicks >= bestEta) continue
    bestEta = etaTicks
    bestDx = pu.x + pu.w / 2 - pcx
    bestDy = pu.y + pu.h / 2 - pcy
  }
  if (!isFinite(bestEta)) return -1
  const idx = dirIndexToward(bestDx, bestDy)
  if (idx < 0) return -1
  return corridorCells(world, tank, MOVE4_DECODE[idx], 1) > 0 ? idx : -1
}

/**
 * A3/A2 reuse the God-AI turn predicate verbatim — never copy it (plan §2.1 /
 * §4.1.5). Re-exported here so resolver consumers have one import site.
 */
export { turnWillCommit } from '../ai/god/turn-gate'
