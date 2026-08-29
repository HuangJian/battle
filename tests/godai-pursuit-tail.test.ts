// godai-pursuit-tail.test.ts — §302 pursuit-tail navigation (追尾导航).
//
// plan/Intent-Policy-NN-Plan.md §12.1 defect #3: "追击走并行车道横向开火，
// 不并入目标车道后方" — HUNT chases by closing the ROW gap first
// (directMoveImpl's vertical-first priority), which against a vertically
// travelling enemy settles the player in a PARALLEL lane firing perpendicular
// at a sliding target. The §302 knob steers the player onto the target's lane
// instead.
//
// ⚠️ ENABLED 2026-08-29 (DECISIONS §304; renumbered from §303 on merge — the remote guard-fix commit had taken §303) — `pursuitTailMode: 7` +
// `pursuitTailAlongMode: 4` ships ON: the user-directed yield-then-tail
// redesign reached net +29 on hard 35×60, then the am=4 add-on (sticky
// locked-target keying + T2a slide preemption) added a paired net +20 over
// am=3 (OFF 1581 → am3 1612 → am4 1632 of 2100). classic
// stays OFF via CLASSIC_OVERRIDES (instant 1-HP pool never A/B'd,
// byte-identical gate).
//
// Measurement history (all hard 35×60, 2100 paired games per arm):
//
//   Round 1:  mode 1 tail-cell nav −30 · mode 2 lateral ≤4 gap 0 ·
//             mode 3 +shot gate +8 · mode 3 gap≤2 −27 · mode 6 −35
//             → all |t| ≤ 1.5 SE (SE ≈ 24) = noise, archived.
//   Round 2:  mode 7 adjacent-lane (laneGap === 2): both sides −39 ·
//             wake only +1 · level/ahead only −58 (t=−2.79, the only
//             ±2SE-significant arm of the entire program — cutting in while
//             the target is level/closing lands the player IN its path with
//             a lateral facing).
//   Round 3:  the user's fix (do NOT cut in early — hold, let the target
//             sweep past, then merge into its wake) measured across three
//             mechanic-completeness fixes: v1 hold + half-aborted slide −4 ·
//             v2 whole-slide ownership +16 · v3 + waiting out the target-body
//             PIXEL stagger (along=−2 is rounded; a mid-cell target still
//             blocks the slide footprint) +29.
//
// These tests pin the invariants that must stay true while the knob ships
// ON: the state machine only fires inside a HUNT chase window, never
// re-aims HUNT's anchor fallbacks, never walks the player backward, holds
// instead of cutting across a closing target, and waits out both the
// laneGap-1 half-slide and the target-body pixel stagger.
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import {
  pursuitTailDirImpl,
  pursuitTailSlideDir,
  pursuitTailTargetCell,
  PURSUIT_TAIL_HOLD,
} from '../src/ai/god/Navigator'
import { ARCHIVED_KNOB_GROUPS } from '../src/ai/god/params.interface'
import { MAP_CENTER } from '../src/ai/god/candidates/shared'
import { BASE_POS, CELL } from '../src/constants'
import type { Tank } from '../src/types'
import { clearArena, placeEnemy, positionPlayer, seedWorld } from './helpers'

function setupWorld(params: Partial<typeof DEFAULT_GOD_AI_PARAMS> = {}): {
  world: World
  input: GodAIInput
} {
  const world = seedWorld(42)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, ...params })
  const sim = new Simulation(world, new Input())
  world.startGame('hard', 'modern', 0)
  clearArena(world)
  void sim
  world.enemiesRemaining = 20 // canHunt false → the normal nearest-chase branch
  input.hasBase = world.tileMap.hasBase()
  input.reset()
  return { world, input }
}

/** Refresh the per-tick enemy snapshot the way endFrame/think would. */
function refresh(input: GodAIInput, world: World): void {
  input._baseUnderThreatCache = null
  input._selTargetValid = false
  input._enemies.length = 0
  for (const t of world.tanks) {
    if (t.alive && t.spawnTimer <= 0) input._enemies.push(t)
  }
}

/** Park the player at a cell and give it a facing. */
function at(world: World, col: number, row: number, dir: 'up' | 'down' | 'left' | 'right' = 'up') {
  positionPlayer(world, col, row, dir)
  return world.player!
}

/** Freeze an enemy at a cell with an explicit travel axis (velocity, not dir). */
function travelling(enemy: Tank, col: number, row: number, axis: 'up' | 'right'): Tank {
  enemy.x = col * CELL
  enemy.y = row * CELL
  enemy.vx = axis === 'right' ? 1 : 0
  enemy.vy = axis === 'up' ? -1 : 0
  enemy.dir = axis
  return enemy
}

const ON = { pursuitTailMode: 2 }
const ISOLATED = { chokepointMode: 0, baseGuardAnchorMode: 0 }

describe('§302: pursuit-tail ships ON (DECISIONS §304)', () => {
  it('DEFAULT is mode 7 + AlongMode=3 and NOT in ARCHIVED_KNOB_GROUPS', () => {
    expect(DEFAULT_GOD_AI_PARAMS.pursuitTailMode).toBe(7)
    expect(DEFAULT_GOD_AI_PARAMS.pursuitTailAlongMode).toBe(4)
    expect(ARCHIVED_KNOB_GROUPS.some((g) => g.gate === 'pursuitTailMode')).toBe(false)
  })

  it('classic stays OFF via CLASSIC_MODEL_PARAMS (byte-identical gate)', async () => {
    const { CLASSIC_MODEL_PARAMS } = await import('../src/ai/god/params.tables')
    expect(CLASSIC_MODEL_PARAMS.pursuitTailMode).toBe(0)
  })

  it('OFF: the helper returns null for every geometry (byte-identical)', () => {
    const { world, input } = setupWorld({ ...ISOLATED, pursuitTailMode: 0 })
    const e = travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    const p = at(world, 5, 8)
    const target = { col: 10, row: 5 }
    expect(pursuitTailDirImpl(input, p, { col: 5, row: 8 }, target, true)).toBeNull()
    expect(pursuitTailDirImpl(input, p, { col: 10, row: 12 }, target, false)).toBeNull()
    void e
  })
})

describe('§302: lateral merge geometry (mode 2)', () => {
  it("merges onto the target's column when it travels vertically", () => {
    // Target at (10,5) climbing; player behind (below) and off-lane to the
    // left. directMove would go UP (closing the row gap → parallel lane);
    // the merge goes RIGHT, onto the target's column.
    const { world, input } = setupWorld({ ...ISOLATED, ...ON })
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    // lane gap 3 (within the 4-cell merge budget); dist 6, behind by 3.
    const p = at(world, 7, 8)
    expect(pursuitTailDirImpl(input, p, { col: 7, row: 8 }, { col: 10, row: 5 }, true)).toBe(
      'right',
    )
  })

  it("merges onto the target's row when it travels horizontally", () => {
    const { world, input } = setupWorld({ ...ISOLATED, ...ON })
    // Behind a right-moving target means to its LEFT: player at col 6.
    travelling(placeEnemy(world, 10, 10), 10, 10, 'right')
    refresh(input, world)
    const p = at(world, 6, 14)
    expect(pursuitTailDirImpl(input, p, { col: 6, row: 14 }, { col: 10, row: 10 }, true)).toBe('up')
  })

  it('never walks the player backward — on-lane is left to the normal chase', () => {
    // Player already sits on the target's column, 6 cells behind. Mode 2 is a
    // lateral-merge-only override: steering here could only push the player
    // AWAY from the target (this is exactly the mode-1 defect, net −30).
    const { world, input } = setupWorld({ ...ISOLATED, ...ON })
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    const p = at(world, 10, 11)
    expect(pursuitTailDirImpl(input, p, { col: 10, row: 11 }, { col: 10, row: 5 }, true)).toBeNull()
  })

  it('bails when the player is AHEAD of the target on its travel axis', () => {
    // Player above a target that is climbing: merging would cut across its
    // front, so the override must not fire.
    const { world, input } = setupWorld({ ...ISOLATED, ...ON })
    travelling(placeEnemy(world, 10, 10), 10, 10, 'up')
    refresh(input, world)
    const p = at(world, 7, 4)
    expect(pursuitTailDirImpl(input, p, { col: 7, row: 4 }, { col: 10, row: 10 }, true)).toBeNull()
  })

  it('bails outside the distance window [min, max]', () => {
    const { world, input } = setupWorld({ ...ISOLATED, ...ON })
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    const p = at(world, 5, 8)
    // Too close: |5-10| + |8-5| would be 8, so walk the player adjacent.
    expect(pursuitTailDirImpl(input, p, { col: 10, row: 7 }, { col: 10, row: 5 }, true)).toBeNull()
    // Too far: lane gap 8 > pursuitTailMaxLaneGap (4).
    expect(pursuitTailDirImpl(input, p, { col: 2, row: 8 }, { col: 10, row: 5 }, true)).toBeNull()
  })

  it('never re-aims HUNT anchor fallbacks (center / defense position)', () => {
    // HUNT's nav-stuck escape targets MAP_CENTER and the §179 emergency /
    // M3 survival retreat target the defense position. Neither is a chase
    // lane — the override must treat them as "no enemy on this cell" and
    // keep its hands off.
    const { world, input } = setupWorld({ ...ISOLATED, ...ON })
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    const p = at(world, 5, 8)
    expect(pursuitTailDirImpl(input, p, { col: 5, row: 8 }, MAP_CENTER, true)).toBeNull()
    expect(
      pursuitTailDirImpl(
        input,
        p,
        { col: 5, row: 8 },
        { col: BASE_POS.col, row: BASE_POS.row },
        true,
      ),
    ).toBeNull()
  })

  it('counts overrides only when it actually steers (probe trigger rate)', () => {
    const { world, input } = setupWorld({ ...ISOLATED, ...ON })
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    const p = at(world, 5, 8)
    expect(input._pursuitTailOverrides).toBe(0)
    // The counter lives on the HUNT call site, so assert the contract the
    // probe relies on: a non-null direction is the only thing that counts.
    const d = pursuitTailDirImpl(input, p, { col: 7, row: 8 }, { col: 10, row: 5 }, true)
    expect(d).not.toBeNull()
  })
})

describe('§302: mode 3 — the merge must buy a clear shot', () => {
  it('refuses a lane whose line back to the target is walled off', () => {
    const { world, input } = setupWorld({ ...ISOLATED, pursuitTailMode: 3 })
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    // Wall the lane between the player's row (8) and the target (5).
    world.tileMap.grid[7][10] = 'steel'
    refresh(input, world)
    const p = at(world, 8, 8)
    expect(pursuitTailDirImpl(input, p, { col: 8, row: 8 }, { col: 10, row: 5 }, true)).toBeNull()
  })

  it('takes the lane when the shot line is clear', () => {
    const { world, input } = setupWorld({ ...ISOLATED, pursuitTailMode: 3 })
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    const p = at(world, 8, 8)
    expect(pursuitTailDirImpl(input, p, { col: 8, row: 8 }, { col: 10, row: 5 }, true)).toBe(
      'right',
    )
  })
})

describe('§302: determinism', () => {
  it('is a pure function of world state — no RNG consumption', () => {
    const { world, input } = setupWorld({ ...ISOLATED, ...ON })
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    const p = at(world, 5, 8)
    const before = input.rng.getState?.()
    const a = pursuitTailDirImpl(input, p, { col: 5, row: 8 }, { col: 10, row: 5 }, true)
    const b = pursuitTailDirImpl(input, p, { col: 5, row: 8 }, { col: 10, row: 5 }, true)
    const after = input.rng.getState?.()
    expect(a).toBe(b)
    if (before !== undefined) expect(after).toBe(before)
  })
})

// ---------------------------------------------------------------------------
// mode 7 — adjacent-lane merge (laneGap === 2: the tank body is 2×2 cells, so
// "neighbouring lane" is a gap of 2, not 1). The along-mode split pins the
// measured A/B arms; AlongMode=3 is the user directive (2026-08-29): do NOT
// cut in while the target is level with or closing on the player — HOLD
// (release the throttle) and let it sweep past, then merge into its wake.
// ---------------------------------------------------------------------------
const M7 = { pursuitTailMode: 7 }

describe('§302: mode 7 along-mode split — archived arms stay pinned', () => {
  it('am=1 (wake only): merges behind a receding target, bails ahead of it', () => {
    const { world, input } = setupWorld({ ...ISOLATED, ...M7, pursuitTailAlongMode: 1 })
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    // Behind (below): along = -2 → wake merge onto the target's column.
    expect(
      pursuitTailDirImpl(input, at(world, 12, 7), { col: 12, row: 7 }, { col: 10, row: 5 }, true),
    ).toBe('left')
    // Ahead (above): along = +2 → merging cuts the target's bow → bail.
    expect(
      pursuitTailDirImpl(input, at(world, 12, 3), { col: 12, row: 3 }, { col: 10, row: 5 }, true),
    ).toBeNull()
  })

  it('am=2 (level/ahead only): the measured net −58 arm keeps its old shape', () => {
    const { world, input } = setupWorld({ ...ISOLATED, ...M7, pursuitTailAlongMode: 2 })
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    expect(
      pursuitTailDirImpl(input, at(world, 12, 3), { col: 12, row: 3 }, { col: 10, row: 5 }, true),
    ).toBe('left')
    expect(
      pursuitTailDirImpl(input, at(world, 12, 7), { col: 12, row: 7 }, { col: 10, row: 5 }, true),
    ).toBeNull()
  })
})

describe('§302: mode 7 AlongMode=3 — yield-then-tail (wait, then merge)', () => {
  const AM3 = { ...ISOLATED, ...M7, pursuitTailAlongMode: 3 }

  it('HOLDS while the target is level with the player (along = 0)', () => {
    const { world, input } = setupWorld(AM3)
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    // Side by side in the neighbouring lane: cutting in now lands the player
    // on the target's flank with a lateral facing. Hold instead.
    expect(
      pursuitTailDirImpl(input, at(world, 12, 5), { col: 12, row: 5 }, { col: 10, row: 5 }, true),
    ).toBe(PURSUIT_TAIL_HOLD)
  })

  it('HOLDS while the target is closing from below (along > 0, in window)', () => {
    const { world, input } = setupWorld(AM3)
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    expect(
      pursuitTailDirImpl(input, at(world, 12, 3), { col: 12, row: 3 }, { col: 10, row: 5 }, true),
    ).toBe(PURSUIT_TAIL_HOLD)
  })

  it('bails out of the yield once the target is outside the passing window', () => {
    const { world, input } = setupWorld(AM3)
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    // along = +4 > pursuitTailAlongWindow (3): the target is not passing —
    // leave the chase to directMove.
    expect(
      pursuitTailDirImpl(input, at(world, 12, 1), { col: 12, row: 1 }, { col: 10, row: 5 }, true),
    ).toBeNull()
  })

  it('does not hold for a lane whose shot line is walled (along > 0)', () => {
    const { world, input } = setupWorld(AM3)
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    world.tileMap.grid[4][10] = 'steel'
    refresh(input, world)
    expect(
      pursuitTailDirImpl(input, at(world, 12, 3), { col: 12, row: 3 }, { col: 10, row: 5 }, true),
    ).toBeNull()
  })

  it('merges into the wake once the target has passed (along < 0)', () => {
    const { world, input } = setupWorld(AM3)
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    // The hold carried the player through the pass; now the target is 2 rows
    // ahead climbing away — merge onto its column behind it.
    expect(
      pursuitTailDirImpl(input, at(world, 12, 7), { col: 12, row: 7 }, { col: 10, row: 5 }, true),
    ).toBe('left')
  })

  it('waits for the TWO-row stagger: along = -1 holds instead of merging', () => {
    // User spec (replay review, 2026-08-29): merge only when the target is
    // 2 cells up-left — the 2×2 bodies vertically staggered — so the slide
    // cannot grind its flank. At 1 row clear the slide is collision-blocked.
    const { world, input } = setupWorld(AM3)
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    expect(
      pursuitTailDirImpl(input, at(world, 12, 6), { col: 12, row: 6 }, { col: 10, row: 5 }, true),
    ).toBe(PURSUIT_TAIL_HOLD)
  })

  it('OWNS the whole slide: the half-entered lane (gap 1) keeps merging to gap 0', () => {
    // Replay review found the first cut aborting one sub-cell short of the
    // lane (the laneGap === 2 gate dropped the override mid-slide and
    // directMove yanked the player back into the parallel chase).
    const { world, input } = setupWorld(AM3)
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    // gap 1, stagger 2: keep pressing toward the lane.
    expect(
      pursuitTailDirImpl(input, at(world, 11, 7), { col: 11, row: 7 }, { col: 10, row: 5 }, true),
    ).toBe('left')
    // gap 1, stagger 1: still in the yield phase (not staggered yet).
    expect(
      pursuitTailDirImpl(input, at(world, 11, 6), { col: 11, row: 6 }, { col: 10, row: 5 }, true),
    ).toBe(PURSUIT_TAIL_HOLD)
  })

  it('waits out the PIXEL stagger: a mid-cell target blocking the slide holds', () => {
    // along = -2 is a ROUNDED cell distance; a target mid-cell (y = 87 → cell
    // row 5, body reaching y=119) still physically overlaps the player's
    // slide footprint (y ∈ [112,144)). s21@30 t2475: handing these ticks to
    // directMove made it chase the target and grind the flank for ~1.5 s.
    // The target is RECEDING here — its own travel opens the gap, so hold.
    const { world, input } = setupWorld(AM3)
    const e = travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    e.y = 5 * CELL + 7 // 87: still cell row 5, body pokes into the slide band
    refresh(input, world)
    expect(
      pursuitTailDirImpl(input, at(world, 12, 7), { col: 12, row: 7 }, { col: 10, row: 5 }, true),
    ).toBe(PURSUIT_TAIL_HOLD)
  })

  it('slides the moment the pixel stagger actually opens (control)', () => {
    const { world, input } = setupWorld(AM3)
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    // Snapped target: body [80,112) exactly clears the slide band [112,144).
    expect(
      pursuitTailDirImpl(input, at(world, 12, 7), { col: 12, row: 7 }, { col: 10, row: 5 }, true),
    ).toBe('left')
  })

  it('engages only in the adjacent band (gap ≥ 3 hands back to the chase)', () => {
    const { world, input } = setupWorld(AM3)
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    expect(
      pursuitTailDirImpl(input, at(world, 13, 5), { col: 13, row: 5 }, { col: 10, row: 5 }, true),
    ).toBeNull()
  })

  it('the hold survives the exact pass tick (along = 0 skips the shot-line check)', () => {
    // laneShotClear steps AWAY from the target when the rows coincide; without
    // the along === 0 exemption the hold would flicker off for a tick and
    // re-arm directMove's sideways cut exactly as the target passes.
    const { world, input } = setupWorld(AM3)
    travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    expect(
      pursuitTailDirImpl(input, at(world, 12, 5), { col: 12, row: 5 }, { col: 10, row: 5 }, true),
    ).toBe(PURSUIT_TAIL_HOLD)
  })
})

// ---------------------------------------------------------------------------
// AlongMode=4 — the two self-healing-interruption fixes (user directive,
// 2026-08-29, after the am=3 enablement): (a) the tail keys on the LOCKED
// combat target when it diverges from HUNT's nav pick, (b) an active tail
// SLIDE preempts the ENGAGE/T2a turn (fire dropped while turned away).
// Both live behind am=4 so am=3 (the shipped default) stays byte-faithful.
// ---------------------------------------------------------------------------
describe('§302: AlongMode=4 — locked-target keying and T2a slide preemption', () => {
  const AM4 = { ...ISOLATED, ...M7, pursuitTailAlongMode: 4 }

  it('am=3: pursuitTailTargetCell returns navTarget untouched (archived arms)', () => {
    const { world, input } = setupWorld({ ...ISOLATED, ...M7, pursuitTailAlongMode: 3 })
    const e = travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    input._lastSelectTargetId = e.id
    const nav = { col: 20, row: 20 }
    expect(pursuitTailTargetCell(input, nav)).toBe(nav)
    expect(pursuitTailSlideDir(input, at(world, 12, 7))).toBeNull()
  })

  it('am=4: keys on the locked combat target over the nav pick', () => {
    const { world, input } = setupWorld(AM4)
    const e = travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    input._lastSelectTargetId = e.id
    expect(pursuitTailTargetCell(input, { col: 20, row: 20 })).toEqual({ col: 10, row: 5 })
  })

  it('am=4: falls back to navTarget with no lock or a dead locked tank', () => {
    const { world, input } = setupWorld(AM4)
    const e = travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    const nav = { col: 20, row: 20 }
    expect(pursuitTailTargetCell(input, nav)).toBe(nav)
    input._lastSelectTargetId = e.id
    e.alive = false
    expect(pursuitTailTargetCell(input, nav)).toBe(nav)
  })

  it('am=4: the slide preempts the T2a turn when the stagger is open', () => {
    const { world, input } = setupWorld(AM4)
    const e = travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    input._lastSelectTargetId = e.id
    // staggered (along = -2): a real slide command for the T2a call site.
    expect(pursuitTailSlideDir(input, at(world, 12, 7))).toBe('left')
  })

  it('am=4: HOLD does not preempt — the duel shot stands (level geometry)', () => {
    // (playerCell is per-tick cached, so each geometry gets a fresh world —
    // repositioning within one tick would read the stale cached cell.)
    const { world, input } = setupWorld(AM4)
    const e = travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    refresh(input, world)
    input._lastSelectTargetId = e.id
    expect(pursuitTailSlideDir(input, at(world, 12, 5))).toBeNull()
  })

  it('am=4: the slide respects the pixel-stagger wait (mid-cell target)', () => {
    const { world, input } = setupWorld(AM4)
    const e = travelling(placeEnemy(world, 10, 5), 10, 5, 'up')
    e.y = 5 * CELL + 7 // body pokes into the slide band → hold, not slide
    refresh(input, world)
    input._lastSelectTargetId = e.id
    expect(pursuitTailSlideDir(input, at(world, 12, 7))).toBeNull()
  })
})
