import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { STAGES } from '../src/config/stages'
import { BASE_POS } from '../src/constants'
import {
  playerActionEta,
  enemyDeadline,
  killAssessment,
  canShootBaseLine,
  canBreachRingLine,
  ticksUntilLegalTurn,
  ticksUntilFire,
  playerShotsToKill,
  RING_CELLS,
} from '../src/ai/god/ThreatBudget'
import { enemyCanShootBase, enemyCanBreachRing } from '../src/ai/god/SmartThreatModel'

/**
 * Phase 1 §5.4 minimal test set (plan/God-AI-Hard-Breakthrough-Implementation.md):
 *   - turnCooldownMs 200 vs 500 → ETAs increase monotonically, never bypassed.
 *   - Enemy closer / base HP lower / fewer ring bricks → slack never increases.
 *   - Player farther / needs a turn / more shots → killSlack never increases.
 *   - Same World + params + input → byte-identical results.
 *   - Model is read-only, consumes no World RNG.
 * Plus: parity of the mirror predicates (canShootBaseLine / canBreachRingLine)
 * against the AI's own predicates (SmartThreatModel) on the same World.
 */

function buildWorld(stageIdx: number): World {
  const w = new World()
  w.difficultyKey = 'hard'
  w.loadStageData(STAGES[stageIdx], stageIdx)
  w.playerLevel = 1
  return w
}

/** Clear the base approach zone (rows 22-25, cols 10-25) for deterministic geometry. */
function clearBaseZone(w: World) {
  for (let r = 22; r <= 25; r++) for (let c = 10; c <= 25; c++) w.tileMap.destroy(c, r)
}

function placePlayer(w: World, col: number, row: number, dir: 'up' | 'down' | 'left' | 'right') {
  const p = w.player!
  p.x = col * 16
  p.y = row * 16
  p.dir = dir
  p.lastTurnMs = -9999
  p.lastFire = -9999
  return p
}

function addEnemy(w: World, col: number, row: number, kind: string = 'basic') {
  const e = w.createTank(kind as never, col * 16, row * 16, 'down')
  e.spawnTimer = 0
  e.alive = true
  return e
}

describe('ticksUntilLegalTurn / ticksUntilFire (§5.4 turn-cost)', () => {
  it('read turnCooldownMs from World.rules; 500ms waits longer than 200ms', () => {
    const w = buildWorld(0)
    const p = placePlayer(w, 10, 10, 'up')
    p.lastTurnMs = 0 // turned at t0; now = frame * 16.67ms
    w.frame = 30 // 500ms elapsed
    expect(ticksUntilLegalTurn(w, p)).toBe(0)
    w.frame = 6 // 100ms elapsed
    p.lastTurnMs = 0
    const at200 = ticksUntilLegalTurn(w, p)
    const w500 = buildWorld(0)
    placePlayer(w500, 10, 10, 'up')
    w500.frame = 6
    w500.player!.lastTurnMs = 0
    w500.rules!.turnCooldownMs = 500
    const at500 = ticksUntilLegalTurn(w500, w500.player!)
    expect(at500).toBeGreaterThan(at200)
    expect(at200).toBeGreaterThan(0)
  })

  it('no cooldown rule → turn is always legal', () => {
    const w = buildWorld(0)
    const p = placePlayer(w, 10, 10, 'up')
    p.lastTurnMs = 0
    w.frame = 1
    w.rules!.turnCooldownMs = 0
    expect(ticksUntilLegalTurn(w, p)).toBe(0)
  })

  it('fire ETA is 0 when ready and >0 inside the frozen interval', () => {
    const w = buildWorld(0)
    const p = placePlayer(w, 10, 10, 'up')
    p.nextFireInterval = 1200
    p.lastFire = 0
    w.frame = 72 // 1200ms → ready
    expect(ticksUntilFire(w, p)).toBe(0)
    w.frame = 30 // 500ms → waiting
    expect(ticksUntilFire(w, p)).toBeGreaterThan(0)
  })
})

describe('playerActionEta (§5.1 + §5.4 monotonicity)', () => {
  it('turnCooldownMs 500 makes total ETA strictly larger (never bypassed)', () => {
    const build = (turnMs: number) => {
      const w = buildWorld(0)
      const p = placePlayer(w, 8, 8, 'up')
      w.rules!.turnCooldownMs = turnMs
      return playerActionEta(w, p, 12, 24, 'down', 3).total
    }
    expect(build(500)).toBeGreaterThan(build(200))
  })

  it('needs a turn → total >= aligned total', () => {
    const w = buildWorld(0)
    const p = placePlayer(w, 8, 8, 'up')
    const aligned = playerActionEta(w, p, 12, 8, 'right', 1).total
    const turned = playerActionEta(w, p, 12, 8, 'down', 1).total
    expect(turned).toBeGreaterThanOrEqual(aligned)
  })

  it('more shots required → total never decreases', () => {
    const w = buildWorld(0)
    const p = placePlayer(w, 8, 8, 'up')
    const one = playerActionEta(w, p, 12, 24, 'down', 1).total
    const four = playerActionEta(w, p, 12, 24, 'down', 4).total
    expect(four).toBeGreaterThan(one)
  })

  it('farther target → movement ETA never decreases', () => {
    const w = buildWorld(0)
    const p = placePlayer(w, 8, 8, 'up')
    const near = playerActionEta(w, p, 9, 9, 'down', 1).movementEta
    const far = playerActionEta(w, p, 24, 24, 'down', 1).movementEta
    expect(far).toBeGreaterThan(near)
  })
})

describe('enemyDeadline (§5.2 monotonicity)', () => {
  it('enemy already aligned with the base → shootEta 0', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    const e = addEnemy(w, 18, 24, 'basic') // open row 24, right of the base
    const d = enemyDeadline(w, e)
    expect(d.enemyToShootEta).toBe(0)
  })

  it('base HP lower → urgency never decreases, deadline never grows', () => {
    const full = buildWorld(0)
    const low = buildWorld(0)
    const e1 = addEnemy(full, 20, 20, 'basic')
    const e2 = addEnemy(low, 20, 20, 'basic')
    low.baseHp = 10
    expect(enemyDeadline(low, e2).enemyUrgency).toBeGreaterThan(enemyDeadline(full, e1).enemyUrgency)
    expect(enemyDeadline(low, e2).damageDeadline).toBeLessThan(enemyDeadline(full, e1).damageDeadline)
  })

  it('ring bricks destroyed → urgency never decreases', () => {
    const intact = buildWorld(0)
    const broken = buildWorld(0)
    const e1 = addEnemy(intact, 20, 20, 'basic')
    const e2 = addEnemy(broken, 20, 20, 'basic')
    for (const { col, row } of RING_CELLS) broken.tileMap.destroy(col, row)
    expect(enemyDeadline(broken, e2).enemyUrgency).toBeGreaterThan(enemyDeadline(intact, e1).enemyUrgency)
  })
})

describe('killAssessment (§5.3)', () => {
  it('base HP lower → killSlack never increases (same enemy, same spot)', () => {
    const full = buildWorld(0)
    const low = buildWorld(0)
    const pn = placePlayer(full, 8, 8, 'down')
    const pl = placePlayer(low, 8, 8, 'down')
    const en = addEnemy(full, 18, 24, 'basic')
    const el = addEnemy(low, 18, 24, 'basic')
    low.baseHp = 10
    const fullSlack = killAssessment(full, pn, en).killSlack
    const lowSlack = killAssessment(low, pl, el).killSlack
    expect(lowSlack).toBeLessThan(fullSlack)
  })

  it('enemy closer along a clear lane → deadline never grows', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    const near = addEnemy(w, 16, 20, 'basic')
    const far = addEnemy(w, 24, 20, 'basic')
    const dn = enemyDeadline(w, near)
    const df = enemyDeadline(w, far)
    expect(dn.enemyToShootEta).toBeLessThan(df.enemyToShootEta)
    expect(dn.damageDeadline).toBeLessThan(df.damageDeadline)
  })

  it('playerShotsToKill respects enemy maxHp (armor needs more shots)', () => {
    const w = buildWorld(0)
    const basic = addEnemy(w, 20, 20, 'basic')
    const armor = addEnemy(w, 20, 20, 'armor')
    expect(playerShotsToKill(w, armor)).toBeGreaterThanOrEqual(playerShotsToKill(w, basic))
  })
})

describe('mirror parity: ThreatBudget vs SmartThreatModel', () => {
  it('canShootBaseLine ≡ enemyCanShootBase on the same World (all 35 stages)', () => {
    for (let s = 0; s < STAGES.length; s++) {
      const w = buildWorld(s)
      const ai = new GodAIInput(w, { ...DEFAULT_GOD_AI_PARAMS })
      ai.reset()
      for (const [col, row] of [
        [BASE_POS.col, BASE_POS.row + 3],
        [BASE_POS.col + 5, BASE_POS.row + 1],
        [BASE_POS.col, 10],
        [4, 24],
        [20, 20],
      ] as Array<[number, number]>) {
        const t = w.createTank('basic', col * 16, row * 16, 'up')
        t.spawnTimer = 0
        expect(canShootBaseLine(w, col, row)).toBe(enemyCanShootBase(ai, t))
      }
    }
  })

  it('canBreachRingLine ≡ enemyCanBreachRing on the same World (all 35 stages)', () => {
    for (let s = 0; s < STAGES.length; s++) {
      const w = buildWorld(s)
      const ai = new GodAIInput(w, { ...DEFAULT_GOD_AI_PARAMS })
      ai.reset()
      for (const [col, row] of [
        [BASE_POS.col, BASE_POS.row - 2],
        [BASE_POS.col - 2, BASE_POS.row],
        [BASE_POS.col + 4, BASE_POS.row + 1],
        [14, 24],
        [12, 21],
      ] as Array<[number, number]>) {
        const t = w.createTank('basic', col * 16, row * 16, 'down')
        t.spawnTimer = 0
        expect(canBreachRingLine(w, col, row)).toBe(enemyCanBreachRing(ai, t))
      }
    }
  })
})

describe('determinism & purity (§5.4)', () => {
  it('same World + same call → identical results, World untouched', () => {
    const w = buildWorld(7)
    const p = placePlayer(w, 8, 8, 'up')
    const e = addEnemy(w, 12, 24, 'power')
    const before = JSON.stringify({ ...w, rng: undefined })
    const a = killAssessment(w, p, e)
    const b = killAssessment(w, p, e)
    expect(a).toEqual(b)
    expect(JSON.stringify({ ...w, rng: undefined })).toBe(before)
  })

  it('consumes no World RNG (rng state identical after calls)', () => {
    const w = buildWorld(0)
    const p = placePlayer(w, 8, 8, 'up')
    const e = addEnemy(w, 12, 24, 'basic')
    const seedBefore = w.rng.getState()
    playerActionEta(w, p, 12, 24, 'down', 2)
    enemyDeadline(w, e)
    killAssessment(w, p, e)
    expect(w.rng.getState()).toBe(seedBefore)
  })
})