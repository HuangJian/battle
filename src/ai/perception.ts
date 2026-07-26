import type { World } from '../game/World'
import type { Tank } from '../types'
import type { Direction } from '../constants'
import { CELL, TANK, DIR_VECTORS, FIELD } from '../constants'
import { aabb, snap } from '../utils/helpers'
import type { Perception, Situation, BulletObservation, IntelligenceConfig } from './types'

/**
 * ai/perception.ts — the "eyes" of the framework.
 *
 * Perception converts the World into an observation snapshot the AI is allowed
 * to reason about. The AI never reads gameplay objects directly (AI
 * Constitution #1: "Perceive before deciding"). Situation Analysis then turns
 * observations into tactical knowledge — analysis only, no decisions.
 */

/** Manhattan distance between two tank centers (px). */
export function manhattan(ax: number, ay: number, bx: number, by: number): number {
  return Math.abs(ax - bx) + Math.abs(ay - by)
}

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
export function canStep(world: World, tank: Tank, dir: Direction, considerTanks: boolean): boolean {
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
    const all = world.allTanks
    for (let i = 0; i < all.length; i++) {
      const o = all[i]
      if (o === tank || !o.alive || o.spawnTimer > 0) continue
      if (aabb(sx, sy, sw, sh, o.x, o.y, o.w, o.h)) return false
    }
  }
  return true
}

/** Result of scanning ahead along a direction. */
export interface ScanResult {
  hit: 'none' | 'base' | 'player' | 'wall' | 'steel'
  dist: number
}

/**
 * Step along `dir` (in CELL increments) up to `maxDist` px and report the
 * first thing an enemy bullet would strike: base, player, brick (wall),
 * steel, or nothing. Forest/water/ice do NOT block bullets (TileMap), so they
 * are skipped. Other enemy tanks never block enemy bullets, so they are
 * ignored — only the player tank and terrain matter for an enemy's line of
 * fire.
 */
export function scanAhead(world: World, tank: Tank, dir: Direction, maxDist: number): ScanResult {
  const v = DIR_VECTORS[dir]
  const sx = tank.x + tank.w / 2
  const sy = tank.y + tank.h / 2
  const player = world.player
  for (let d = CELL; d <= maxDist; d += CELL) {
    const cx = sx + v.dx * d
    const cy = sy + v.dy * d
    const col = Math.floor(cx / CELL)
    const row = Math.floor(cy / CELL)
    const tt = world.tileMap.get(col, row)
    if (tt === 'base') return { hit: 'base', dist: d }
    if (tt === 'brick') return { hit: 'wall', dist: d }
    if (tt === 'steel') return { hit: 'steel', dist: d }
    if (
      player &&
      player.alive &&
      aabb(cx - 1, cy - 1, 2, 2, player.x, player.y, player.w, player.h)
    ) {
      return { hit: 'player', dist: d }
    }
  }
  return { hit: 'none', dist: maxDist }
}

/** Build the observation snapshot for one tank. */
export function perceive(world: World, tank: Tank, cfg: IntelligenceConfig): Perception {
  const sx = tank.x + tank.w / 2
  const sy = tank.y + tank.h / 2
  const player = world.player
  const base = world.tileMap.getBasePos()

  const threats: BulletObservation[] = []
  const range = cfg.predictionDepth * CELL
  for (const b of world.bullets) {
    if (!b.alive || !b.isPlayer) continue // only player bullets threaten enemies
    const bx = b.x + b.w / 2
    const by = b.y + b.h / 2
    const vertical = b.dir === 'up' || b.dir === 'down'
    const aligned = vertical ? Math.abs(bx - sx) < CELL * 0.75 : Math.abs(by - sy) < CELL * 0.75
    if (!aligned) continue
    const approaching =
      (b.dir === 'down' && by < sy) ||
      (b.dir === 'up' && by > sy) ||
      (b.dir === 'right' && bx < sx) ||
      (b.dir === 'left' && bx > sx)
    if (!approaching) continue
    const dist = vertical ? Math.abs(by - sy) : Math.abs(bx - sx)
    if (dist > range) continue
    threats.push({ x: bx, y: by, dir: b.dir, aligned: true, approaching: true, distance: dist })
  }
  threats.sort((a, b) => a.distance - b.distance)

  const teammates: Perception['teammates'] = []
  let congestion = 0
  const all = world.allTanks
  for (let i = 0; i < all.length; i++) {
    const o = all[i]
    if (o === tank || !o.alive || o.spawnTimer > 0 || o.isPlayer) continue
    teammates.push({ id: o.id, x: o.x + o.w / 2, y: o.y + o.h / 2, dir: o.dir })
    if (manhattan(sx, sy, o.x + o.w / 2, o.y + o.h / 2) < CELL * 8) congestion++
  }

  const openDirs: Direction[] = []
  const dirs: Direction[] = ['up', 'down', 'left', 'right']
  for (const d of dirs) {
    if (canStep(world, tank, d, true)) openDirs.push(d)
  }

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
    threats,
    teammates,
    congestion,
    openDirs,
  }
}

/** Convert perception into tactical knowledge (analysis only, no decisions). */
export function analyze(world: World, tank: Tank, p: Perception, cfg: IntelligenceConfig): Situation {
  const maxDist = FIELD
  const distToBase = p.hasBase ? manhattan(p.selfX, p.selfY, p.baseX, p.baseY) : Infinity
  const distToPlayer = p.hasPlayer ? manhattan(p.selfX, p.selfY, p.playerX, p.playerY) : Infinity

  const losRange = cfg.predictionDepth * CELL + TANK
  const baseLOS = scanAhead(world, tank, tank.dir, losRange)
  const baseInLineOfFire = baseLOS.hit === 'base'
  const playerInLineOfFire = baseLOS.hit === 'player'
  const wallInLineOfFire = baseLOS.hit === 'wall'

  // Path blocked by a breakable brick directly ahead toward the objective?
  const objX = p.hasBase ? p.baseX : p.playerX
  const objY = p.hasBase ? p.baseY : p.playerY
  const objDir = p.hasBase || p.hasPlayer ? dirToward(p.selfX, p.selfY, objX, objY) : tank.dir
  const ahead = scanAhead(world, tank, objDir, losRange)
  const pathBlocked = ahead.hit === 'wall'

  const threat = p.threats.length > 0 ? p.threats[0] : null
  const threatDir: Direction | null = null // computed by the decision layer (needs objective)

  const baseDanger = p.hasBase ? Math.max(0, 1 - distToBase / maxDist) : 0

  return {
    distToBase,
    distToPlayer,
    baseInLineOfFire,
    playerVisible: p.hasPlayer,
    playerInLineOfFire,
    wallInLineOfFire,
    pathBlocked,
    threat,
    threatDir,
    baseDanger,
    teammateCount: p.teammates.length,
    congestion: p.congestion,
    openDirs: p.openDirs,
  }
}
