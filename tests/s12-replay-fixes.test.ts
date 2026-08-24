import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { CLASSIC_MODEL_PARAMS } from '../src/ai/god/params'
import { bulletPathSteelBlockedImpl } from '../src/ai/god/FireControl'
import { findUrgentPowerUpTargetWithCommitImpl } from '../src/ai/god/StrategyPlanner'
import { thinkImpl } from '../src/ai/god/think'
import { RNG } from '../src/utils/RNG'
import { RULES } from '../src/config/rules'
import { DIFFICULTIES } from '../src/config/difficulty'
import { CELL, GRID } from '../src/constants'
import type { StageData, PowerUpType } from '../src/types'

// ================================================================
// §152 — hard S12 Lattice seed 934391936 replay fixes.
//
// Four root causes were diagnosed from the browser replay + headless
// reproduction (gameover tick 8272, base destroyed):
//
//   W1 (t3540-3660, 0:59-1:01) — steel-blocked stop-and-aim: the player
//     stopped at (17,18) with its center x=288 exactly on the col-17/18
//     boundary and fired up at a fast enemy at (17,3). The scan's dual ±8px
//     offset lines saw the enemy, but the bullet's ACTUAL 6px box [285,291]
//     clipped the steel column 18 [288,304) at rows 8-9 and died there —
//     the T2a/aggressive stop-and-aim gates only checked baseWall/baseSteel,
//     so the fire was wasted. FIX: t2aSteelPathBlock (bulletPathSteelBlocked).
//
//   W2 (t3840-4560, 1:04-1:16) — aggressive-branch movement oscillation: A*
//     ignores tanks, so a frozen enemy's body made the path first-step
//     blocked every replan; followPath's fallback ping-ponged at
//     (8,16)↔(8,17) for 720+ ticks. FIX: aggNavStuckTicks (zone-based stuck
//     guard → navigate-to-center escape).
//
//   W3 (t5880-6960, 1:38-1:56) — urgent-pickup oscillation: the decoy at
//     (21,14) sat exactly at the mid-range boundary (4 = pickupPriorityMidRange);
//     from (21,18) dist=4 (commit), from (22,18) dist=5 (skip) — the player
//     ping-ponged ~800 ticks. FIX: pickupCommitTicks (commit persistence).
//
//   W4 (t7502-8272, 2:05) — post-pickup freeze: the decoy spawned ON the
//     player's cell and boxed it in (tankHitsTank treats spawning tanks as
//     blockers) for the final 770 ticks. FIX: decoy spawns at a clear nearby
//     cell instead (SimulationPlayer.decoySpawnCell).
// ================================================================

/** Empty 26×26 arena with the classic base + 8-brick protection ring. */
function ringArena(): StageData {
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) {
    let row = ''
    for (let c = 0; c < GRID; c++) row += '.'
    if (r === 24 || r === 25) row = row.slice(0, 12) + 'EE' + row.slice(14)
    if (r === 23) row = row.slice(0, 11) + 'bbbb' + row.slice(15)
    if (r === 24 || r === 25) row = row.slice(0, 11) + 'b' + row.slice(12)
    if (r === 24 || r === 25) row = row.slice(0, 14) + 'b' + row.slice(15)
    tiles.push(row)
  }
  return { id: 9996, name: 'Ring Arena', tiles, enemies: ['basic'] }
}

/** Arena with a single steel column at a caller-chosen cell. */
function steelArena(steelCol: number, steelRow: number): StageData {
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) {
    let row = ''
    for (let c = 0; c < GRID; c++) row += c === steelCol && r === steelRow ? 's' : '.'
    tiles.push(row)
  }
  return { id: 9997, name: 'Steel Arena', tiles, enemies: ['basic'] }
}

function setup(stage?: StageData): { world: World; ai: GodAIInput; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(42)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = { ...RULES['hard'] }
  world.state = 'playing'
  world.coop = false
  world.loadStageData(stage ?? ringArena(), 0)
  world.spawnQueue = []
  world.tanks = []
  world.enemiesSpawned = 0
  world.enemiesTotal = 1
  world.enemiesRemaining = 0
  const p = world.player!
  p.spawnTimer = 0
  p.shieldTimer = 0
  const sim = new Simulation(world, new Input())
  const ai = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, new RNG(0x1234))
  ai.reset()
  return { world, ai, sim }
}

function placeItem(world: World, type: PowerUpType, col: number, row: number): void {
  world.addPowerUp({
    id: 9000 + col * 100 + row,
    type,
    x: col * CELL,
    y: row * CELL,
    w: 32,
    h: 32,
    alive: true,
    blinkTimer: 0,
    lifeTimer: 0,
  })
}

// ----------------------------------------------------------------
// W1 — t2aSteelPathBlock: bullet-path steel gate for stop-and-aim
// ----------------------------------------------------------------
describe('§152-W1 — bulletPathSteelBlocked (steel-blocked stop-and-aim)', () => {
  it('params: t2aSteelPathBlock defaults ON in hard/chaos pool model, OFF in classic', () => {
    expect(DEFAULT_GOD_AI_PARAMS.t2aSteelPathBlock).toBe(1)
    expect(CLASSIC_MODEL_PARAMS.t2aSteelPathBlock).toBe(0)
  })

  it('a steel column ON the bullet center line blocks the shot (the W1 case)', () => {
    // Player at cell (17,18), steel at (18,9) — the W1 geometry. Center
    // x=288 is exactly on the col-17/18 boundary, so the 6px box [285,291]
    // clips the steel column [288,304).
    const { world, ai } = setup(steelArena(18, 9))
    const p = world.player!
    p.x = 17 * CELL
    p.y = 18 * CELL
    p.level = 0
    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    // 15 cells of range → the walk must cross row 9.
    expect(bulletPathSteelBlockedImpl(ai, pcx, pcy, 'up', 15 * CELL)).toBe(true)
  })

  it('steel NOT on the center line does not block (offset-only steel)', () => {
    // Steel at (18,9) but the player center at x=296 (col 18 center): the
    // 6px box [293,299] stays inside col 18, and the steel IS in col 18
    // here... so use steel at (19,9) — 2 cells right of the boundary.
    const { world, ai } = setup(steelArena(19, 9))
    const p = world.player!
    p.x = 17 * CELL
    p.y = 18 * CELL
    p.level = 0
    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    expect(bulletPathSteelBlockedImpl(ai, pcx, pcy, 'up', 15 * CELL)).toBe(false)
  })

  it('base-RING steel is skipped (the baseWall/baseSteel gates own the ring)', () => {
    // Ring steel at (11,23) — the base ring row (BASE_POS (12,24) ring:
    // row 23 cols 11-14). Firing down from (11,21) with maxDist 3 cells the
    // walk only crosses rows 22 (empty), 23 (ring steel — skipped) and 25
    // (empty) — never OOB, never a non-ring steel.
    const { world, ai } = setup(steelArena(11, 23))
    const p = world.player!
    p.x = 11 * CELL
    p.y = 21 * CELL
    p.level = 0
    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    expect(bulletPathSteelBlockedImpl(ai, pcx, pcy, 'down', 3 * CELL)).toBe(false)

    // Paired control: the SAME geometry with NON-ring steel at (11,22) MUST
    // block — proves the walk really inspects that column, and only the ring
    // exemption let the previous case through.
    const { world: w2, ai: ai2 } = setup(steelArena(11, 22))
    const p2 = w2.player!
    p2.x = 11 * CELL
    p2.y = 21 * CELL
    p2.level = 0
    const pcx2 = p2.x + p2.w / 2
    const pcy2 = p2.y + p2.h / 2
    expect(bulletPathSteelBlockedImpl(ai2, pcx2, pcy2, 'down', 3 * CELL)).toBe(true)
  })

  it('level ≥ 3 pierces non-ring steel (steel-pierce)', () => {
    const { world, ai } = setup(steelArena(18, 9))
    const p = world.player!
    p.x = 17 * CELL
    p.y = 18 * CELL
    p.level = 3
    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    expect(bulletPathSteelBlockedImpl(ai, pcx, pcy, 'up', 15 * CELL)).toBe(false)
  })

  it('the bullet dies at the field edge (OOB = steel, TileMap.get fallback)', () => {
    const { world, ai } = setup(ringArena())
    const p = world.player!
    p.x = 8 * CELL
    p.y = 3 * CELL
    p.level = 0
    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    // Firing up toward row 0 — the box leaves the field before any terrain.
    expect(bulletPathSteelBlockedImpl(ai, pcx, pcy, 'up', 12 * CELL)).toBe(true)
  })
})

// ----------------------------------------------------------------
// W2 — aggNavStuckTicks: aggressive-branch movement stuck guard
// ----------------------------------------------------------------
describe('§152-W2 — aggressive movement-stuck guard (aggNavStuckTicks)', () => {
  it('params: aggNavStuckTicks defaults ON in pool model, OFF in classic', () => {
    expect(DEFAULT_GOD_AI_PARAMS.aggNavStuckTicks).toBe(120)
    expect(CLASSIC_MODEL_PARAMS.aggNavStuckTicks).toBe(0)
  })

  it('a freeze-window stall trips the escape → navigate toward map center', () => {
    const { world, ai } = setup()
    const p = world.player!
    p.x = 6 * CELL
    p.y = 10 * CELL
    world.freezeTimer = 1000 // aggressive mode
    // Pre-arm the stuck state at the threshold: same zone, no kills.
    const st = ai._aggNavTrack
    st.cell = { col: 6, row: 10 }
    st.ticks = DEFAULT_GOD_AI_PARAMS.aggNavStuckTicks
    st.killsAtStart = world.killCount
    st.suppress = 0
    ai._thought = false
    thinkImpl(ai)
    // The guard increments past the threshold and commits the escape.
    expect(ai._aggNavTrack.suppress).toBeGreaterThan(0)
    expect(ai._moveDir).not.toBeNull()
    expect(ai._lastBranch).toBe('aggressive')
  })

  it('the escape is a one-shot navigate-to-center for the suppress window', () => {
    const { world, ai } = setup()
    const p = world.player!
    p.x = 6 * CELL
    p.y = 10 * CELL
    world.freezeTimer = 1000
    const st = ai._aggNavTrack
    st.cell = { col: 6, row: 10 }
    st.ticks = DEFAULT_GOD_AI_PARAMS.aggNavStuckTicks
    st.killsAtStart = world.killCount
    st.suppress = 0
    ai._thought = false
    thinkImpl(ai)
    const suppressAfter = ai._aggNavTrack.suppress
    expect(suppressAfter).toBeGreaterThan(0)
    // A kill DURING the stuck window resets the counter (no false escape).
    const { world: w2, ai: ai2 } = setup()
    const p2 = w2.player!
    p2.x = 6 * CELL
    p2.y = 10 * CELL
    w2.freezeTimer = 1000
    const st2 = ai2._aggNavTrack
    st2.cell = { col: 6, row: 10 }
    st2.ticks = DEFAULT_GOD_AI_PARAMS.aggNavStuckTicks
    w2.killCount = 5 // a kill happened since the anchor
    st2.killsAtStart = 4
    st2.suppress = 0
    ai2._thought = false
    thinkImpl(ai2)
    expect(ai2._aggNavTrack.suppress).toBe(0)
  })
})

// ----------------------------------------------------------------
// W3 — pickupCommitTicks: urgent-pickup commit persistence
// ----------------------------------------------------------------
describe('§152-W3 — urgent-pickup commit persistence (pickupCommitTicks)', () => {
  it('params: pickupCommitTicks defaults OFF (experimental knob — the 35×60 A/B + per-seed isolation showed the commit hijacks base defense on S34 Battlement and turns the S12 seed-934391936 win back into a loss; the W3 window is already fixed by W1+W2)', () => {
    expect(DEFAULT_GOD_AI_PARAMS.pickupCommitTicks).toBe(0)
    expect(CLASSIC_MODEL_PARAMS.pickupCommitTicks).toBe(0)
  })

  it('0 = OFF: byte-identical to the plain lookup (no persistence)', () => {
    const { world, ai } = setup()
    ai.params = { ...DEFAULT_GOD_AI_PARAMS, pickupCommitTicks: 0, pickupPriorityMode: 1 }
    const p = world.player!
    p.x = 6 * CELL
    p.y = 10 * CELL
    placeItem(world, 'bomb', 7, 10) // dist 1 — urgent (high tier)
    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    const t1 = findUrgentPowerUpTargetWithCommitImpl(ai, pcx, pcy, 'high')
    expect(t1).not.toBe(null)
    expect(t1!.col).toBe(7)
    // The player "moves away" → dist > range → the plain lookup cancels.
    p.x = 20 * CELL
    const t2 = findUrgentPowerUpTargetWithCommitImpl(ai, p.x + p.w / 2, pcy, 'high')
    expect(t2).toBe(null)
    expect(ai._pickupCommitActive).toBe(false)
  })

  it('an active commit survives the transient dist>range flip (the W3 case)', () => {
    const { world, ai } = setup()
    ai.params = { ...DEFAULT_GOD_AI_PARAMS, pickupCommitTicks: 300, pickupPriorityMode: 1 }
    const p = world.player!
    p.x = 6 * CELL
    p.y = 10 * CELL
    placeItem(world, 'bomb', 7, 10) // dist 1 — urgent (high tier, range 8)
    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    const t1 = findUrgentPowerUpTargetWithCommitImpl(ai, pcx, pcy, 'high')
    expect(t1).not.toBe(null)
    expect(ai._pickupCommitActive).toBe(true)
    // Player steps well past the high-tier range (8) — the plain lookup
    // would cancel, but the commit keeps the pursuit alive.
    p.x = 16 * CELL
    p.y = 10 * CELL
    const t2 = findUrgentPowerUpTargetWithCommitImpl(ai, p.x + p.w / 2, p.y + p.h / 2, 'high')
    expect(t2).not.toBe(null)
    expect(t2!.col).toBe(7)
    expect(t2!.row).toBe(10)
  })

  it('a collected/despawned item ends the pursuit immediately', () => {
    const { world, ai } = setup()
    ai.params = { ...DEFAULT_GOD_AI_PARAMS, pickupCommitTicks: 300, pickupPriorityMode: 1 }
    const p = world.player!
    p.x = 6 * CELL
    p.y = 10 * CELL
    placeItem(world, 'bomb', 7, 10)
    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    expect(findUrgentPowerUpTargetWithCommitImpl(ai, pcx, pcy, 'high')).not.toBe(null)
    expect(ai._pickupCommitActive).toBe(true)
    // Item despawns → the commit's existence re-verify fails.
    world.powerUps[0].alive = false
    const t2 = findUrgentPowerUpTargetWithCommitImpl(ai, pcx, pcy, 'high')
    expect(t2).toBe(null)
    expect(ai._pickupCommitActive).toBe(false)
  })

  it('the commit window expires after pickupCommitTicks', () => {
    const { world, ai } = setup()
    ai.params = { ...DEFAULT_GOD_AI_PARAMS, pickupCommitTicks: 300, pickupPriorityMode: 1 }
    const p = world.player!
    p.x = 6 * CELL
    p.y = 10 * CELL
    placeItem(world, 'bomb', 7, 10)
    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    expect(findUrgentPowerUpTargetWithCommitImpl(ai, pcx, pcy, 'high')).not.toBe(null)
    expect(ai._pickupCommitActive).toBe(true)
    // Burn the whole window AND move out of range: the next call clears the
    // commit and the fresh lookup (dist > range) has nothing to re-arm.
    ai._pickupCommitTicks = ai.params.pickupCommitTicks + 1
    p.x = 20 * CELL
    const t2 = findUrgentPowerUpTargetWithCommitImpl(ai, p.x + p.w / 2, pcy, 'high')
    expect(t2).toBe(null)
    expect(ai._pickupCommitActive).toBe(false)
  })
})

// ----------------------------------------------------------------
// W4 — decoy spawn placement (no more same-cell box-in)
// ----------------------------------------------------------------
describe('§152-W4 — decoy spawns at a clear cell, never on the player', () => {
  it('the decoy does NOT spawn on the player cell (the W4 box-in fix)', () => {
    const { world, sim } = (() => {
      const w = new World()
      w.rng = new RNG(6)
      w.difficultyKey = 'hard'
      w.difficulty = DIFFICULTIES['hard']
      w.rules = { ...RULES['hard'] }
      w.state = 'playing'
      w.coop = false
      w.loadStageData(ringArena(), 0)
      w.spawnQueue = []
      w.tanks = []
      w.enemiesSpawned = 0
      w.enemiesTotal = 1
      w.enemiesRemaining = 0
      const p = w.player!
      p.spawnTimer = 0
      p.shieldTimer = 0
      // Open field, well clear of walls — mirrors the S12 pickup spot.
      p.x = 6 * CELL
      p.y = 12 * CELL
      const s = new Simulation(w, new Input())
      return { world: w, sim: s }
    })()
    const p = world.player!
    const before = world.allies.length
    sim.systems.powerUps.applyPowerUp('decoy')
    expect(world.allies.length).toBe(before + 1)
    const decoy = world.allies[world.allies.length - 1]
    const sameCell =
      Math.floor(decoy.x / CELL) === Math.floor(p.x / CELL) &&
      Math.floor(decoy.y / CELL) === Math.floor(p.y / CELL)
    expect(sameCell).toBe(false)
    // The player must still be able to move in at least one direction.
    let canStep = false
    for (const [dx, dy] of [
      [0, -1],
      [0, 1],
      [-1, 0],
      [1, 0],
    ] as const) {
      const nx = p.x + dx * 4
      const ny = p.y + dy * 4
      if (world.isInBounds(nx, ny, p.w, p.h) && !world.rectHitsTerrain(nx, ny, p.w, p.h)) {
        let blocked = false
        for (const t of world.allTanks) {
          if (!t.alive || t === p) continue // the player is in allTanks too
          const overlap = nx < t.x + t.w && nx + p.w > t.x && ny < t.y + t.h && ny + p.h > t.y
          if (overlap) {
            blocked = true
            break
          }
        }
        if (!blocked) {
          canStep = true
          break
        }
      }
    }
    expect(canStep).toBe(true)
  })
})
