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
  firePower,
  tankCenterCell,
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
  w.rng.reseed(20260816) // createTank draws spawn jitter from world.rng — pin it
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
    w500.rules = { ...w500.rules!, turnCooldownMs: 500 } // clone — never mutate the shared RULES
    const at500 = ticksUntilLegalTurn(w500, w500.player!)
    expect(at500).toBeGreaterThan(at200)
    expect(at200).toBeGreaterThan(0)
  })

  it('no cooldown rule → turn is always legal', () => {
    const w = buildWorld(0)
    const p = placePlayer(w, 10, 10, 'up')
    p.lastTurnMs = 0
    w.frame = 1
    w.rules = { ...w.rules!, turnCooldownMs: 0 } // clone
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
      w.rules = { ...w.rules!, turnCooldownMs: turnMs } // clone
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

describe('enemyDeadline (§5.2 monotonicity, §4.2 bound semantics)', () => {
  it('enemy already aligned with the base → first damage imminent (flight only)', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    const e = addEnemy(w, 18, 24, 'basic') // open row 24, right of the base
    e.lastFire = -9999 // gun ready — no re-arm wait
    const d = enemyDeadline(w, e)
    // csb: earliest = flight only (> 0 — the bullet must still fly).
    expect(d.enemyDamageEarliest).toBeGreaterThan(0)
    expect(d.enemyDamageEarliest).toBeLessThan(30)
  })

  it('extra blocker on the ray → model must NOT claim base can be shot now', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    const e = addEnemy(w, 18, 24, 'basic') // centers at (19, 25)
    e.lastFire = -9999
    expect(canShootBaseLine(w, 19, 25)).toBe(true)
    const openEarliest = enemyDeadline(w, e).enemyDamageEarliest
    // Put a brick back between the enemy and the base on its lane row
    // (addEnemy(18,24) centers the tank at col 19, row 25 = br+1).
    w.tileMap.set(16, 25, 'brick')
    expect(canShootBaseLine(w, 19, 25)).toBe(false)
    const blocked = enemyDeadline(w, e)
    // Ring is gone (cleared) → walk branch; strictly later than the open shot.
    expect(blocked.enemyDamageEarliest).toBeGreaterThan(openEarliest)
    expect(canBreachRingLine(w, 19, 25)).toBe(false)
  })

  it('cbr breach cost is charged ONCE — never re-charged in the damage window', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    // Keep exactly ONE ring brick (15,25): enemy at (18,24) centers at
    // (19,25) — clear line to it (cols 16-18 empty) → cbr, blocks = 1.
    w.tileMap.set(14, 25, 'brick') // bc+2 ring column
    const e = addEnemy(w, 18, 24, 'basic')
    e.lastFire = -9999
    expect(canShootBaseLine(w, 19, 25)).toBe(false)
    expect(canBreachRingLine(w, 19, 25)).toBe(true)
    const cadence = e.nextFireInterval > 0 ? e.nextFireInterval / (1000 / 60) : 0
    const flight = e.bulletSpeed > 0 ? (16 * 2) / e.bulletSpeed : 0
    const d = enemyDeadline(w, e)
    // window = baseShots × (cadence + flight) — NO extra ring-breach cycles.
    const baseShots = Math.max(1, Math.ceil(w.baseHp / Math.max(1, firePower(w, 'basic'))))
    expect(d.enemyDamageWindow).toBeCloseTo(baseShots * (cadence + flight), 5)
    // earliest = fireReady(0) + 1 brick × (cadence + flight) + flight.
    expect(d.enemyDamageEarliest).toBeCloseTo(1 * (cadence + flight) + flight, 5)
    // deadline = earliest − one-turn safety margin (200ms → 12 ticks).
    expect(d.enemyDamageDeadline).toBeCloseTo(d.enemyDamageEarliest - 12, 5)
  })

  it('base HP lower → urgency never decreases, window shrinks, threat never lighter', () => {
    const full = buildWorld(0)
    const low = buildWorld(0)
    const e1 = addEnemy(full, 20, 20, 'basic')
    const e2 = addEnemy(low, 20, 20, 'basic')
    low.baseHp = 10
    expect(enemyDeadline(low, e2).enemyUrgency).toBeGreaterThan(
      enemyDeadline(full, e1).enemyUrgency,
    )
    expect(enemyDeadline(low, e2).enemyDamageWindow).toBeLessThan(
      enemyDeadline(full, e1).enemyDamageWindow,
    )
    // first-damage time is HP-independent; the safe deadline must not grow.
    expect(enemyDeadline(low, e2).enemyDamageDeadline).toBeLessThanOrEqual(
      enemyDeadline(full, e1).enemyDamageDeadline,
    )
  })

  it('ring bricks destroyed → walking enemy earliest never grows (no phantom breach)', () => {
    const intact = buildWorld(0)
    const broken = buildWorld(0)
    // Walking enemies (not aligned, not breaching): stage-0 default terrain.
    const e1 = addEnemy(intact, 20, 20, 'basic')
    const e2 = addEnemy(broken, 20, 20, 'basic')
    for (const { col, row } of RING_CELLS) broken.tileMap.destroy(col, row)
    expect(enemyDeadline(broken, e2).enemyUrgency).toBeGreaterThan(
      enemyDeadline(intact, e1).enemyUrgency,
    )
    expect(enemyDeadline(broken, e2).enemyDamageEarliest).toBeLessThan(
      enemyDeadline(intact, e1).enemyDamageEarliest,
    )
    expect(enemyDeadline(broken, e2).enemyDamageDeadline).toBeLessThan(
      enemyDeadline(intact, e1).enemyDamageDeadline,
    )
  })
})

describe('killAssessment (§5.3)', () => {
  it('player farther / more shots / needs a turn → killSlack never increases', () => {
    // §4.1: slack shrinks with every player-side cost. Same enemy, same spot.
    const w = buildWorld(0)
    clearBaseZone(w)
    const e = addEnemy(w, 18, 24, 'basic')
    // placePlayer returns the SHARED w.player — snapshot each slack before
    // repositioning, or near and far are the same tank.
    const nearSlack = killAssessment(w, placePlayer(w, 14, 24, 'left'), e).killSlack
    const farSlack = killAssessment(w, placePlayer(w, 4, 24, 'left'), e).killSlack
    expect(farSlack).toBeLessThan(nearSlack)

    // More shots required (armor) → longer kill ETA → smaller slack.
    const w2 = buildWorld(0)
    clearBaseZone(w2)
    const basic = addEnemy(w2, 18, 24, 'basic')
    const armor = addEnemy(w2, 18, 24, 'armor')
    const p2 = placePlayer(w2, 14, 24, 'left')
    const armorSlack = killAssessment(w2, p2, armor).killSlack
    const basicSlack = killAssessment(w2, p2, basic).killSlack
    expect(armorSlack).toBeLessThan(basicSlack)

    // Needs a turn → the turn wait + window shrink the slack (§4.1: the wait
    // is billed once — but it IS billed). Player (14,24) facing left
    // (aligned with the row-24 enemy) vs facing right (must turn 180°).
    const w3 = buildWorld(0)
    clearBaseZone(w3)
    const e3 = addEnemy(w3, 18, 24, 'basic')
    // placePlayer hands back the shared w.player — snapshot in placement order.
    const alignedSlack = killAssessment(w3, placePlayer(w3, 14, 24, 'right'), e3).killSlack
    const turnedSlack = killAssessment(w3, placePlayer(w3, 14, 24, 'left'), e3).killSlack
    expect(turnedSlack).toBeLessThan(alignedSlack)
  })

  it('enemy closer along a clear lane → deadline never grows', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    const near = addEnemy(w, 16, 20, 'basic')
    const far = addEnemy(w, 24, 20, 'basic')
    const dn = enemyDeadline(w, near)
    const df = enemyDeadline(w, far)
    expect(dn.enemyDamageEarliest).toBeLessThan(df.enemyDamageEarliest)
    expect(dn.enemyDamageDeadline).toBeLessThan(df.enemyDamageDeadline)
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
describe('playerActionEta single-billing (open-test protocol §4.1)', () => {
  it('total is exactly the sum of its five fields — no cost billed twice', () => {
    const w = buildWorld(0)
    const p = placePlayer(w, 8, 8, 'up')
    p.lastTurnMs = 0
    w.frame = 6 // mid-cooldown: a turn is NOT legal yet
    const eta = playerActionEta(w, p, 12, 24, 'down', 3)
    expect(eta.total).toBe(
      eta.nextLegalTurnEta +
        eta.movementEta +
        eta.aimAlignmentEta +
        eta.fireCooldownEta +
        eta.requiredShotsEta,
    )
    // aimAlignmentEta is the turn WINDOW only — the WAIT lives in
    // nextLegalTurnEta and must not be repeated inside it.
    expect(eta.aimAlignmentEta).toBeGreaterThan(0)
    expect(eta.nextLegalTurnEta).toBeGreaterThan(0)
  })

  it('already facing aimDir on a single-axis path → aim wait unchanged by cooldown', () => {
    // §4.1: "当前方向正确时,增加 turn cooldown 不应改变瞄准等待".
    const mk = (turnMs: number) => {
      const w = buildWorld(0)
      w.rules = { ...w.rules!, turnCooldownMs: turnMs } // clone
      const p = placePlayer(w, 8, 8, 'up')
      // player centers at cell col 9 — same column ⇒ single-axis path.
      return playerActionEta(w, p, 9, 3, 'up', 1)
    }
    const e200 = mk(200)
    const e500 = mk(500)
    expect(e200.aimAlignmentEta).toBe(0)
    expect(e500.aimAlignmentEta).toBe(0)
    expect(e200.nextLegalTurnEta).toBe(0)
    expect(e500.nextLegalTurnEta).toBe(0)
  })

  it('wrong facing → the legal-turn WAIT is added exactly once (200→500)', () => {
    // Same geometry, player facing the wrong way, one turn from legal
    // (turned at frame 0, now frame 6 → 100ms elapsed).
    const mk = (turnMs: number) => {
      const w = buildWorld(0)
      w.rules = { ...w.rules!, turnCooldownMs: turnMs } // clone
      const p = placePlayer(w, 8, 8, 'up')
      p.lastTurnMs = 0
      w.frame = 6
      return { eta: playerActionEta(w, p, 8, 2, 'down', 1), world: w }
    }
    const a = mk(200)
    const b = mk(500)
    // Δwait = (500−100)/16.67 − (200−100)/16.67 = 18 ticks, Δwindow = 18.
    // If the wait were double-billed the total delta would be 36+ — pinned.
    const dWait = b.eta.nextLegalTurnEta - a.eta.nextLegalTurnEta
    const dWindow = b.eta.aimAlignmentEta - a.eta.aimAlignmentEta
    expect(b.eta.total - a.eta.total).toBeCloseTo(dWait + dWindow, 5)
    expect(dWait).toBeGreaterThan(0)
    expect(dWindow).toBeGreaterThan(0)
    // All turn-requiring ETAs strictly increase under a longer cooldown.
    expect(b.eta.total).toBeGreaterThan(a.eta.total)
    expect(b.eta.aimAlignmentEta).toBeGreaterThan(a.eta.aimAlignmentEta)
    expect(b.eta.nextLegalTurnEta).toBeGreaterThan(a.eta.nextLegalTurnEta)
  })

  it('perpendicular path change with correct facing still bills ONE turn window', () => {
    // Facing up, target up-right: path needs an axis change even though the
    // aim is already 'up'. Old model dropped this cost entirely; it must now
    // be exactly one turn window (never two).
    const w = buildWorld(0)
    const p = placePlayer(w, 8, 8, 'up')
    p.lastTurnMs = -9999
    const straight = playerActionEta(w, p, 9, 3, 'up', 1)
    const bend = playerActionEta(w, p, 10, 3, 'up', 1)
    const window = 200 / (1000 / 60) + 1 // one turn window @200ms = 13 ticks
    expect(bend.aimAlignmentEta).toBeCloseTo(window, 5)
    expect(straight.aimAlignmentEta).toBe(0)
    expect(bend.total - straight.total).toBeCloseTo(
      bend.movementEta -
        straight.movementEta +
        window +
        (bend.requiredShotsEta - straight.requiredShotsEta),
      5,
    )
  })
})

describe('enemyDeadline terminates for degenerate placements (M4 regression)', () => {
  it('an enemy whose center row equals BASE_POS.row does not hang bricksBetween', () => {
    // M4 godai-candidates "clear-lane never targets a ring brick" hung here:
    // bricksBetween' descending loop had no bounds check, so an enemy at
    // row === BASE_POS.row walked r = 23, 22, ... forever.
    const w = buildWorld(0)
    for (let r = 18; r <= 22; r++) w.tileMap.destroy(12, r)
    const e = addEnemy(w, 12, BASE_POS.row) // (12,24) — the base row itself
    e.lastFire = 0
    expect(enemyDeadline(w, e).directThreat).toBe(true) // cbr via the ring column
  })

  it('an enemy below the base row also terminates (r runs to the grid edge)', () => {
    const w = buildWorld(0)
    const e = addEnemy(w, 12, BASE_POS.row + 1) // (12,25) — the base cell
    e.lastFire = 0
    const dl = enemyDeadline(w, e)
    expect(Number.isFinite(dl.enemyDamageDeadline)).toBe(true)
  })
})

describe('coordinate protocol (open-test protocol §4.3)', () => {
  it('tankCenterCell = corner cell + 1 on both axes (32px tank on 16px cells)', () => {
    // createTank(x, y) centers the 32px tank at x+16: corner (x/16, y/16),
    // center ((x+16)/16, (y+16)/16) — the center cell is corner + 1 always.
    const w = buildWorld(0)
    for (const x of [0, 8, 15, 16, 32, 100, 207]) {
      const t = w.createTank('basic', x, 32, 'up')
      const c = tankCenterCell(t)
      expect(c.col).toBe(Math.floor(x / 16) + 1)
    }
  })

  it('±1px jitter around a cell midpoint: floor conventions stable, round flips', () => {
    // Tank CENTERED in cell col C: x = C*16 − 8 (center at C*16). The two
    // floor-based conventions are jitter-stable; Navigator's round(x/16)
    // flips AT the midpoint itself (x/16 = C−0.5) — the §210 lesson, and the
    // reason CoveragePlanner uses floor.
    const w = buildWorld(0)
    const C = 10
    for (const dx of [-1, 0, 1]) {
      const t = w.createTank('basic', C * 16 - 8 + dx, 160, 'up')
      expect(tankCenterCell(t).col).toBe(C) // center floor — stable
      expect(Math.floor(t.x / 16)).toBe(C - 1) // corner floor — stable
    }
    // round() pinned exactly at and around the midpoint (documented flip).
    expect(Math.round((C * 16 - 8 - 1) / 16)).toBe(C - 1)
    expect(Math.round((C * 16 - 8) / 16)).toBe(C) // midpoint rounds up
    expect(Math.round((C * 16 - 8 + 1) / 16)).toBe(C)
  })

  it('cell-boundary placement is deterministic in every convention (no oscillation)', () => {
    const w = buildWorld(0)
    // Exactly ON the boundary x = C*16: corner-floor = C, center = C+1 —
    // pinned exactly, so a fixed x can never flip between runs.
    const t = w.createTank('basic', 10 * 16, 160, 'up')
    expect(Math.floor(t.x / 16)).toBe(10)
    expect(tankCenterCell(t).col).toBe(11)
    expect(Math.round(t.x / 16)).toBe(10)
    // 1px BEFORE the boundary: all three agree with the previous cell.
    const t2 = w.createTank('basic', 10 * 16 - 1, 160, 'up')
    expect(Math.floor(t2.x / 16)).toBe(9)
    expect(tankCenterCell(t2).col).toBe(10)
    expect(Math.round(t2.x / 16)).toBe(10)
  })
})
