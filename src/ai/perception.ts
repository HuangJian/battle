import type { World } from '../game/World'
import type { Tank } from '../types'
import type { Direction } from '../constants'
import { CELL, TANK, DIR_VECTORS, FIELD, GRID } from '../constants'
import { aabb, snap, bulletLaneDist } from '../utils/helpers'
import type { Perception, Situation, IntelligenceConfig } from './types'

import { manhattan } from '../utils/helpers'
import { BULLET_ALIGN_NEXT_CELL } from './god/constants'

/**
 * ai/perception.ts — the "eyes" of the framework.
 *
 * Perception converts the World into an observation snapshot the AI is allowed
 * to reason about. The AI never reads gameplay objects directly (AI
 * Constitution #1: "Perceive before deciding"). Situation Analysis then turns
 * observations into tactical knowledge — analysis only, no decisions.
 */

/** Manhattan distance between two tank centers (px). */
// Canonical manhattan moved to utils/helpers (遗留 #2 unification) —
// re-exported to keep TacticalIntelligence's import path stable.
export { manhattan } from '../utils/helpers'

/** Primary-axis direction from a tank toward a target point (used for routing). */
export function dirToward(x: number, y: number, tx: number, ty: number): Direction {
  const dx = tx - x
  const dy = ty - y
  if (Math.abs(dx) > Math.abs(dy)) return dx > 0 ? 'right' : 'left'
  return dy > 0 ? 'down' : 'up'
}

/**
 * Can the tank take one full step in `dir` from its current position?
 * `considerTanks` includes other tanks as obstacles (used for routing to avoid
 * gridlock); the movement system still resolves final collisions regardless.
 *
 * The check covers the *swept union* of the tank's current and destination
 * footprints, not just the destination. Checking only the destination was the
 * root cause of "enemies stuck on each other / against walls": a tank already
 * touching a wall (or another tank) on the side it wants to move along would
 * see a clear destination (the destination sits *past* the obstacle) while the
 * very first fine-step already overlaps it — so the AI kept choosing that
 * "open" lane, rammed the obstacle every tick, and froze. The swept union
 * catches the obstacle the tank is adjacent to, so such a direction is
 * correctly reported as blocked.
 */
export function canStep(
  world: World,
  tank: Tank,
  dir: Direction,
  considerTanks: boolean,
  others?: Tank[],
): boolean {
  const v = DIR_VECTORS[dir]
  // Reason about the tank on its conceptual grid. The cross-axis is kept
  // aligned by `alignTank` every tick, but the movement axis can accumulate
  // sub-cell drift (speed is fractional). Using the raw (drifted) position in
  // the swept union makes two tanks stacked in a column wrongly report each
  // other as blocking their perpendicular move — a primary cause of "enemies
  // stuck on each other". Snapping to the grid removes that phantom overlap;
  // since a tank can never legally overlap a real obstacle, the grid-aligned
  // union agrees with the actual movement collision.
  const gx = snap(tank.x, CELL)
  const gy = snap(tank.y, CELL)
  const nx = gx + v.dx * TANK
  const ny = gy + v.dy * TANK
  // Swept rectangle: union of the current footprint [gx,gx+TANK] and the
  // destination footprint [nx,nx+TANK]. Width/height = step distance + size.
  const sx = Math.min(gx, nx)
  const sy = Math.min(gy, ny)
  const sw = Math.abs(nx - gx) + TANK
  const sh = Math.abs(ny - gy) + TANK
  if (!world.isInBounds(sx, sy, sw, sh)) return false
  if (world.rectHitsTerrain(sx, sy, sw, sh)) return false
  if (considerTanks) {
    // `others` (when provided by perceive) is the precomputed set of all
    // other live tanks — avoids re-scanning world.allTanks for every one of
    // the 4 direction checks. Falls back to world.allTanks otherwise.
    const list = others ?? world.allTanks
    for (let i = 0; i < list.length; i++) {
      const o = list[i]
      if (o === tank || !o.alive || o.spawnTimer > 0) continue
      // Decoys (诱饵) never block movement (不卡位置) — enemies path through
      // them freely; only their bullets may strike (bulletHitsTank).
      if (o.isDecoy) continue
      if (aabb(sx, sy, sw, sh, o.x, o.y, o.w, o.h)) return false
    }
  }
  return true
}

/** Result of scanning ahead along a direction. */
export type ScanHit = 'none' | 'base' | 'player' | 'decoy' | 'wall' | 'steel'

/**
 * Step along `dir` (in CELL increments) up to `maxDist` px and report the
 * first thing an enemy bullet would strike: base, player, brick (wall),
 * steel, or nothing. Forest/water/ice do NOT block bullets (TileMap), so they
 * are skipped. Other enemy tanks never block enemy bullets, so they are
 * ignored — only the player tank and terrain matter for an enemy's line of
 * fire.
 *
 * Returns the hit type as a string (no object allocation) — the caller
 * (`analyze`) only checks the hit category, never the distance.
 */
export function scanAhead(world: World, tank: Tank, dir: Direction, maxDist: number): ScanHit {
  const v = DIR_VECTORS[dir]
  const sx = tank.x + tank.w / 2
  const sy = tank.y + tank.h / 2
  // Lie-Back-Win-Mode §3.8 P3: detect both players in line of fire.
  const player = world.player
  // 督战双玩家 (spectateDual) is a second, machine-controlled player — it must
  // be perceived exactly like the Lie-Back-Win coop partner, or the P2 God AI
  // is blind to its own tank (scanAhead) and mis-targets (perceive picks P1).
  const player2 = world.coop || world.spectateDual ? world.player2 : null
  // §233 (perf): the scan walks whole cells along an axis-aligned dir, so the
  // scanned cell increments by exactly ±1 per step. Integer cell stepping
  // replaces the per-step multiply + division + floor; pixel coordinates are
  // derived from the cell + precomputed center offset ONLY when a tank/decoy
  // AABB check needs them. Arithmetic identity: floor((sx + dx·k·CELL)/CELL)
  // = floor(sx/CELL) + dx·k for integer k, and cx = col·CELL + (sx mod CELL)
  // — byte-identical cells and pixels.
  const allies = world.allies
  const offX = sx - Math.floor(sx / CELL) * CELL
  const offY = sy - Math.floor(sy / CELL) * CELL
  let col = Math.floor(sx / CELL)
  let row = Math.floor(sy / CELL)
  const steps = Math.floor(maxDist / CELL)
  for (let i = 1; i <= steps; i++) {
    col += v.dx
    row += v.dy
    // Out-of-bounds is treated as steel (impassable) — matches TileMap.get's
    // behavior for the original string grid.
    if (col < 0 || col >= GRID || row < 0 || row >= GRID) return 'steel'
    const tt = world.tileMap.get(col, row)
    if (tt === 'base') return 'base'
    if (tt === 'brick') return 'wall'
    if (tt === 'steel') return 'steel'
    if (player && player.alive) {
      const cx = col * CELL + offX
      const cy = row * CELL + offY
      if (aabb(cx - 1, cy - 1, 2, 2, player.x, player.y, player.w, player.h)) {
        return 'player'
      }
    }
    if (player2 && player2.alive) {
      const cx = col * CELL + offX
      const cy = row * CELL + offY
      if (aabb(cx - 1, cy - 1, 2, 2, player2.x, player2.y, player2.w, player2.h)) {
        return 'player'
      }
    }
    // Decoy (诱饵): a fake tank that draws enemy fire. If an enemy's line of
    // fire crosses a decoy, the decoy is a valid (and desirable) target so the
    // enemy shoots it instead of pushing toward the base/player (new-powerups
    // §4.4). Bullets pass through allies, but the decoy is the exception we
    // want enemies to aim at.
    for (let ai = 0; ai < allies.length; ai++) {
      const dec = allies[ai]
      if (!dec.alive || !dec.isDecoy || dec.spawnTimer > 0) continue
      const cx = col * CELL + offX
      const cy = row * CELL + offY
      if (aabb(cx - 1, cy - 1, 2, 2, dec.x, dec.y, dec.w, dec.h)) {
        return 'decoy'
      }
    }
  }
  return 'none'
}

// Hoisted constant — avoids allocating a 4-element array on every perceive call.
const PERCEIVE_DIRS: readonly Direction[] = ['up', 'down', 'left', 'right']

/** Compute open directions (4 × canStep). Exported for lazy on-demand use
 * by reactiveDodge when perceive skipped this computation (perf: the scan
 * is ~100 ops but only needed by throttled tacticalThink or rare threat-dodge). */
export function computeOpenDirs(world: World, tank: Tank, all: Tank[]): Direction[] {
  const openDirs: Direction[] = []
  for (let i = 0; i < PERCEIVE_DIRS.length; i++) {
    if (canStep(world, tank, PERCEIVE_DIRS[i], true, all)) openDirs.push(PERCEIVE_DIRS[i])
  }
  return openDirs
}

/** Build the observation snapshot for one tank.
 * When `computeOpenDirs` is false, the expensive 4 × canStep scan is skipped
 * (openDirs = []). The caller (reactiveDodge) must call computeOpenDirs()
 * on demand if it needs openDirs on a tick where perceive skipped it.
 *
 * `all` (when provided) is the precomputed `world.allTanks` buffer — passing
 * it avoids re-fetching the getter (AGENTS.md §14.6). Tests may omit it; the
 * function falls back to `world.allTanks` (slower path, but identical result).
 *
 * The returned Perception is allocation-free in the hot path: it carries flat
 * threat + teammate aggregates instead of `BulletObservation[]` /
 * `TeammateObservation[]` (those types were removed; see types.ts). */
export function perceive(
  world: World,
  tank: Tank,
  cfg: IntelligenceConfig,
  needOpenDirs = true,
  all?: Tank[],
): Perception {
  const sx = tank.x + tank.w / 2
  const sy = tank.y + tank.h / 2
  // Lie-Back-Win-Mode §3.8 P3: pick the closest player as the perception target.
  let player = world.player
  // 督战双玩家 (spectateDual) is a second, machine-controlled player — it must
  // be perceived like the coop partner, or the P2 God AI anchors its awareness
  // to P1 instead of its own tank (grossly mis-targets, e.g. defends P1's side).
  if ((world.coop || world.spectateDual) && world.player2) {
    const p1Dist = player
      ? manhattan(player.x + player.w / 2, player.y + player.h / 2, sx, sy)
      : Infinity
    const p2Dist = manhattan(
      world.player2.x + world.player2.w / 2,
      world.player2.y + world.player2.h / 2,
      sx,
      sy,
    )
    if (p2Dist < p1Dist) player = world.player2
  }
  const base = world.tileMap.getBasePos()

  // Track the single closest threat via flat fields — consumers only read the
  // threat's direction (analyze → s.threatDir, used by reactiveDodge). The
  // prior design allocated a `BulletObservation[]` + per-element objects here
  // every call (~1.1M allocs per 30-game batch); the new design allocates
  // nothing.
  const range = cfg.predictionDepth * CELL
  let hasThreat = false
  let threatDist = Infinity
  let threatDir: Direction = 'up'
  const bullets = world.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    // Only hostile-to-enemy bullets are a threat: player OR ally fire (ally
    // bullets carry allegiance 'ally', never 'enemy').
    if (!b.alive || b.allegiance === 'enemy') continue
    // §3.1 single-sourced lane geometry (allegiance filtered above).
    const dist = bulletLaneDist(
      b.dir,
      b.x + b.w / 2,
      b.y + b.h / 2,
      sx,
      sy,
      BULLET_ALIGN_NEXT_CELL,
    )
    if (dist < 0 || dist > range) continue
    // Track the closest threat. Sorting was only needed to pick threats[0];
    // a running min replaces the sort + array entirely.
    if (dist < threatDist) {
      threatDist = dist
      threatDir = b.dir
      hasThreat = true
    }
  }

  // Reuse the allTanks buffer directly instead of building a filtered `others`
  // array. canStep already skips dead/spawning/self entries with the same
  // filter (`o === tank || !o.alive || o.spawnTimer > 0`), so passing `all`
  // yields identical results. This eliminates one array allocation + N pushes
  // per perceive call (~550K allocs over a 30-game batch).
  const list = all ?? world.allTanks

  // Teammate aggregates: centroid (sumX/sumY/count) replaces the
  // TeammateObservation[] array. `targetForGoal`'s spreadOut directive is the
  // only consumer, and it only computes the centroid — never per-element data.
  let teammateCount = 0
  let teammateSumX = 0
  let teammateSumY = 0
  let congestion = 0
  // Nearest live decoy (for decoy-targeting). Decoys are allies, so they are
  // excluded from the teammate aggregates (they are not fellow enemies).
  // Tracked as flat fields — no object allocation.
  let hasDecoy = false
  let decoyX = 0
  let decoyY = 0
  let nearestDecoyDist = Infinity
  for (let i = 0; i < list.length; i++) {
    const o = list[i]
    if (o === tank || !o.alive || o.spawnTimer > 0) continue
    if (o.isPlayer) continue
    const ocx = o.x + o.w / 2
    const ocy = o.y + o.h / 2
    if (o.isDecoy) {
      const d = manhattan(sx, sy, ocx, ocy)
      if (d < nearestDecoyDist) {
        nearestDecoyDist = d
        hasDecoy = true
        decoyX = ocx
        decoyY = ocy
      }
      continue
    }
    teammateCount++
    teammateSumX += ocx
    teammateSumY += ocy
    if (manhattan(sx, sy, ocx, ocy) < CELL * 8) congestion++
  }

  const openDirs: Direction[] = needOpenDirs ? computeOpenDirs(world, tank, list) : []

  return {
    selfX: sx,
    selfY: sy,
    selfDir: tank.dir,
    hasPlayer: !!(player && player.alive),
    playerX: player ? player.x + player.w / 2 : 0,
    playerY: player ? player.y + player.h / 2 : 0,
    hasBase: !!base,
    baseX: base ? base.x + CELL : 0,
    baseY: base ? base.y + CELL : 0,
    hasDecoy,
    decoyX,
    decoyY,
    hasThreat,
    threatDir,
    teammateCount,
    teammateSumX,
    teammateSumY,
    congestion,
    openDirs,
  }
}

/** Convert perception into tactical knowledge (analysis only, no decisions). */
export function analyze(
  world: World,
  tank: Tank,
  p: Perception,
  cfg: IntelligenceConfig,
): Situation {
  const maxDist = FIELD
  const distToBase = p.hasBase ? manhattan(p.selfX, p.selfY, p.baseX, p.baseY) : Infinity
  const distToPlayer = p.hasPlayer ? manhattan(p.selfX, p.selfY, p.playerX, p.playerY) : Infinity

  const losRange = cfg.predictionDepth * CELL + TANK
  const baseLOS = scanAhead(world, tank, tank.dir, losRange)
  const baseInLineOfFire = baseLOS === 'base'
  const playerInLineOfFire = baseLOS === 'player'
  const decoyInLineOfFire = baseLOS === 'decoy'
  const wallInLineOfFire = baseLOS === 'wall'

  // Path blocked by a breakable brick directly ahead toward the objective?
  const objX = p.hasBase ? p.baseX : p.playerX
  const objY = p.hasBase ? p.baseY : p.playerY
  const objDir = p.hasBase || p.hasPlayer ? dirToward(p.selfX, p.selfY, objX, objY) : tank.dir
  // The objective sits in the tank's current facing direction → the base
  // scan already covers it, so reuse it instead of re-walking the same line.
  // Result is identical to scanning objDir again (pure perf win).
  const ahead = objDir === tank.dir ? baseLOS : scanAhead(world, tank, objDir, losRange)
  const pathBlocked = ahead === 'wall'

  const baseDanger = p.hasBase ? Math.max(0, 1 - distToBase / maxDist) : 0

  return {
    distToBase,
    distToPlayer,
    baseInLineOfFire,
    playerVisible: p.hasPlayer,
    playerInLineOfFire,
    decoyInLineOfFire,
    wallInLineOfFire,
    pathBlocked,
    hasThreat: p.hasThreat,
    threatDir: p.hasThreat ? p.threatDir : null,
    baseDanger,
    teammateCount: p.teammateCount,
    congestion: p.congestion,
    openDirs: p.openDirs,
  }
}
