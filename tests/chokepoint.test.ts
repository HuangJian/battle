import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import type { GodAIParams } from '../src/ai/GodAIInput'
import { RNG } from '../src/utils/RNG'
import { RULES } from '../src/config/rules'
import { DIFFICULTIES } from '../src/config/difficulty'
import { GRID, TANK } from '../src/constants'
import { chokepointCoversEnemy } from '../src/ai/god/StrategyPlanner'
import type { StageData, Tank } from '../src/types'

// ================================================================
// §88 — 据守咽喉要地 (chokepoint holding, user request 2026-08-02).
//
// Definitions (from the request):
//   1. 威胁点 (threat point)  = a cell from which an enemy can DIRECTLY
//      shoot the base — the `canShootBaseFrom` predicate.
//   2. 威胁路径 (threat path) = the A* corridor route from an enemy to its
//      NEAREST threat point, gated by rule 3 (the enemy's turret must point
//      along the path's dominant direction).
//   3. 咽喉要地 (chokepoint)  = the lower-half cell (row >= chokepointMinRow)
//      that can shoot the most threat paths; ties break toward steel/brick
//      cover (steel weight >> brick weight), then nearest the base.
//
// Strategy (rule 2): base NOT under threat → hold the chokepoint while
// swarmed (enemies > chokepointHoldThreshold), chase the enemy nearest a
// threat point otherwise. Rule 4 reorders §87: HIGH pickup > 回防 > MID
// pickup > 据守.
//
// Gated by chokepointMode (default 1 = ON since DECISIONS §94, 2026-08-03;
// 0 = OFF, byte-identical to pre-§88). The A/B validation lives at the tool
// level (eval-suite --compare + per-seed tick-diff); these tests lock the
// primitives + the shipped default.
// ================================================================

/** §88 OFF parameter set (explicit mode OFF — default is ON since §94). */
function offParams(): GodAIParams {
  return { ...DEFAULT_GOD_AI_PARAMS, chokepointMode: 0 }
}

/** §88 A/B candidate parameter set (user-spec targets, mode ON). */
function onParams(): GodAIParams {
  return {
    ...DEFAULT_GOD_AI_PARAMS,
    chokepointMode: 1,
    threatPointMargin: 1,
    chokepointHoldThreshold: 2,
    chokepointMinRow: 13,
    chokepointSteelWeight: 10,
    chokepointBrickWeight: 1,
    chokepointFacingGate: 1,
    chokepointPathsPerEnemy: 4,
    chokepointMaxThreatDist: 14,
    chokepointReplanTicks: 30,
    chokepointChaseMaxDist: 3,
    chokepointHoldMaxDist: 6,
    chokepointChaseMaxPlayerDist: 10,
  }
}

/** §88 candidate set + §87 urgent pickup ON (rule-4 chain tests). */
function onAll(): GodAIParams {
  return {
    ...onParams(),
    pickupPriorityMode: 1,
    pickupPriorityHighRange: 8,
    pickupPriorityMidRange: 4,
    pickupPriorityLowRange: 2,
    pickupPriorityMaxDanger: 0,
    pickupPriorityMinEnemyDist: 5,
    pickupPrioritySpawnRowMax: 3,
  }
}

/** Empty 26×26 arena with the classic base (eagle) at rows 24-25, cols 12-13. */
function makeEmptyStage(): StageData {
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) {
    let row = ''
    for (let c = 0; c < GRID; c++) row += '.'
    if (r === 24 || r === 25) row = row.slice(0, 12) + 'EE' + row.slice(14)
    tiles.push(row)
  }
  return { id: 9999, name: 'Empty Arena', tiles, enemies: ['basic'] }
}

/** Empty arena with the given cells set to 's' (steel). */
function steelArena(steelCells: [number, number][]): StageData {
  const grid = makeEmptyStage().tiles.map((r) => r.split(''))
  for (const [c, r] of steelCells) grid[r][c] = 's'
  return { id: 9998, name: 'Steel Arena', tiles: grid.map((r) => r.join('')), enemies: ['basic'] }
}

/**
 * Player parked at cell (pcx, pcy) (tank top-left = cell*16), no enemies,
 * God AI constructed with the given params and reset. Callers add enemies
 * as needed.
 */
function setup(
  params: GodAIParams = DEFAULT_GOD_AI_PARAMS,
  pcx = 6,
  pcy = 20,
  stage: StageData = makeEmptyStage(),
): { world: World; ai: GodAIInput } {
  const world = new World()
  world.rng = new RNG(42)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = { ...RULES['classic'] }
  world.state = 'playing'
  world.coop = false
  world.loadStageData(stage, 0)
  world.spawnQueue = []
  world.tanks = []
  world.enemiesSpawned = 0
  world.enemiesTotal = 1
  world.enemiesRemaining = 0
  const p = world.player!
  p.spawnTimer = 0
  p.shieldTimer = 0
  p.x = pcx * 16
  p.y = pcy * 16
  p.dir = 'up'
  const ai = new GodAIInput(world, params, new RNG(0x1234))
  ai.reset()
  return { world, ai }
}

/** Enemy at the given grid cell (basic, fully spawned, facing `dir`). */
function placeEnemy(world: World, col: number, row: number, dir: Tank['dir']): Tank {
  const e = world.createTank('basic', col * 16, row * 16, dir)
  e.spawnTimer = 0
  world.tanks.push(e)
  return e
}

// ---------------------------------------------------------------- params

describe('§88 params — shipped default (mode ON since DECISIONS §94)', () => {
  it('defaults: chokepointMode ON with the A/B-tuned candidate values locked', () => {
    expect(DEFAULT_GOD_AI_PARAMS.chokepointMode).toBe(1)
    // A/B round 3: margin 2 → 1 (margin=2 pulled the player off kills too
    // often on S33 Diamond).
    expect(DEFAULT_GOD_AI_PARAMS.threatPointMargin).toBe(1) // 威胁点外 1 格
    expect(DEFAULT_GOD_AI_PARAMS.chokepointHoldThreshold).toBe(2) // 敌人数目 > 2
    expect(DEFAULT_GOD_AI_PARAMS.chokepointMinRow).toBe(13) // 地图下半区
    expect(DEFAULT_GOD_AI_PARAMS.chokepointSteelWeight).toBe(10) // 钢铁 >> 砖墙
    expect(DEFAULT_GOD_AI_PARAMS.chokepointBrickWeight).toBe(1)
    expect(DEFAULT_GOD_AI_PARAMS.chokepointFacingGate).toBe(1)
    expect(DEFAULT_GOD_AI_PARAMS.chokepointPathsPerEnemy).toBe(4)
    expect(DEFAULT_GOD_AI_PARAMS.chokepointMaxThreatDist).toBe(14)
    expect(DEFAULT_GOD_AI_PARAMS.chokepointReplanTicks).toBe(30)
    // A/B round 2-3: chase imminence gate 3 格, hold march cap 6 格.
    expect(DEFAULT_GOD_AI_PARAMS.chokepointChaseMaxDist).toBe(3)
    expect(DEFAULT_GOD_AI_PARAMS.chokepointHoldMaxDist).toBe(6)
    // A/B round 3: chase player-distance cap 10 格 (speed-scaled: armor ×3,
    // basic ×2, power ×1.5, fast ×1) — a 27-cell chase is a lost race
    // (S33 seed 10) while a 25-cell armor chase is winnable (S33 seed 48).
    expect(DEFAULT_GOD_AI_PARAMS.chokepointChaseMaxPlayerDist).toBe(10)
  })
})

// ---------------------------------------------------------------- OFF inert

describe('§88 OFF (chokepointMode=0) is inert', () => {
  it('all §88 entry points return null/false even with an enemy on a threat point', () => {
    // Explicit OFF set — the shipped default is ON since §94.
    const { world, ai } = setup(offParams(), 6, 20)
    // (12,22) is a threat point (base column, clear LOS) — an enemy standing
    // there would trigger the threat state if §88 were active.
    placeEnemy(world, 12, 22, 'down')
    placeEnemy(world, 0, 5, 'right')
    placeEnemy(world, 20, 5, 'left')

    expect(ai.chokepointPlan()).toBeNull()
    expect(ai.chokepointCell()).toBeNull()
    expect(ai.isThreatState()).toBe(false)
    expect(ai.threatChaseTarget()).toBeNull()
  })
})

// ---------------------------------------------------------------- threat points

describe('§88 威胁点 (threat points)', () => {
  it('empty arena: base column + base rows are threat points; interior cells are not', () => {
    const { world, ai } = setup(onParams(), 6, 20)
    placeEnemy(world, 2, 10, 'right')
    const plan = ai.chokepointPlan()!
    expect(plan.threatPoints.length).toBeGreaterThan(0)
    // Same column as the base (col 12), clear vertical LOS.
    expect(plan.threatPoints.some((t) => t.col === 12 && t.row === 10)).toBe(true)
    // Bottom base rows: horizontal LOS clear.
    expect(plan.threatPoints.some((t) => t.col === 0 && t.row === 24)).toBe(true)
    // A mid-map interior cell with no alignment to the base is NOT a threat point.
    expect(plan.threatPoints.some((t) => t.col === 5 && t.row === 10)).toBe(false)
  })

  it('steel in front of the base breaks the column-12 LOS for cells ABOVE it', () => {
    const stage = steelArena([
      [12, 20],
      [13, 20],
    ]) // steel across the base column at row 20
    const { world, ai } = setup(onParams(), 6, 20, stage)
    placeEnemy(world, 2, 10, 'right')
    const plan = ai.chokepointPlan()!
    // Cells above the steel: LOS to the base is broken → NOT threat points.
    expect(plan.threatPoints.some((t) => t.col === 12 && t.row === 10)).toBe(false)
    // Cells below the steel (rows 21-22, clear line to the base): still threat
    // points. (12,23) is excluded by the 2×2 footprint overlapping the base.
    expect(plan.threatPoints.some((t) => t.col === 12 && t.row === 21)).toBe(true)
  })
})

// ---------------------------------------------------------------- chokepoint

describe('§88 咽喉要地 (chokepoint selection)', () => {
  it('single enemy facing along its threat path → lower-half chokepoint (12,22)', () => {
    const { world, ai } = setup(onParams(), 6, 20)
    placeEnemy(world, 2, 10, 'right') // path: row 10 → column 12
    const plan = ai.chokepointPlan()!
    expect(plan.chokepoint).toEqual({ col: 12, row: 22 })
  })

  it('rule 3 facing gate: enemy facing AWAY → no threat path → no chokepoint; gate OFF restores it', () => {
    const { world, ai } = setup(onParams(), 6, 20)
    placeEnemy(world, 2, 10, 'up') // turret away from every threat point
    expect(ai.chokepointPlan()!.chokepoint).toBeNull()

    // Same geometry, gate disabled → the path is counted → chokepoint appears.
    world.frame += 31 // force plan recompute (throttled cache)
    ai.params.chokepointFacingGate = 0
    expect(ai.chokepointPlan()!.chokepoint).toEqual({ col: 12, row: 22 })
  })

  it('rule 5 cover tie-break: steel-adjacent cell beats the base-closest cell', () => {
    // Empty arena → (12,22) (cover 0, closest to base).
    // Steel at (11,20),(11,21) → (12,21) has two steel in its ring (cover 20)
    // and beats (12,22) (cover 10) even though it is farther from the base.
    const { world, ai } = setup(
      onParams(),
      6,
      20,
      steelArena([
        [11, 20],
        [11, 21],
      ]),
    )
    placeEnemy(world, 2, 10, 'right')
    expect(ai.chokepointPlan()!.chokepoint).toEqual({ col: 12, row: 21 })
  })

  it('steel-sealed base front → no column approach, no chokepoint (falls through to normal defense)', () => {
    const steel = [] as [number, number][]
    for (let c = 11; c <= 14; c++) steel.push([c, 23])
    const { world, ai } = setup(onParams(), 6, 20, steelArena(steel))
    placeEnemy(world, 2, 10, 'right')
    expect(ai.chokepointPlan()!.chokepoint).toBeNull()
  })
})

// ---------------------------------------------------------------- threat state

describe('§88 rule 1 — 基地受威胁状态', () => {
  it('enemy ON a threat point (within margin) → threat state + isBaseUnderThreat true', () => {
    const { world, ai } = setup(onParams(), 6, 20)
    placeEnemy(world, 12, 22, 'down')
    expect(ai.isThreatState()).toBe(true)
    expect(ai.isBaseUnderThreat()).toBe(true)
  })

  it('enemy 3+ cells from every threat point → no threat state', () => {
    const { world, ai } = setup(onParams(), 6, 20)
    placeEnemy(world, 0, 20, 'right') // nearest threat point (12,20) is 12 away
    expect(ai.isThreatState()).toBe(false)
    expect(ai.isBaseUnderThreat()).toBe(false)
  })

  it('rule 3 facing gate: enemy NEAR a threat point but facing AWAY from the base → no threat state', () => {
    // A/B round 3 (S27 seed 12): an armor at (12,12) facing RIGHT — the base
    // is BELOW at (12,24) — tripped the margin check, dragged the player 14
    // cells to "intercept" a non-threat, and B lost while A won by ignoring
    // it. The facing gate anchors on the base: a tank only fires along its
    // turret axis, so a tank facing away is not about to shoot the base.
    const { world, ai } = setup(onParams(), 6, 20)
    placeEnemy(world, 12, 15, 'right') // 1 cell from threat point (12,14)…
    // (12,14) is a threat point (base column, clear LOS) — but the enemy
    // faces RIGHT, away from the base below. Facing gate rejects it.
    expect(ai.isThreatState()).toBe(false)
    expect(ai.threatChaseTarget()).toBeNull()

    // Same geometry, facing DOWN (toward the base) → threat state fires.
    const e = world.tanks.find((t) => !t.isPlayer)!
    e.dir = 'down'
    expect(ai.isThreatState()).toBe(true)
    expect(ai.threatChaseTarget()).toEqual({ col: 12, row: 15 })
  })

  it('rule 3 facing gate on chase: enemy facing away from the base is skipped even within chaseMaxDist', () => {
    const { world, ai } = setup(onParams(), 6, 20)
    // (12,15): 0 cells from threat point (12,15) — but faces RIGHT (away).
    placeEnemy(world, 12, 15, 'right')
    expect(ai.threatChaseTarget()).toBeNull()
  })

  it('chase player-distance cap: fast enemy 20 cells away is a lost race → no chase (fall through to normal hunt)', () => {
    // A/B round 3 (S33 seed 10): chase sent the player from (8,3) on a
    // 27-cell march to intercept a power at (0,22) — the enemy reached the
    // threat point first and the march derailed the game. The cap is
    // speed-scaled (fast ×1 = 10, power ×1.5 = 15, basic ×2 = 20, armor ×3
    // = 30): a distant FAST enemy is never intercepted, while a slow armor
    // 25 cells out is still winnable (S33 seed 48).
    const { world, ai } = setup(onParams(), 6, 20)
    // Fast at (12,24)-adjacent… place a fast tank 20 cells from the player
    // near a threat point: (0,10) faces DOWN toward the base row threat
    // points. Player at (6,20) is 16 cells away → fast cap 10 blocks it.
    const e = world.createTank('fast', 0 * 16, 10 * 16, 'down')
    e.spawnTimer = 0
    world.tanks.push(e)
    expect(ai.threatChaseTarget()).toBeNull()
    // Normal hunt still targets the nearest enemy (not a §88 diversion).
    const pc = ai.playerCell()
    expect(ai.selectTarget(pc)).toEqual({ col: 0, row: 10 })
  })

  it('chase player-distance cap is speed-scaled: an armor 25 cells out IS still chased (S33 seed 48)', () => {
    const { world, ai } = setup(onParams(), 12, 7)
    // Armor at (2,22) facing right (toward the base) — 25 cells from the
    // player at (12,7). Armor cap = 10 × 3 = 30 → chase fires. (createTank
    // 'armor' — placeEnemy always makes a basic, whose cap is only 20.)
    const e = world.createTank('armor', 2 * 16, 22 * 16, 'right')
    e.spawnTimer = 0
    world.tanks.push(e)
    expect(ai.threatChaseTarget()).toEqual({ col: 2, row: 22 })
  })
})

// ---------------------------------------------------------------- hold vs chase

describe('§88 rule 2 — 据守 vs 追杀', () => {
  it('enemies > holdThreshold (2) + imminent threat + hold cell within reach → selectTarget = chokepoint', () => {
    // Player (12,16) — 6 cells from the hold cell (12,22), inside HoldMaxDist.
    // Enemy (10,15) sits in the hold window: 2 cells from the nearest threat
    // point (12,15) — inside chaseMaxDist 3 (imminent) but OUTSIDE
    // threatPointMargin 1, and row 15 < 18 so the base box doesn't fire.
    // baseUnderThreat stays false → the hold arm runs.
    const { world, ai } = setup(onParams(), 12, 16)
    placeEnemy(world, 10, 15, 'down') // hold-window enemy (2 cells from tp)
    placeEnemy(world, 0, 5, 'right')
    placeEnemy(world, 24, 5, 'left')
    expect(ai.isBaseUnderThreat()).toBe(false)
    const pc = ai.playerCell()
    expect(ai.selectTarget(pc)).toEqual({ col: 12, row: 22 })
  })

  it('hold cell farther than HoldMaxDist → falls through to the chase arm target', () => {
    // Player (4,16): the hold cell (12,22) is 14 cells away — beyond the
    // 6-cell march cap. With the hold-window enemy, the chase arm (same
    // enemy) takes over instead of marching.
    const { world, ai } = setup(onParams(), 4, 16)
    placeEnemy(world, 10, 15, 'down') // hold-window enemy (2 cells from tp)
    placeEnemy(world, 0, 5, 'right')
    placeEnemy(world, 24, 5, 'left')
    const pc = ai.playerCell()
    expect(ai.selectTarget(pc)).toEqual({ col: 10, row: 15 })
  })

  it('rule 1 outranks hold: imminent enemy NOT coverable from the chokepoint → chase it directly', () => {
    // A/B round 3 (S33 seed 23): the hold arm marched the player to
    // chokepoint (15,18) while a fast tank at (24,22) headed for the base
    // through a lane the chokepoint could NOT shoot — the fast broke
    // through. When the chokepoint can't cover the imminent enemy's
    // approach (same row/col with clear LOS to the enemy or its nearest
    // threat point), the chase arm wins over the hold arm.
    //
    // Geometry: player (12,16), chokepoint (12,22) (empty-arena default).
    // Enemy (0,21) faces RIGHT toward the base (base at (12,24) is
    // right-down): its nearest threat point is (0,24) — 3 cells away,
    // inside chaseMaxDist 3, but OUTSIDE threatPointMargin 1, so the
    // base-threat state stays false. The chokepoint (12,22) shares neither
    // a row nor a column with (0,21) or (0,24) → NOT covered → chase wins.
    const { world, ai } = setup(onParams(), 12, 16)
    placeEnemy(world, 0, 21, 'right') // hold-window enemy, uncovered lane
    placeEnemy(world, 0, 5, 'right')
    placeEnemy(world, 24, 5, 'left')
    expect(ai.isBaseUnderThreat()).toBe(false)
    const pc = ai.playerCell()
    expect(ai.threatChaseTarget()).toEqual({ col: 0, row: 21 })
    expect(ai.selectTarget(pc)).toEqual({ col: 0, row: 21 })
  })

  it('rule 1 yields to hold: imminent enemy IS coverable from the chokepoint → hold fires', () => {
    // Control for the coverage gate: the imminent enemy IS in the
    // chokepoint's firing line → the hold arm keeps the player at the
    // chokepoint instead of chasing. Enemy (10,15) faces DOWN (toward the
    // base): its nearest threat point is (12,15), which shares COLUMN 12
    // with the chokepoint (12,22) → covered → hold fires.
    const { world, ai } = setup(onParams(), 12, 16)
    placeEnemy(world, 10, 15, 'down') // hold-window enemy, covered lane
    placeEnemy(world, 0, 5, 'right')
    placeEnemy(world, 24, 5, 'left')
    expect(ai.isBaseUnderThreat()).toBe(false)
    const pc = ai.playerCell()
    const plan = ai.chokepointPlan()!
    expect(plan.chokepoint).toEqual({ col: 12, row: 22 })
    expect(ai.selectTarget(pc)).toEqual({ col: 12, row: 22 })
  })

  it('enemies <= holdThreshold → chase the enemy within chaseMaxDist of a threat point', () => {
    const { world, ai } = setup(onParams(), 6, 20)
    // (10,15): 2 cells from threat point (12,15) — inside chaseMaxDist 3
    // (imminent) but outside threatPointMargin 1 and the base box (row
    // 15 < 18), so baseUnderThreat stays false and the chase arm runs.
    placeEnemy(world, 10, 15, 'down')
    placeEnemy(world, 0, 5, 'right') // far from every threat point
    const pc = ai.playerCell()
    expect(ai.selectTarget(pc)).toEqual({ col: 10, row: 15 })
  })

  it('no enemy within chaseMaxDist of a threat point → chase returns null (falls through to normal hunt)', () => {
    const { world, ai } = setup(onParams(), 6, 20)
    placeEnemy(world, 0, 5, 'right') // 12+ cells from any threat point
    placeEnemy(world, 20, 5, 'left')
    const pc = ai.playerCell()
    expect(ai.threatChaseTarget()).toBeNull()
    // Falls through to the normal nearest-enemy chase (not the §88 branch).
    expect(ai.selectTarget(pc)).toEqual({ col: 0, row: 5 })
  })
})

// ---------------------------------------------------------------- think + rule 4

describe('§88 think() integration + rule-4 priority chain', () => {
  it('hold fires the chokepoint branch (navigate to the held cell)', () => {
    // Player (12,16) — 6 cells from the hold cell (12,22), inside HoldMaxDist.
    // The hold-window enemy (10,13) is 2 cells from a threat point (inside
    // chaseMaxDist) but outside threatPointMargin 1, and row 13 < 18, so the
    // base box does NOT fire — baseUnderThreat stays false, hold arm runs.
    // NOTE: the enemy must ALSO sit OUTSIDE the player's firing band (a
    // (10,15)-style placement is 2-left-1-up and the T2a stop-and-aim branch
    // wins before the hold arm — correct rule-3 behavior, but not this test).
    const { world, ai } = setup(onParams(), 12, 16)
    placeEnemy(world, 10, 13, 'down') // hold-window enemy, out of firing band
    placeEnemy(world, 0, 5, 'right')
    placeEnemy(world, 24, 5, 'left')
    expect(ai.isBaseUnderThreat()).toBe(false)
    const dir = ai.getMoveDirection()
    expect(dir).not.toBeNull()
    expect(ai.branchCounts.chokepoint).toBeGreaterThanOrEqual(1)
  })

  it('HIGH pickup (bomb 8格) outranks 回防 — diverts even while the base is threatened', () => {
    const { world, ai } = setup(onAll(), 6, 20)
    world.addPowerUp({
      id: 900,
      type: 'bomb',
      x: 8 * 16,
      y: 20 * 16,
      w: TANK,
      h: TANK,
      alive: true,
      blinkTimer: 0,
      lifeTimer: 0,
    })
    placeEnemy(world, 12, 22, 'down') // on a threat point → base threatened
    const dir = ai.getMoveDirection()
    expect(ai.branchCounts.powerup).toBe(1) // HIGH tier diverted to the bomb
    expect(dir).not.toBeNull()
  })

  it('MID pickup (star 4格) does NOT defer to 回防 — §87 safe-pickup invariant kept', () => {
    // A/B round 3 finding (S33 seed 17): gating MID pickup on
    // isBaseUnderThreat made the player ABANDON a 3-cell star to "defend" —
    // the old base box fired while the star was safe to grab, and the player
    // lost. The §87 urgent-pickup gates (nearby-enemy 5 格, route-danger,
    // A*-reachability) already make a close pickup safe; rule 4's
    // "回防基地 > 星星/加命/护盾" is honored by HIGH > base-defense and
    // MID > 据守, not by abandoning point-blank pickups.
    const { world, ai } = setup(onAll(), 6, 20)
    world.addPowerUp({
      id: 901,
      type: 'star',
      x: 8 * 16,
      y: 20 * 16,
      w: TANK,
      h: TANK,
      alive: true,
      blinkTimer: 0,
      lifeTimer: 0,
    })
    placeEnemy(world, 12, 22, 'down') // on a threat point → base threatened
    const dir = ai.getMoveDirection()
    expect(ai.branchCounts.powerup).toBe(1) // MID tier still diverts (safe pickup)
    expect(dir).not.toBeNull()
  })

  it('enemy ON the chokepoint cell → covered, no OOB crash (60-seed regression, DECISIONS §101)', () => {
    const { ai } = setup(onParams(), 6, 20)
    // Same-cell LOS: the zero-length walk used to step off-grid forever
    // (grid[-1] undefined crash, exposed by 60-seed chaos seeds > 20).
    expect(chokepointCoversEnemy(ai, { col: 12, row: 20 }, { col: 12, row: 20 })).toBe(true)
    // Sanity: clear vertical LOS one cell away still works (empty arena).
    expect(chokepointCoversEnemy(ai, { col: 12, row: 20 }, { col: 12, row: 22 })).toBe(true)
    // Out-of-bounds target → not covered (no crash).
    expect(chokepointCoversEnemy(ai, { col: 12, row: 20 }, { col: 12, row: 30 })).toBe(false)
  })

  it('MID pickup outranks 据守 — diverts when the base is NOT threatened', () => {
    const { world, ai } = setup(onAll(), 6, 20)
    world.addPowerUp({
      id: 902,
      type: 'star',
      x: 8 * 16,
      y: 20 * 16,
      w: TANK,
      h: TANK,
      alive: true,
      blinkTimer: 0,
      lifeTimer: 0,
    })
    // No enemies: base safe → MID tier diverts to the star (before chokepoint hold).
    const dir = ai.getMoveDirection()
    expect(ai.branchCounts.powerup).toBe(1)
    expect(dir).not.toBeNull()
  })
})
