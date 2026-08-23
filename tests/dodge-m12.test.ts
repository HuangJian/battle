import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { dodgeDirectionImpl } from '../src/ai/god/ThreatAssessor'
import { CELL, BULLET } from '../src/constants'
import type { Bullet } from '../src/types'
import type { Direction } from '../src/constants'
import { clearArena, makeBullet as makeBulletShared } from './helpers'

/**
 * M12 (DECISIONS §112): player HP buffer awareness — unit tests.
 *
 * Locks the HP-adaptive dodge commit gate inside the M9/M10 horizon block:
 *   danger mode (hits-to-die <= hpDangerHits) RELAXES the commit margin to
 *   hpDangerCommitMargin (escape = survival at low HP);
 *   trade mode (hits-to-die >= hpTradeHits) TIGHTENS it by
 *   hpTradeCommitPenalty (accept partial dodges at high HP).
 * Pool-model only ('instant'/classic has no HP buffer). Default
 * playerHpAwareness=0 → byte-identical (margin unchanged).
 *
 * Geometry: player center at (col 4, row 10) = (80, 176). Base at cols
 * 12-13 / rows 24-25 → 'right' is base-closer. Threat bullet traveling DOWN
 * 160px above (tArr = 40). A brick wall at col 7 (rows 9-11) caps the FREE
 * PATH in 'right' to 16px < the 19px band → hB = -tArr (doomed side), while
 * 'left' is open → hA = tArr - escapeTicks ≈ 23 (winnable). Terrain (not a
 * bullet) is used to doom the right side because an enemy bullet there would
 * also flip isSafeDir('right')=false in the LEGACY path (which must return
 * the base-closer 'right' for the control arms to differ from the commit).
 * canMoveDir('right') stays passable: the destination rect (cols 5-6) is
 * clear; only the second free-path step (col 7) is blocked.
 *
 * So: horizon commit → 'left' (longer-horizon side); legacy binary path →
 * 'right' (base-closer among the two safe perpendiculars).
 */

function setupWorld(difficulty: 'classic' | 'hard'): { world: World; input: GodAIInput } {
  const world = new World()
  world.rng = new RNG(42)
  // Explicit clone (NOT the DEFAULT singleton): mutating input.params must
  // not leak into DEFAULT_GOD_AI_PARAMS (cross-file module state is shared in
  // bun test — DECISIONS §98).
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS })
  const sim = new Simulation(world, new Input())
  world.startGame(difficulty, 'modern', 0)
  clearArena(world)
  // Brick wall at col 7, rows 9-11: caps freeDist('right') at 16px without
  // blocking canMoveDir('right') (destination rect = cols 5-6).
  for (const r of [9, 10, 11]) world.tileMap.grid[r][7] = 'brick'
  void sim
  return { world, input }
}

// Local positional flavor → shared field-complete fixture (遗留 #5;
// 口径差异表 in tests/helpers.ts).
const makeBullet = (x: number, y: number, dir: Bullet['dir'], damage = 100): Bullet =>
  makeBulletShared({ x, y, dir, damage, ownerKind: 'basic' })

function positionPlayer(world: World, x: number, y: number, hp: number): void {
  const p = world.player!
  p.x = x
  p.y = y
  p.hp = hp
  // Clear spawn timer and shield timer so the AI can think + see threats.
  p.spawnTimer = 0
  p.shieldTimer = 0
}

const PCX = 4 * CELL + CELL // 80
const PCY = 10 * CELL + CELL // 176

function dodge(
  input: GodAIInput,
  world: World,
  overrides: Record<string, number>,
): Direction | null {
  for (const [k, v] of Object.entries(overrides)) {
    ;(input.params as unknown as Record<string, number>)[k] = v
  }
  input.hasBase = world.tileMap.hasBase()
  const threat = makeBullet(PCX - BULLET / 2, PCY - 160 - BULLET / 2, 'down')
  world.bullets.length = 0
  world.bullets.push(threat)
  return dodgeDirectionImpl(input, threat, PCX, PCY)
}

describe('M12 player HP buffer awareness', () => {
  it('default OFF: playerHpAwareness=0 leaves the horizon gate untouched (inert)', () => {
    const { world, input } = setupWorld('hard')
    positionPlayer(world, 4 * CELL, 10 * CELL, 100)
    // playerHpAwareness=0 → margin stays dodgeHorizonMinMarginTicks=100 →
    // gate fails (bestH ~23 < 100) → legacy binary path → base-closer 'right'.
    const control = dodge(input, world, {
      dodgeHorizonScore: 1,
      dodgeHorizonMinMarginTicks: 100,
      playerHpAwareness: 0,
      hpDangerHits: 2,
      hpDangerCommitMargin: 1,
      hpTradeHits: 4,
      hpTradeCommitPenalty: 100,
    })
    expect(control).toBe('right')
  })

  it('danger mode: low HP (1 hit to die) RELAXES the gate → commits to the escape side', () => {
    const { world, input } = setupWorld('hard')
    positionPlayer(world, 4 * CELL, 10 * CELL, 100)
    // 100 HP / 100 damage = 1 hit to die <= hpDangerHits=2 → danger mode.
    // Margin 100 → relaxed to hpDangerCommitMargin=1 → bestH (~23) >= 1 →
    // commits → 'left' (open longer-horizon side), NOT the base-closer.
    const danger = dodge(input, world, {
      dodgeHorizonScore: 1,
      dodgeHorizonMinMarginTicks: 100,
      playerHpAwareness: 1,
      hpDangerHits: 2,
      hpDangerCommitMargin: 1,
    })
    expect(danger).toBe('left')
  })

  it('danger mode does NOT fire when HP is high (threshold-gated)', () => {
    const { world, input } = setupWorld('hard')
    positionPlayer(world, 4 * CELL, 10 * CELL, 315)
    // 315 / 100 = 4 hits to die > hpDangerHits=2 → not danger → margin stays
    // 100 → gate fails → legacy → 'right'.
    const high = dodge(input, world, {
      dodgeHorizonScore: 1,
      dodgeHorizonMinMarginTicks: 100,
      playerHpAwareness: 1,
      hpDangerHits: 2,
      hpDangerCommitMargin: 1,
    })
    expect(high).toBe('right')
  })

  it('trade mode: high HP (full 1★ buffer) TIGHTENS the gate → accepts the partial dodge', () => {
    const { world, input } = setupWorld('hard')
    positionPlayer(world, 4 * CELL, 10 * CELL, 315)
    // Control: margin 1 → commits to the escape ('left', bestH ~23 >= 1).
    const control = dodge(input, world, {
      dodgeHorizonScore: 1,
      dodgeHorizonMinMarginTicks: 1,
      playerHpAwareness: 0,
    })
    expect(control).toBe('left')
    // Trade: 315/100 = 4 hits >= hpTradeHits=4 → margin 1 + 100 = 101 → gate
    // fails → legacy partial dodge → base-closer 'right'.
    const trade = dodge(input, world, {
      dodgeHorizonScore: 1,
      dodgeHorizonMinMarginTicks: 1,
      playerHpAwareness: 1,
      hpTradeHits: 4,
      hpTradeCommitPenalty: 100,
    })
    expect(trade).toBe('right')
  })

  it('pool-only: M12 is inert in the instant (classic) combat model', () => {
    const { world, input } = setupWorld('classic')
    positionPlayer(world, 4 * CELL, 10 * CELL, 100)
    // classic 'instant': even with every M12 knob set and low HP, the margin
    // must stay dodgeHorizonMinMarginTicks=100 → gate fails → legacy 'right'.
    const classic = dodge(input, world, {
      dodgeHorizonScore: 1,
      dodgeHorizonMinMarginTicks: 100,
      playerHpAwareness: 1,
      hpDangerHits: 2,
      hpDangerCommitMargin: 1,
    })
    expect(classic).toBe('right')
  })
})
