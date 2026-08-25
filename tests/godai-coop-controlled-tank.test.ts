import { seedWorld } from './helpers'
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput } from '../src/ai/GodAIInput'
import { RNG } from '../src/utils/RNG'
import { CELL, GRID, TANK, DIR_VECTORS, type Direction } from '../src/constants'
import { snap } from '../src/utils/helpers'
import type { Tank } from '../src/types'

/**
 * §79 — co-op God AI must reason about the tank it actually drives.
 *
 * Reported from `classic-s15-clear-l2-t105-seed1785585133360.repaired.replay`:
 * after respawning at 00:53, player 2 (God AI) sat one cell from its spawn
 * point from 00:56 until 01:41 pressing `left` into the base wall, and along
 * the way blew two base-protection bricks out of the wall.
 *
 * ROOT CAUSE: the god/* sub-modules read `world.player` (P1) instead of
 * `self.controlledTank(world)` (P2) for every passability / wall-breaking
 * test, while the path START came from `playerCell()` (correctly P2). So the
 * AI planned from P2's cell but validated the move against P1's surroundings.
 * With the human parked at a spot where `left` was open and `up` was blocked,
 * the AI emitted `left` forever — the one direction P2 could not take — and
 * `canMoveOrBreak`'s base-protection-brick guard (also position-relative)
 * cleared P2 to dig through the base wall.
 *
 * These tests pin the invariant: every movement/fire decision must be
 * evaluated against the controlled tank's own position and stats.
 */

// ---------------------------------------------------------------- helpers

/** Grid cells occupied by the base itself. */
const BASE_CELLS: Array<[number, number]> = [
  [12, 24],
  [13, 24],
  [12, 25],
  [13, 25],
]

/** The brick ring that protects the base (cols 11..14, rows 23..25). */
function baseWallCells(): Array<[number, number]> {
  const out: Array<[number, number]> = []
  for (let r = 23; r <= 25; r++) {
    for (let c = 11; c <= 14; c++) {
      if (BASE_CELLS.some(([bc, br]) => bc === c && br === r)) continue
      out.push([c, r])
    }
  }
  return out
}

/** Empty arena + base + its brick wall ring. */
function baseArena(world: World): void {
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  for (const [c, r] of BASE_CELLS) world.tileMap.grid[r][c] = 'base'
  for (const [c, r] of baseWallCells()) world.tileMap.grid[r][c] = 'brick'
  world.tileMap.rebuildBaseCache()
  world.state = 'playing'
}

/**
 * Build the reported situation: co-op world, God AI on P2, P2 wedged between
 * the right base wall and the bottom edge, P1 parked where `left` is open and
 * `up` is blocked (so the buggy code is actively misled).
 */
function coopStandoff(seed: number): {
  world: World
  sim: Simulation
  god: GodAIInput
  p2: Tank
} {
  const world = seedWorld(seed)
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)
  baseArena(world)

  world.coop = true
  world.lives2 = 3
  world.playerLevel2 = 0

  // --- P1: parked at (160,288) = cell (10,18). `up` blocked by steel,
  //     `left` wide open. This is what the buggy code read instead of P2. ---
  world.player!.x = 160
  world.player!.y = 288
  world.player!.shieldTimer = 0
  for (const c of [10, 11]) world.tileMap.grid[17][c] = 'steel'

  // --- P2: at (240,384) = cell (15,24), exactly where the replay jammed.
  //     left  -> col 14 base-protection brick (blocked, must NOT be shot)
  //     down  -> out of bounds
  //     up/right -> open
  world.player2SpawnPoint = { col: 16, row: 24 }
  world.spawnPlayer2()
  const p2 = world.player2!
  p2.x = 240
  p2.y = 384
  p2.dir = 'left'
  p2.shieldTimer = 0

  const god = new GodAIInput(world, undefined, new RNG(seed ^ 0xdeadbeef), (w) => w.player2)
  god.reset()
  sim.input2 = god

  // Give the AI something to chase: two enemies in the upper half.
  for (const [x, y] of [
    [64, 64],
    [320, 96],
  ]) {
    const t = world.createTank('basic', x, y, 'down')
    t.spawnTimer = 0
    world.tanks.push(t)
  }
  world.enemiesSpawned = 2

  return { world, sim, god, p2 }
}

/** Run the sim, keeping the stage alive, and track P2's travelled span. */
function runCoop(
  world: World,
  sim: Simulation,
  god: GodAIInput,
  ticks: number,
): { spanX: number; spanY: number } {
  let minX = Infinity
  let maxX = -Infinity
  let minY = Infinity
  let maxY = -Infinity
  for (let i = 0; i < ticks; i++) {
    sim.tick()
    god.endFrame()
    if (world.state !== 'playing') world.state = 'playing'
    const p2 = world.player2
    if (p2?.alive) {
      minX = Math.min(minX, p2.x)
      maxX = Math.max(maxX, p2.x)
      minY = Math.min(minY, p2.y)
      maxY = Math.max(maxY, p2.y)
    }
  }
  return { spanX: maxX - minX, spanY: maxY - minY }
}

/** Mirrors GodAIInput.isBaseProtectionBrick's geometry (T6). */
function isBaseProtection(col: number, row: number): boolean {
  const dc = Math.abs(col - 12)
  const dr = Math.abs(row - 24)
  return dc <= 4 && dr <= 4 && (dc <= 2 || dr <= 2)
}

/** Ray-cast a bullet forward and report the first blocking terrain cell. */
function firstTerrainHit(
  world: World,
  cx: number,
  cy: number,
  dir: Direction,
): { type: string; col: number; row: number } | null {
  const v = DIR_VECTORS[dir]
  for (let d = 0; d <= GRID * CELL; d += CELL / 2) {
    const col = Math.floor((cx + v.dx * d) / CELL)
    const row = Math.floor((cy + v.dy * d) / CELL)
    if (col < 0 || col >= GRID || row < 0 || row >= GRID) return null
    const t = world.tileMap.grid[row][col]
    if (t === 'brick' || t === 'steel' || t === 'base') return { type: t, col, row }
  }
  return null
}

/** Ground truth: can this tank physically advance one cell in `dir`? */
function reallyPassable(world: World, t: Tank, dir: Direction): boolean {
  const v = DIR_VECTORS[dir]
  const nx = snap(t.x, CELL) + v.dx * CELL
  const ny = snap(t.y, CELL) + v.dy * CELL
  if (!world.isInBounds(nx, ny, TANK, TANK)) return false
  if (world.rectHitsTerrain(nx, ny, TANK, TANK)) return false
  for (const o of world.allTanks) {
    if (o === t || !o.alive) continue
    if (nx < o.x + o.w && o.x < nx + TANK && ny < o.y + o.h && o.y < ny + TANK) return false
  }
  return true
}

// ---------------------------------------------------------------- tests

describe('§79 co-op God AI drives the tank it controls', () => {
  it('P2 does not stall against the base wall (replay 00:56-01:41 repro)', () => {
    const { world, sim, god } = coopStandoff(1785585133360 & 0xffff)

    // Sanity: the standoff is set up as reported — P1 and P2 disagree.
    expect(reallyPassable(world, world.player!, 'left')).toBe(true)
    expect(reallyPassable(world, world.player!, 'up')).toBe(false)
    expect(reallyPassable(world, world.player2!, 'left')).toBe(false)
    expect(reallyPassable(world, world.player2!, 'up')).toBe(true)

    // 600 ticks = 10s. The replay showed 45s of no movement, so 10s of
    // freedom is a generous but decisive bar.
    const { spanX, spanY } = runCoop(world, sim, god, 600)

    // It must leave the pocket, not shuffle inside one cell.
    expect(spanX + spanY).toBeGreaterThan(CELL * 4)
  })

  it('P2 never fires at the base wall', () => {
    // Enemies legitimately shell the base wall, so wall integrity alone is
    // not the invariant. What must never happen is P2 aiming its OWN shots
    // at the base or its protection bricks — that is what dug the hole at
    // 01:22 in the replay. Attribute by bullet owner instead.
    const { world, sim, god } = coopStandoff(4242)
    const p2id = world.player2!.id
    const seen = new Set<number>()
    const violations: string[] = []

    for (let i = 0; i < 900; i++) {
      sim.tick()
      god.endFrame()
      if (world.state !== 'playing') world.state = 'playing'
      for (const b of world.bullets) {
        if (!b.alive || b.ownerId !== p2id || seen.has(b.id)) continue
        seen.add(b.id)
        const hit = firstTerrainHit(world, b.x + b.w / 2, b.y + b.h / 2, b.dir)
        if (hit && (hit.type === 'base' || isBaseProtection(hit.col, hit.row))) {
          violations.push(`t${i}: P2 shot ${hit.type} at (${hit.col},${hit.row})`)
        }
      }
    }

    // Not vacuous: P2 must actually have been shooting during the window.
    expect(seen.size).toBeGreaterThan(0)
    expect(violations).toEqual([])
    // NOTE: no assertion on overall base survival here — P1 is parked by
    // construction, so the enemies are free to shell the wall themselves.
    // The invariant under test is strictly "P2 does not shoot its own base".
  })

  it('movement decisions are evaluated against P2, not world.player', () => {
    const { world, god, p2 } = coopStandoff(7)

    // Drive one decision cycle and read the direction the AI wants.
    god.endFrame()
    const dir = god.getMoveDirection()

    // The bug emitted `left` — passable for P1, a base wall for P2.
    expect(dir).not.toBe('left')
    if (dir) {
      // Whatever it picked must be something P2 can actually act on:
      // either physically passable, or a legitimately breakable wall.
      const ok = reallyPassable(world, p2, dir) || god.canMoveOrBreak(p2, dir)
      expect(ok).toBe(true)
    }
  })

  it('canMoveOrBreak refuses the base wall for P2 even when P1 is clear', () => {
    const { world, god } = coopStandoff(11)
    // P1 sits in open ground: breaking "left" is fine for it...
    expect(god.canMoveOrBreak(world.player!, 'left')).toBe(true)
    // ...but P2's left is a base-protection brick and must stay off-limits.
    expect(god.canMoveOrBreak(world.player2!, 'left')).toBe(false)
  })

  it('single-player God AI still controls world.player (parity)', () => {
    const world = seedWorld(3)
    const sim = new Simulation(world, new Input())
    world.startGame('classic', 'modern', 0)
    baseArena(world)

    const god = new GodAIInput(world, undefined, new RNG(3))
    god.reset()
    sim.input = god

    // Default controlledTank must resolve to P1.
    expect(god.controlledTank(world)).toBe(world.player)

    world.player!.x = 160
    world.player!.y = 288
    world.player!.shieldTimer = 0
    const t = world.createTank('basic', 64, 64, 'down')
    t.spawnTimer = 0
    world.tanks.push(t)
    world.enemiesSpawned = 1

    let minX = Infinity
    let maxX = -Infinity
    let minY = Infinity
    let maxY = -Infinity
    for (let i = 0; i < 300; i++) {
      sim.tick()
      god.endFrame()
      if (world.state !== 'playing') world.state = 'playing'
      const p = world.player
      if (p?.alive) {
        minX = Math.min(minX, p.x)
        maxX = Math.max(maxX, p.x)
        minY = Math.min(minY, p.y)
        maxY = Math.max(maxY, p.y)
      }
    }
    expect(maxX - minX + (maxY - minY)).toBeGreaterThan(CELL)
  })
})
