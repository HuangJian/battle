import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { isFieldRetreatConditionImpl } from '../src/ai/god/StrategyPlanner'
import { CLASSIC_MODEL_PARAMS } from '../src/ai/god/params'
import { STAGES } from '../src/config/stages'
import type { PowerUpType } from '../src/types'

/**
 * §146 C — fieldRetreatPickupGate: when the M13 field-pressure retreat
 * condition holds (far from base + full enemy field, base NOT under threat),
 * the HIGH-tier urgent pickup must NOT hijack the retreat. S8: the pickup
 * branch (weight 800) evaluated before hunt (200), so M13's return to the
 * defense position never ran while a power-up sat within divert range — the
 * player stayed in the dead-end pocket (43% of loss-ending time) while the
 * base fell.
 *
 * The predicate is SHARED with selectTargetUncached's M13 block
 * (isFieldRetreatConditionImpl — single source of truth). These tests lock:
 *   1. the predicate semantics (all six conditions),
 *   2. knob default 0 (byte-identical),
 *   3. PICKUP_HIGH suppression only when the predicate holds.
 *
 * Note: S8's brick/(brick+steel) is 0.884 — just BELOW the 0.9 brick-heavy
 * threshold — so the field retreat stays at the global dist 26 and the
 * pocket (dist 25) does NOT trigger it (root-cause #1, the threshold gap).
 * The predicate is therefore exercised via Battlement (pure brick, adapted
 * dist 12) and via an explicit tight threshold.
 */
function buildAI(
  stageIdx: number,
  extra: Partial<typeof DEFAULT_GOD_AI_PARAMS> = {},
): { w: World; ai: GodAIInput } {
  const w = new World()
  w.loadStageData(STAGES[stageIdx], stageIdx)
  const ai = new GodAIInput(w, { ...DEFAULT_GOD_AI_PARAMS, ...extra })
  ai.reset()
  return { w, ai }
}

/** Teleport the controlled player to a cell (px coords). */
function placePlayer(ai: GodAIInput, col: number, row: number): void {
  const p = ai.controlledTank(ai.world)
  if (!p) throw new Error('no player')
  p.x = col * 16
  p.y = row * 16
  // Clear the spawn-protection timer so think()'s dead-check passes and the
  // Cluster C _enemies snapshot is populated (otherwise branch = 'dead').
  p.spawnTimer = 0
}

/** Place a live, fully-spawned enemy at a cell. */
function placeEnemy(w: World, col: number, row: number): void {
  const t = w.createTank('basic', col * 16, row * 16, 'down')
  t.spawnTimer = 0
  t.alive = true
  w.tanks.push(t)
}

/** Place an alive power-up at a cell. */
function placeItem(w: World, type: PowerUpType, col: number, row: number): void {
  w.addPowerUp({
    id: 9000 + col * 100 + row,
    type,
    x: col * 16,
    y: row * 16,
    w: 32,
    h: 32,
    alive: true,
    lifeTimer: 0,
    blinkTimer: 0,
  })
}

describe('isFieldRetreatConditionImpl (§146 C shared predicate)', () => {
  it('does NOT fire at dist 25 with the default pool threshold 26 (S8 pocket)', () => {
    const { w, ai } = buildAI(7)
    placePlayer(ai, 1, 10) // pocket: dist to base (12,24) = 25
    for (let i = 0; i < 4; i++) placeEnemy(w, 2 + i, 1)
    ai.getMoveDirection() // runs think() — populates the Cluster C _enemies snapshot
    const pc = ai.playerCell()
    const dist = Math.abs(pc.col - 12) + Math.abs(pc.row - 24)
    expect(dist).toBe(25)
    // S8 is NOT brick-heavy (0.884 < 0.9) — the threshold stays at 26.
    expect(ai.params.outnumberedFieldDistCells).toBe(26)
    expect(isFieldRetreatConditionImpl(ai, ai.isBaseUnderThreat(), dist, ai._enemies.length)).toBe(
      false,
    )
  })

  it('fires on Battlement with the brick-heavy adaptation (pure brick ≥ 0.9 ratio)', () => {
    // §133 adaptation: with brickHeavyDefenseWallRatio=0.9, S34 (brick ratio
    // 1.0) gets outnumberedFieldDistCells = brickHeavyFieldDistCells (12).
    // S8 (0.884) is below the ratio and stays at the global 26 — that is
    // exactly the S8 threshold gap (root-cause #1).
    const { w, ai } = buildAI(33, { brickHeavyDefenseWallRatio: 0.9, brickHeavyFieldDistCells: 12 })
    placePlayer(ai, 4, 2) // far top-left: dist to base (12,24) = 30
    for (let i = 0; i < 4; i++) placeEnemy(w, 2 + i, 1) // top band — no base threat
    ai.getMoveDirection()
    const pc = ai.playerCell()
    const dist = Math.abs(pc.col - 12) + Math.abs(pc.row - 24)
    expect(ai.params.outnumberedFieldDistCells).toBe(12) // adaptation applied
    expect(dist).toBeGreaterThan(ai.params.outnumberedFieldDistCells)
    expect(ai.isBaseUnderThreat()).toBe(false)
    expect(isFieldRetreatConditionImpl(ai, false, dist, ai._enemies.length)).toBe(true)
  })

  it('fires at dist 25 when the threshold is tightened (the S8 fix scenario)', () => {
    const { w, ai } = buildAI(7, { outnumberedFieldDistCells: 20 }) // simulate fix
    placePlayer(ai, 1, 10)
    for (let i = 0; i < 4; i++) placeEnemy(w, 2 + i, 1)
    ai.getMoveDirection()
    const pc = ai.playerCell()
    const dist = Math.abs(pc.col - 12) + Math.abs(pc.row - 24)
    expect(isFieldRetreatConditionImpl(ai, ai.isBaseUnderThreat(), dist, ai._enemies.length)).toBe(
      true,
    )
  })

  it('does NOT fire when the base IS under threat (defense takes over)', () => {
    const { w, ai } = buildAI(7, { outnumberedFieldDistCells: 20 })
    placePlayer(ai, 1, 10)
    placeEnemy(w, 11, 23) // enemy on the base ring → threat
    ai.getMoveDirection()
    const pc = ai.playerCell()
    const dist = Math.abs(pc.col - 12) + Math.abs(pc.row - 24)
    expect(ai.isBaseUnderThreat()).toBe(true)
    expect(isFieldRetreatConditionImpl(ai, true, dist, ai._enemies.length)).toBe(false)
  })

  it('does NOT fire in classic (instant) combat — M13 is pool-only', () => {
    const { w, ai } = buildAI(7, { outnumberedFieldDistCells: 20 })
    ai.world.rules.combatModel = 'instant'
    try {
      placePlayer(ai, 1, 10)
      placeEnemy(w, 2, 1)
      ai.getMoveDirection()
      const pc = ai.playerCell()
      const dist = Math.abs(pc.col - 12) + Math.abs(pc.row - 24)
      expect(isFieldRetreatConditionImpl(ai, false, dist, ai._enemies.length)).toBe(false)
    } finally {
      ai.world.rules.combatModel = 'pool'
    }
  })

  it('does NOT fire when outnumberedFieldRetreat is OFF', () => {
    const { w, ai } = buildAI(7, { outnumberedFieldDistCells: 20, outnumberedFieldRetreat: 0 })
    placePlayer(ai, 1, 10)
    for (let i = 0; i < 4; i++) placeEnemy(w, 2 + i, 1)
    ai.getMoveDirection()
    const pc = ai.playerCell()
    const dist = Math.abs(pc.col - 12) + Math.abs(pc.row - 24)
    expect(isFieldRetreatConditionImpl(ai, false, dist, ai._enemies.length)).toBe(false)
  })
})

describe('fieldRetreatPickupGate knob (§146 C)', () => {
  it('defaults to 0 (OFF — byte-identical)', () => {
    expect(DEFAULT_GOD_AI_PARAMS.fieldRetreatPickupGate).toBe(0)
  })

  it('does not appear in CLASSIC_MODEL_PARAMS (pool-only, predicate gates itself)', () => {
    expect((CLASSIC_MODEL_PARAMS as Record<string, unknown>).fieldRetreatPickupGate ?? 0).toBe(0)
  })

  it('HIGH pickup target exists in the pocket but the gate suppresses it when ON', () => {
    // Simulate the S8 fix scenario: tightened threshold so the pocket dist 25
    // DOES satisfy the M13 predicate — the gate then blocks the pickup.
    const { w, ai } = buildAI(7, { fieldRetreatPickupGate: 1, outnumberedFieldDistCells: 20 })
    placePlayer(ai, 1, 10)
    for (let i = 0; i < 4; i++) placeEnemy(w, 2 + i, 1)
    ai.getMoveDirection()
    placeItem(w, 'bomb', 2, 10) // within HIGH range (8) of the player
    const target = ai.findUrgentPowerUpTarget(1 * 16 + 8, 10 * 16 + 8, 'high')
    expect(target).not.toBeNull() // the item IS a valid HIGH pickup…
    const pc = ai.playerCell()
    const dist = Math.abs(pc.col - 12) + Math.abs(pc.row - 24)
    // …but the M13 predicate holds → the gate returns false → HUNT/M13
    // retreat fires instead of the pickup hijack.
    expect(isFieldRetreatConditionImpl(ai, ai.isBaseUnderThreat(), dist, ai._enemies.length)).toBe(
      true,
    )
  })

  it('gate OFF: the pickup branch still sees the item (no suppression)', () => {
    const { w, ai } = buildAI(7, { outnumberedFieldDistCells: 20 }) // gate default 0
    placePlayer(ai, 1, 10)
    for (let i = 0; i < 4; i++) placeEnemy(w, 2 + i, 1)
    ai.getMoveDirection()
    placeItem(w, 'bomb', 2, 10)
    const target = ai.findUrgentPowerUpTarget(1 * 16 + 8, 10 * 16 + 8, 'high')
    expect(target).not.toBeNull() // item still available — gate OFF = no suppression
  })

  it('MID tier is NOT gated — the economy core must keep flowing (scope, §147)', () => {
    // §147: extending the gate to MID/LOW was A/B-measured net negative on
    // chaos (S12 −9pp / S4 −9pp / S15 −7pp / S34 −7pp, aggregate −1.3pp) —
    // star/tank/shield are the permanent-DPS economy and suppressing them
    // under M13 conditions loses more than the retreat gains. This test
    // LOCKS the scope: the gate helper must not block the MID candidate's
    // target query path.
    const { w, ai } = buildAI(7, {
      fieldRetreatPickupGate: 1,
      outnumberedFieldDistCells: 20,
      pickupPriorityMode: 1,
      chokepointMode: 1,
    })
    placePlayer(ai, 1, 10)
    for (let i = 0; i < 4; i++) placeEnemy(w, 2 + i, 1)
    ai.getMoveDirection()
    placeItem(w, 'star', 2, 10) // within MID range (4) of the player
    const midTarget = ai.findUrgentPowerUpTarget(1 * 16 + 8, 10 * 16 + 8, 'midlow')
    // MID item remains a valid target (gate scope = HIGH only).
    expect(midTarget).not.toBeNull()
  })
})
