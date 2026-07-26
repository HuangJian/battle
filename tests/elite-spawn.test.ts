import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { GRID } from '../src/constants'
import type { TankKind } from '../src/types'
import {
  applyEliteModifier,
  resolveProfile,
  ELITE_DIMENSION,
} from '../src/config/combat'
import { DIFFICULTIES } from '../src/config/difficulty'

/**
 * Elite spawn system — follow-up for commit 70ca619 (redesigned 2026-07-26).
 *
 * Design (DECISIONS.md §29): an elite enemy *is* a commander, born at spawn
 * time. There is NO runtime commander election. The per-difficulty `eliteChance`
 * knob drives the roll in `Simulation.updateSpawning`; on success the tank gets
 * the +15% combat boost AND `level = 'commander'` + `isCommander = true`.
 *
 * Guards (AGENTS §7 / §9):
 *  - elite determinism: same RNG seed → identical elite sequence
 *  - `classic` (eliteChance 0) spawns zero elites/commanders and consumes no
 *    elite RNG
 *  - elite stat boost is exactly +15% on the kind dimension (other dims base)
 *  - a spawned elite is `level === 'commander'` AND `isCommander === true`
 *    (so it runs commander AI and broadcasts directives)
 *  - there is no second commander path: on a difficulty with eliteChance 0,
 *    no commander ever appears
 */

function makeWorld(seed: number, difficulty: string): { world: World; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(seed)
  const sim = new Simulation(world, new Input())
  world.startGame(difficulty, 'modern', 0)
  return { world, sim }
}

/** Make the base indestructible + player invincible so a scenario runs
 *  indefinitely (no early game-over), while still exercising spawning/AI.
 *  The base cells themselves stay 'base' (so `isBaseDestroyed` stays false) —
 *  we ring them with steel instead of overwriting them. */
function protectArena(world: World): void {
  const baseCells: Array<[number, number]> = []
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) {
      if (world.tileMap.grid[r][c] === 'base') baseCells.push([c, r])
    }
  }
  for (const [c, r] of baseCells) {
    for (const [dc, dr] of [
      [-1, 0],
      [1, 0],
      [0, -1],
      [0, 1],
    ]) {
      const nc = c + dc
      const nr = r + dr
      if (nc >= 0 && nc < GRID && nr >= 0 && nr < GRID) {
        const t = world.tileMap.grid[nr][nc]
        if (t === 'empty' || t === 'brick') world.tileMap.grid[nr][nc] = 'steel'
      }
    }
  }
  world.tileMap.rebuildBaseCache()
  if (world.player) world.player.shieldTimer = 1e12
}

/** Record the elite/commander sequence (spawn order) as a comparable string. */
function eliteSignature(world: World): string {
  return world.tanks
    .map((t) => `${t.kind}:${t.aiState?.level ?? ''}:${t.aiState?.isCommander ?? false}`)
    .join('|')
}

describe('Elite spawn system (elite = born commander)', () => {
  it('classic (eliteChance 0) spawns zero elites/commanders and never rolls elite', () => {
    const { world, sim } = makeWorld(12345, 'classic')
    protectArena(world)
    expect(world.difficulty.eliteChance).toBe(0)
    for (let i = 0; i < 600; i++) sim.tick()
    const commanders = world.tanks.filter((t) => t.aiState?.isCommander)
    expect(commanders.length).toBe(0)
    // No tank should carry an elite-boosted profile either.
    for (const t of world.tanks) {
      if (t.kind === 'player') continue
      const base = resolveProfile(t.kind, 0)
      expect(t.profile[ELITE_DIMENSION[t.kind]]).toBe(base[ELITE_DIMENSION[t.kind]])
    }
  })

  it('elite determinism: same RNG seed reproduces the same elite sequence', () => {
    const orig = DIFFICULTIES.chaos.eliteChance
    DIFFICULTIES.chaos.eliteChance = 1 // force elites so the sequence is non-trivial
    try {
      const seeds = [1, 7, 42, 99, 2026]
      let anyElite = 0
      for (const seed of seeds) {
        const a = makeWorld(seed, 'chaos')
        const b = makeWorld(seed, 'chaos')
        protectArena(a.world)
        protectArena(b.world)
        for (let i = 0; i < 400; i++) {
          a.sim.tick()
          b.sim.tick()
        }
        const sigA = eliteSignature(a.world)
        const sigB = eliteSignature(b.world)
        expect(sigB).toBe(sigA) // determinism
        anyElite += a.world.tanks.filter((t) => t.aiState?.isCommander).length
      }
      expect(anyElite).toBeGreaterThan(0) // the path was actually exercised
    } finally {
      DIFFICULTIES.chaos.eliteChance = orig
    }
  })

  it('a spawn-elite keeps exactly the single +15% boost and IS a commander', () => {
    const orig = DIFFICULTIES.chaos.eliteChance
    DIFFICULTIES.chaos.eliteChance = 1 // force elites
    try {
      const { world, sim } = makeWorld(555, 'chaos')
      protectArena(world)
      let found: { kind: TankKind; profile: (typeof world.tanks)[number]['profile'] } | null = null
      for (let i = 0; i < 300 && !found; i++) {
        sim.tick()
        const elite = world.tanks.find((t) => t.aiState?.isCommander)
        if (elite) {
          found = { kind: elite.kind, profile: elite.profile }
          // Born as a commander: same concept, same role.
          expect(elite.aiState?.isCommander).toBe(true)
          expect(elite.aiState?.level).toBe('commander')
        }
      }
      expect(found).not.toBeNull()
      const kind = (found as { kind: TankKind }).kind
      const dim = ELITE_DIMENSION[kind]
      const expected = applyEliteModifier(resolveProfile(kind, 0), kind)
      // Every dimension matches the single-boost profile exactly.
      expect(found!.profile[dim]).toBe(expected[dim])
      expect(found!.profile.firepower).toBe(expected.firepower)
      expect(found!.profile.projectileSpeed).toBe(expected.projectileSpeed)
      expect(found!.profile.fireControl).toBe(expected.fireControl)
      expect(found!.profile.mobility).toBe(expected.mobility)
      expect(found!.profile.armor).toBe(expected.armor)
      expect(found!.profile.special).toBe(expected.special)
    } finally {
      DIFFICULTIES.chaos.eliteChance = orig
    }
  })

  it('no election path: with eliteChance 0 no commander ever appears', () => {
    const { world, sim } = makeWorld(31415, 'classic')
    protectArena(world)
    expect(world.difficulty.eliteChance).toBe(0)
    let sawCommander = false
    for (let i = 0; i < 2000; i++) {
      sim.tick()
      if (world.tanks.some((t) => t.alive && t.aiState?.isCommander)) sawCommander = true
      if (world.state !== 'playing') world.loadStage(world.stageIndex)
      if (!world.player || !world.player.alive) world.spawnPlayer()
    }
    expect(sawCommander).toBe(false)
  })

  it('a born-commander elite broadcasts directives to its squad', () => {
    const orig = DIFFICULTIES.chaos.eliteChance
    DIFFICULTIES.chaos.eliteChance = 1 // guarantee a commander spawns
    try {
      const { world, sim } = makeWorld(2024, 'chaos')
      protectArena(world)
      let sawCommander = false
      let sawDirective = false
      for (let i = 0; i < 3600; i++) {
        sim.tick()
        const cmd = world.tanks.find((t) => t.alive && t.aiState?.isCommander)
        if (cmd) {
          sawCommander = true
          // some other tank received a directive from the commander
          if (world.tanks.some((t) => t.alive && t !== cmd && t.aiState?.directive !== 'none')) {
            sawDirective = true
          }
        }
        if (!world.player || !world.player.alive) world.spawnPlayer()
      }
      expect(sawCommander).toBe(true)
      expect(sawDirective).toBe(true)
    } finally {
      DIFFICULTIES.chaos.eliteChance = orig
    }
  })
})
