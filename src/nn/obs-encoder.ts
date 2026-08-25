/**
 * obs-encoder.ts — observation encoder (plan §1, NN-M0a).
 *
 * SINGLE SOURCE OF TRUTH for the NN observation. Both the bun exporter
 * (`tools/replay/export-observations.ts`) and the browser runtime inference
 * (`src/nn/infer.ts`) import this module, so the bytes are guaranteed
 * identical (plan §1.4). The Python trainer NEVER re-encodes — it only
 * consumes the exported npy shards.
 *
 * Invariants (plan §1.4 / AGENTS §2.2):
 *   * Deterministic: same World + same tick -> identical obs bytes.
 *   * Read-only: never mutates the World. Never consumes world.rng.
 *   * No per-tick allocation on the hot path (reused buffers).
 *   * Does NOT read the NN `held` slots (nnMoveHeld/...); those live on the
 *     World but are Input-private state (plan §1.3-5, nn2 N4).
 *
 * Keep this file in lock-step with `nn-training/schema.py`. Any channel /
 * scalar / action change bumps OBS_SCHEMA_MAJOR and forces a full re-export.
 */

import {
  GRID,
  CELL,
  TANK,
  BASE_POS,
  ENEMY_SPAWNS,
  TICK_MS,
  MAX_ENEMIES_ALIVE,
  Direction,
} from '../constants'
import type { World } from '../game/World'
import type { PowerUpType, Tank } from '../types'
import {
  RING_CELLS,
  ticksUntilFire,
  ticksUntilLegalTurn,
  killAssessment,
  enemyDeadline,
} from '../ai/god/ThreatBudget'

// ---- Canonical dimensions (mirror nn-training/schema.py) ----
export const OBS_CHANNELS = 14
export const BOARD = GRID // 26
export const SCALAR_DIM = 19
export const OBS_SCHEMA_MAJOR = 2

// ---- Channel index map (plan §1.1) ----
export const CH = {
  terrainBrick: 0,
  terrainSteel: 1,
  terrainWater: 2,
  terrainForest: 3,
  terrainIce: 4,
  base: 5, // eagle (=2) + ring cells (=1)
  self: 6, // player tank
  enemyBasic: 7,
  enemyFast: 8,
  enemyPower: 9,
  enemyArmor: 10,
  bullet: 11, // enemy bullet 1-4, player bullet 5-8
  powerup: 12, // on-field, value = 1 + enumIndex
  waveHeat: 13, // projected spawns in next K ticks per spawn point
} as const

// ---- PowerUpType declaration order (src/types.ts:20-38) ----
export const POWERUP_ORDER: PowerUpType[] = [
  'star',
  'bomb',
  'shield',
  'freeze',
  'tank',
  'fence',
  'boat',
  'guard',
  'frenzy',
  'sacrifice',
  'rewind',
  'repair',
  'emp',
  'decoy',
  'mine',
]
const POWERUP_ENUM = new Map<PowerUpType, number>(POWERUP_ORDER.map((p, i) => [p, i]))
const POWERUP_COUNT = POWERUP_ORDER.length // 15 (asserted below)

// ---- Enemy kind -> channel offset (relative to CH.enemyBasic) ----
const KIND_INDEX: Record<string, number> = { basic: 0, fast: 1, power: 2, armor: 3 }
// ---- Intelligence tier -> tierCode (plan §1.1 ch7-10) ----
const TIER_INDEX: Record<string, number> = {
  none: 0,
  rookie: 1,
  soldier: 2,
  veteran: 3,
  commander: 4,
}
// ---- Direction -> dirIdx (constants.ts DIR_DX/DY order) ----
const DIR_INDEX: Record<Direction, number> = { up: 0, down: 1, left: 2, right: 3 }

// Scalar indices that flip sign under mirrorX (relative-direction x-components).
// v2 (OBS_SCHEMA_MAJOR=2): item inventory scalars removed (24→19), the two
// rel-x components renumbered [20,23] → [15,18].
export const SCALAR_X_INDICES = [15, 18]

if (POWERUP_COUNT !== 15) {
  throw new Error(`POWERUP_ORDER must have exactly 15 members, got ${POWERUP_COUNT}`)
}

const WAVE_HEAT_TICKS = 600 // K = 600 ticks (10s), plan §1.1 ch13

function clamp01(x: number): number {
  return x < 0 ? 0 : x > 1 ? 1 : x
}

/**
 * Observation encoder. Reuses its internal obs (Uint8 14*26*26) and scalar
 * (Float32 24) buffers across calls — the caller must COPY out what it needs
 * (the exporter does, into the npy shard).
 */
export class ObsEncoder {
  readonly obs: Uint8Array = new Uint8Array(OBS_CHANNELS * BOARD * BOARD)
  readonly scalars: Float32Array = new Float32Array(SCALAR_DIM)

  encode(world: World): void {
    this.obs.fill(0)
    this.scalars.fill(0)
    this.encodeTerrain(world)
    this.encodeBase(world)
    this.encodeSelf(world)
    this.encodeEnemies(world)
    this.encodeBullets(world)
    this.encodePowerups(world)
    this.encodeWaveHeat(world)
    this.encodeScalars(world)
  }

  // ---- spatial channels ----

  private setCell(ch: number, col: number, row: number, val: number): void {
    if (col < 0 || col >= BOARD || row < 0 || row >= BOARD) return
    this.obs[ch * BOARD * BOARD + row * BOARD + col] = val
  }

  /** Write `val` into every cell the (x,y,w,h) box covers (plan §1.1 格锚点). */
  private writeBox(ch: number, x: number, y: number, w: number, h: number, val: number): void {
    const c0 = Math.floor(x / CELL)
    const r0 = Math.floor(y / CELL)
    const c1 = Math.floor((x + w - 1) / CELL)
    const r1 = Math.floor((y + h - 1) / CELL)
    for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) this.setCell(ch, c, r, val)
  }

  private encodeTerrain(world: World): void {
    const tm = world.tileMap.grid
    for (let r = 0; r < BOARD; r++) {
      const row = tm[r]
      for (let c = 0; c < BOARD; c++) {
        const t = row[c]
        switch (t) {
          case 'brick':
            this.setCell(CH.terrainBrick, c, r, 1)
            break
          case 'steel':
            this.setCell(CH.terrainSteel, c, r, 1)
            break
          case 'water':
            this.setCell(CH.terrainWater, c, r, 1)
            break
          case 'forest':
            this.setCell(CH.terrainForest, c, r, 1)
            break
          case 'ice':
            this.setCell(CH.terrainIce, c, r, 1)
            break
          // 'base' and 'empty' -> 0 in these 5 channels (ch5 handles base)
        }
      }
    }
  }

  private encodeBase(world: World): void {
    const bc = BASE_POS.col
    const br = BASE_POS.row
    // Eagle cell: 2 if still alive, 0 if destroyed.
    this.setCell(CH.base, bc, br, world.tileMap.isBaseDestroyed() ? 0 : 2)
    // Ring cells: 1 if the protective brick/steel is still present.
    for (const cell of RING_CELLS) {
      const t = world.tileMap.get(cell.col, cell.row)
      if (t === 'brick' || t === 'steel') this.setCell(CH.base, cell.col, cell.row, 1)
    }
  }

  private encodeSelf(world: World): void {
    const p = world.player
    if (!p || !p.alive) return
    const star = Math.min(p.level ?? 0, 3) // plan §1.1 ch6: truncate star at 3
    const d = DIR_INDEX[p.dir]
    const val = (star << 3) | (d + 1) // range 1-28
    this.writeBox(CH.self, p.x, p.y, TANK, TANK, val)
  }

  private encodeEnemies(world: World): void {
    // Only allegiance 'enemy' tanks are encoded; 'ally' (guard) is v1-excluded
    // (plan nn3 N7). Spawn-in-progress (spawnTimer>0) are not yet active.
    for (const t of world.tanks) {
      if (!t.alive || t.spawnTimer > 0) continue
      if (t.allegiance !== 'enemy') continue
      const kindOffset = KIND_INDEX[t.kind]
      if (kindOffset === undefined) continue
      const tier = TIER_INDEX[t.aiState?.level ?? 'none'] ?? 0
      const d = DIR_INDEX[t.dir]
      const val = (tier << 3) | (d + 1) // range 1-36
      this.writeBox(CH.enemyBasic + kindOffset, t.x, t.y, TANK, TANK, val)
    }
  }

  private encodeBullets(world: World): void {
    for (const b of world.bullets) {
      if (!b.alive) continue
      if (b.allegiance === 'ally') continue // plan nn3 N7: ignore ally bullets
      const d = DIR_INDEX[b.dir]
      const val = b.allegiance === 'enemy' ? d + 1 : d + 1 + 4 // 1-4 enemy, 5-8 player
      // Bullet is ~6px: write its CENTER cell (plan §1.1 格锚点).
      const cc = Math.floor((b.x + b.w / 2) / CELL)
      const cr = Math.floor((b.y + b.h / 2) / CELL)
      this.setCell(CH.bullet, cc, cr, val)
    }
  }

  private encodePowerups(world: World): void {
    for (const pu of world.powerUps) {
      if (!pu.alive) continue
      const idx = POWERUP_ENUM.get(pu.type)
      if (idx === undefined) continue
      const val = idx + 1 // 1-15
      const c = Math.floor(pu.x / CELL)
      const r = Math.floor(pu.y / CELL)
      this.setCell(CH.powerup, c, r, val)
    }
  }

  private encodeWaveHeat(world: World): void {
    // Projected spawns in the next K=600 ticks (plan §1.1 ch13).
    // Approximation (documented): bounded by the spawn INTERVAL (not the raw
    // queue length, which overestimates), capped by enemies still unspawned.
    const remaining = world.spawnQueue.length
    const intervalMs = world.rules?.spawnIntervalMs ?? 1500
    const projK = Math.floor((WAVE_HEAT_TICKS * TICK_MS) / intervalMs)
    const proj = Math.max(0, Math.min(remaining, projK))
    // Distribute round-robin across the 3 spawn points so the summed heat
    // equals `proj` (matches the true projected-spawn total, not 3x).
    const counts = [0, 0, 0]
    for (let i = 0; i < proj; i++) counts[i % 3]++
    const points = world.enemySpawnPoints
    for (let i = 0; i < 3; i++) {
      const px = points[i]?.x ?? ENEMY_SPAWNS[i].col * CELL
      const py = points[i]?.y ?? ENEMY_SPAWNS[i].row * CELL
      this.setCell(CH.waveHeat, Math.floor(px / CELL), Math.floor(py / CELL), counts[i])
    }
  }

  // ---- scalar vector (plan §1.2) ----

  private encodeScalars(world: World): void {
    const s = this.scalars
    const p = world.player
    const FIELD_DIAG = 26 * CELL * 1.5

    // slack / baseDeadline via ThreatBudget (reused, plan §1.2).
    let minKillSlack = Infinity
    let minBaseDeadline = Infinity
    const enemies = world.tanks.filter(
      (t) => t.alive && t.allegiance === 'enemy' && t.spawnTimer <= 0,
    )
    for (const e of enemies) {
      const ka = killAssessmentSlack(world, p, e)
      if (ka.killSlack < minKillSlack) minKillSlack = ka.killSlack
      if (ka.baseDeadline < minBaseDeadline) minBaseDeadline = ka.baseDeadline
    }
    s[0] = enemies.length === 0 ? 1 : clamp01(minKillSlack / 600)
    s[1] = enemies.length === 0 ? 1 : clamp01(minBaseDeadline / 600)

    // lives / level
    s[2] = clamp01(world.lives / 3)
    s[3] = clamp01(Math.min(p?.level ?? 0, 3) / 3)

    // fire-control progress
    if (p && p.nextFireInterval > 0) {
      const now = world.frame * TICK_MS
      s[4] = clamp01((now - (p.lastFire ?? 0)) / p.nextFireInterval)
    } else {
      s[4] = 0
    }

    // turn-cooldown remaining
    const cd = world.rules?.turnCooldownMs ?? 0
    if (p && cd > 0) {
      const now = world.frame * TICK_MS
      const elapsed = now - (p.lastTurnMs ?? -9999)
      s[5] = clamp01((cd - elapsed) / cd)
    } else {
      s[5] = 0
    }

    // ring completeness
    let intact = 0
    for (const cell of RING_CELLS) {
      const t = world.tileMap.get(cell.col, cell.row)
      if (t === 'brick' || t === 'steel') intact++
    }
    s[6] = intact / RING_CELLS.length

    // enemies on field / spawn queue remaining
    s[7] = clamp01(world.enemyCount / MAX_ENEMIES_ALIVE)
    s[8] = clamp01(world.spawnQueue.length / Math.max(1, world.enemiesTotal))

    // tier composition
    const tierCounts = [0, 0, 0, 0, 0]
    for (const e of enemies) {
      const ti = TIER_INDEX[e.aiState?.level ?? 'none'] ?? 0
      tierCounts[ti]++
    }
    const denom = Math.max(1, enemies.length)
    for (let i = 0; i < 5; i++) s[9 + i] = tierCounts[i] / denom

    // nearest enemy relative (dist, dx/dist, dy/dist)
    const pc = p ? { x: p.x + p.w / 2, y: p.y + p.h / 2 } : null
    let nd = Infinity
    let ndx = 0
    let ndy = 0
    if (pc) {
      for (const e of enemies) {
        const ex = e.x + e.w / 2
        const ey = e.y + e.h / 2
        const dx = ex - pc.x
        const dy = ey - pc.y
        const d = Math.hypot(dx, dy)
        if (d < nd) {
          nd = d
          ndx = dx
          ndy = dy
        }
      }
    }
    if (pc && nd < Infinity) {
      s[14] = clamp01(nd / FIELD_DIAG)
      s[15] = ndx / nd
      s[16] = ndy / nd
    } else {
      s[14] = 0
      s[15] = 0
      s[16] = 0
    }

    // nearest base relative
    const bcx = BASE_POS.col * CELL + CELL / 2
    const bcy = BASE_POS.row * CELL + CELL / 2
    if (pc) {
      const dx = bcx - pc.x
      const dy = bcy - pc.y
      const d = Math.hypot(dx, dy) || 1
      s[17] = clamp01(d / FIELD_DIAG)
      s[18] = dx / d
    } else {
      s[17] = 0
      s[18] = 0
    }
  }
}

// ---------------------------------------------------------------
// ThreatBudget-derived scalars (reused, no duplication). Thin wrappers
// around ThreatBudget so the encoder stays a pure function of (world).
// ---------------------------------------------------------------

function killAssessmentSlack(world: World, p: Tank | null, e: Tank) {
  if (!p) return { killSlack: Infinity, baseDeadline: Infinity }
  const ka = killAssessment(world, p, e)
  const dl = enemyDeadline(world, e)
  return { killSlack: ka.killSlack, baseDeadline: dl.enemyDamageDeadline }
}

// ---------------------------------------------------------------
// Decision-tick predicate (plan §1.3) + action/mask helpers.
// Exported so the exporter AND the unit tests share one implementation.
// ---------------------------------------------------------------

/** Fire-control EDGE: the tick the cooldown just elapsed (plan §1.3, nn3 N1). */
export function isFireEdge(world: World): boolean {
  const p = world.player
  if (!p) return false
  const iv = p.nextFireInterval ?? 0
  if (!(iv > 0)) return false
  const now = world.frame * TICK_MS
  const last = p.lastFire ?? -9999
  const ready = now - last >= iv
  const prevReady = now - TICK_MS - last >= iv
  return ready && !prevReady
}

/**
 * Event-type decision tick (plan §1.3).
 *   turn-event  : direction changed vs previous frame
 *   fireEdge    : cooldown just elapsed
 *   item-event  : guard/frenzy bit changed vs previous frame
 *   subsample   : t % k === 0  (k=10) — keeps "do nothing" negative samples
 * Priority for `condition`: turn > fire > item > subsample.
 */
export function decisionTick(
  t: number,
  world: World,
  prevDir: Direction | null,
  curDir: Direction | null,
  prevGuard: boolean,
  curGuard: boolean,
  prevFrenzy: boolean,
  curFrenzy: boolean,
  k = 10,
): { isDecision: boolean; condition: number } {
  const turnEvent = prevDir !== null && prevDir !== curDir
  const fireEdge = isFireEdge(world)
  const itemEvent = prevGuard !== curGuard || prevFrenzy !== curFrenzy
  const subsample = t % k === 0
  const isDecision = turnEvent || fireEdge || itemEvent || subsample
  let condition = 3
  if (turnEvent) condition = 0
  else if (fireEdge) condition = 1
  else if (itemEvent) condition = 2
  return { isDecision, condition }
}

export interface FrameLabel {
  move: number // 0 none,1 up,2 down,3 left,4 right
  fire: number // 0 release,1 hold
}

/** Map a packed human input frame to the 2 action-head labels (v2: no item head). */
export function actionFromFrame(f: { direction: Direction | null; firing: boolean }): FrameLabel {
  const move = f.direction ? DIR_INDEX[f.direction] + 1 : 0
  const fire = f.firing ? 1 : 0
  return { move, fire }
}

export interface Masks {
  move: number[] // length 5
  fire: number[] // length 2  ([release valid, hold valid])
}

/**
 * Invalid-action mask (plan §1.3-4), generated from World state.
 *   fire : hold-mask valid only when cooldown elapsed (else masked).
 *   move : v1 = all valid (turn-lock refinement deferred; noted in plan).
 * v2: item head removed — guard/frenzy (and their masks) no longer exist.
 */
export function computeMasks(world: World): Masks {
  const p = world.player
  const ready = p ? isFireReady(world) : false
  const fire: number[] = [1, ready ? 1 : 0]
  const move: number[] = [1, 1, 1, 1, 1]
  return { move, fire }
}

function isFireReady(world: World): boolean {
  const p = world.player
  if (!p) return false
  const iv = p.nextFireInterval ?? 0
  if (!(iv > 0)) return false
  return world.frame * TICK_MS - (p.lastFire ?? -9999) >= iv
}

export { ticksUntilFire, ticksUntilLegalTurn }
