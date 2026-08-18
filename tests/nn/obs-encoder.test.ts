/**
 * obs-encoder.test.ts — unit tests for the NN observation encoder (plan §1, NN-M0a).
 *
 * Strategy: build a minimal enemy-free World fixture cast `as unknown as World`.
 * With no enemies, the encoder never calls ThreatBudget.killAssessment /
 * enemyDeadline, keeping the fixture tiny while still exercising every channel
 * and scalar. The fixture is intentionally not a real `World` instance — it only
 * populates the fields the encoder reads.
 */

import { describe, it, expect } from 'bun:test'
import {
  ObsEncoder,
  OBS_CHANNELS,
  BOARD,
  SCALAR_DIM,
  CH,
  POWERUP_ORDER,
  SCALAR_X_INDICES,
  isFireEdge,
  decisionTick,
  actionFromFrame,
  computeMasks,
  type FrameLabel,
} from '../../src/nn/obs-encoder'
import type { World } from '../../src/game/World'
import type { Direction } from '../../src/constants'

// ---- channel cell accessor ----
function chCell(obs: Uint8Array, ch: number, col: number, row: number): number {
  return obs[ch * BOARD * BOARD + row * BOARD + col]
}
function chMax(obs: Uint8Array, ch: number): number {
  let m = 0
  for (let i = ch * BOARD * BOARD; i < (ch + 1) * BOARD * BOARD; i++) m = Math.max(m, obs[i])
  return m
}

// ---- fixture builders ----
function mkTank(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    alive: true,
    x: 100,
    y: 100,
    w: 32,
    h: 32,
    dir: 'up',
    allegiance: 'enemy',
    kind: 'basic',
    spawnTimer: 0,
    aiState: { level: 'none' },
    level: 1,
    nextFireInterval: 0,
    lastFire: 0,
    lastTurnMs: -9999,
    frenzyTimer: 0,
    frenzyShotsLeft: 0,
    ...over,
  }
}

function mkWorld(over: Record<string, unknown> = {}): World {
  const grid = Array.from({ length: BOARD }, () => Array.from({ length: BOARD }, () => 'empty'))
  return {
    tileMap: {
      grid,
      isBaseDestroyed: () => false,
      // every queried cell is brick so base ring cells light up at 1
      get: () => 'brick',
    },
    player: mkTank({
      allegiance: 'player',
      x: 96,
      y: 96,
      level: 2,
      nextFireInterval: 300,
      lastFire: 0,
      dir: 'up',
    }),
    tanks: [],
    bullets: [],
    powerUps: [],
    spawnQueue: [],
    enemyCount: 0,
    enemiesTotal: 20,
    enemySpawnPoints: [
      { x: 0, y: 0 },
      { x: 192, y: 0 },
      { x: 96, y: 0 },
    ],
    rules: { spawnIntervalMs: 1500, turnCooldownMs: 200 },
    lives: 3,
    guardStock: 0,
    frenzyStock: 0,
    rewindStock: 0,
    frame: 0,
    ...over,
  } as unknown as World
}

describe('obs-encoder dimensions', () => {
  it('exposes the canonical 14×26×26 obs and 24-dim scalar', () => {
    expect(OBS_CHANNELS).toBe(14)
    expect(BOARD).toBe(26)
    expect(SCALAR_DIM).toBe(24)
    const enc = new ObsEncoder()
    expect(enc.obs.length).toBe(14 * 26 * 26)
    expect(enc.scalars.length).toBe(24)
  })
})

describe('POWERUP_ORDER', () => {
  it('has exactly 15 members in the types.ts union order', () => {
    expect(POWERUP_ORDER.length).toBe(15)
    expect(POWERUP_ORDER[0]).toBe('star')
    expect(POWERUP_ORDER[14]).toBe('mine')
    // ch12 encodes value = enumIndex + 1
    expect(POWERUP_ORDER.indexOf('mine')).toBe(14)
    expect(POWERUP_ORDER.indexOf('star')).toBe(0)
  })
})

describe('actionFromFrame', () => {
  const cases: Array<[Direction | null, boolean, boolean, boolean, FrameLabel]> = [
    [null, false, false, false, { move: 0, fire: 0, item: 0 }],
    ['up', false, false, false, { move: 1, fire: 0, item: 0 }],
    ['down', false, false, false, { move: 2, fire: 0, item: 0 }],
    ['left', false, false, false, { move: 3, fire: 0, item: 0 }],
    ['right', false, false, false, { move: 4, fire: 0, item: 0 }],
    ['up', true, false, false, { move: 1, fire: 1, item: 0 }],
    ['up', false, true, false, { move: 1, fire: 0, item: 1 }],
    ['up', false, false, true, { move: 1, fire: 0, item: 2 }],
  ]
  for (const [dir, firing, guard, frenzy, exp] of cases) {
    it(`dir=${dir} firing=${firing} guard=${guard} frenzy=${frenzy} -> ${JSON.stringify(exp)}`, () => {
      expect(actionFromFrame({ direction: dir, firing, guard, frenzy })).toEqual(exp)
    })
  }
})

describe('isFireEdge (cooldown edge, not window)', () => {
  it('is false before cooldown elapses (frame 0)', () => {
    const w = mkWorld({ frame: 0, player: mkTank({ nextFireInterval: 300, lastFire: 0 }) })
    expect(isFireEdge(w)).toBe(false)
  })
  it('is true on the exact tick cooldown elapses (frame 18 = 300ms)', () => {
    const w = mkWorld({ frame: 18, player: mkTank({ nextFireInterval: 300, lastFire: 0 }) })
    expect(isFireEdge(w)).toBe(true)
  })
  it('is false on the following tick (frame 19)', () => {
    const w = mkWorld({ frame: 19, player: mkTank({ nextFireInterval: 300, lastFire: 0 }) })
    expect(isFireEdge(w)).toBe(false)
  })
})

describe('decisionTick (event-type predicate)', () => {
  // frame 18 / nextFireInterval 300 / lastFire 0 => a fire-edge IS active here.
  const fireWorld = mkWorld({ frame: 18, player: mkTank({ nextFireInterval: 300, lastFire: 0 }) })
  // frame 0 with lastFire 0 => cooldown NOT elapsed: no fire-edge. Used for the
  // non-fire-priority cases so item/subsample priority is observable on its own.
  const noFireWorld = mkWorld({ frame: 0, player: mkTank({ nextFireInterval: 300, lastFire: 0 }) })
  it('flags a turn-event with highest priority (condition 0)', () => {
    const r = decisionTick(123, fireWorld, 'up', 'left', false, false, false, false)
    expect(r.isDecision).toBe(true)
    expect(r.condition).toBe(0)
  })
  it('flags a fire-edge (condition 1)', () => {
    const r = decisionTick(123, fireWorld, 'up', 'up', false, false, false, false)
    expect(r.isDecision).toBe(true)
    expect(r.condition).toBe(1)
  })
  it('flags an item-event when no fire-edge (condition 2)', () => {
    const r = decisionTick(123, noFireWorld, 'up', 'up', false, true, false, false)
    expect(r.isDecision).toBe(true)
    expect(r.condition).toBe(2)
  })
  it('flags k-subsample when no other event (condition 3)', () => {
    const r = decisionTick(20, noFireWorld, 'up', 'up', false, false, false, false)
    expect(r.isDecision).toBe(true)
    expect(r.condition).toBe(3)
  })
  it('is not a decision when nothing fires and t%k!=0', () => {
    const r = decisionTick(7, noFireWorld, 'up', 'up', false, false, false, false)
    expect(r.isDecision).toBe(false)
  })
})

describe('computeMasks', () => {
  it('masks fire-hold when cooldown not elapsed', () => {
    const w = mkWorld({ frame: 0, player: mkTank({ nextFireInterval: 300, lastFire: 0 }), guardStock: 0, frenzyStock: 0 })
    const m = computeMasks(w)
    expect(m.move).toEqual([1, 1, 1, 1, 1])
    expect(m.fire).toEqual([1, 0])
    expect(m.item).toEqual([1, 0, 0])
  })
  it('unmasks fire-hold when ready, and items by stock', () => {
    const w = mkWorld({ frame: 18, player: mkTank({ nextFireInterval: 300, lastFire: 0 }), guardStock: 2, frenzyStock: 1 })
    const m = computeMasks(w)
    expect(m.fire).toEqual([1, 1])
    expect(m.item).toEqual([1, 1, 1])
  })
  it('enables only guard when frenzy stock is empty', () => {
    const w = mkWorld({ frame: 18, player: mkTank({ nextFireInterval: 300, lastFire: 0 }), guardStock: 1, frenzyStock: 0 })
    expect(computeMasks(w).item).toEqual([1, 1, 0])
  })
})

describe('ObsEncoder.encode — spatial channels', () => {
  it('encodes the base: eagle=2 (alive) and ring cells=1', () => {
    const enc = new ObsEncoder()
    enc.encode(mkWorld())
    const bc = 12
    const br = 24
    expect(chCell(enc.obs, CH.base, bc, br)).toBe(2)
    // at least one ring cell is lit (get() returns brick for every query)
    expect(chMax(enc.obs, CH.base)).toBeGreaterThanOrEqual(1)
  })

  it('encodes base destroyed: eagle=0', () => {
    const enc = new ObsEncoder()
    const w = mkWorld()
    w.tileMap.isBaseDestroyed = () => true
    enc.encode(w)
    expect(chCell(enc.obs, CH.base, 12, 24)).toBe(0)
  })

  it('encodes self with star truncated at 3 (level 5 -> star code 3)', () => {
    const enc = new ObsEncoder()
    enc.encode(mkWorld({ player: mkTank({ level: 5, dir: 'up', x: 96, y: 96 }) }))
    // up => dir code 0; val = (3<<3) | (0+1) = 25
    expect(chMax(enc.obs, CH.self)).toBe(25)
  })

  it('encodes self with uncapped level (level 2 -> star code 2)', () => {
    const enc = new ObsEncoder()
    enc.encode(mkWorld({ player: mkTank({ level: 2, dir: 'up', x: 96, y: 96 }) }))
    // val = (2<<3) | 1 = 17
    expect(chMax(enc.obs, CH.self)).toBe(17)
  })

  it('encodes a power-up on ch12 with value = enumIndex+1 (mine=15)', () => {
    const enc = new ObsEncoder()
    enc.encode(
      mkWorld({
        powerUps: [{ alive: true, x: 80, y: 80, type: 'mine' }],
      }),
    )
    // floor(80/16)=5
    expect(chCell(enc.obs, CH.powerup, 5, 5)).toBe(15)
  })

  it('encodes an enemy bullet on ch11 with value = dirCode+1 (up=1)', () => {
    const enc = new ObsEncoder()
    enc.encode(
      mkWorld({
        bullets: [{ alive: true, x: 48, y: 48, w: 6, h: 6, allegiance: 'enemy', dir: 'up' }],
      }),
    )
    // center cell floor((48+3)/16)=3
    expect(chCell(enc.obs, CH.bullet, 3, 3)).toBe(1)
  })

  it('is deterministic: same fixture -> identical bytes', () => {
    const enc = new ObsEncoder()
    enc.encode(mkWorld())
    const a = enc.obs.slice()
    const sa = enc.scalars.slice()
    enc.encode(mkWorld())
    expect(Array.from(enc.obs)).toEqual(Array.from(a))
    expect(Array.from(enc.scalars)).toEqual(Array.from(sa))
  })

  it('SCALAR_X_INDICES are the relative-direction x-components (20,23)', () => {
    expect(SCALAR_X_INDICES).toEqual([20, 23])
  })
})
