import { setupGodGame } from './helpers'
import { describe, it, expect } from 'bun:test'
import { CELL, TICK_MS } from '../src/constants'
import type { Tank } from '../src/types'

/**
 * A3 — turn economy (plan/godai-post-a1-retune.plan.md §3.6 旁支 / DECISIONS §A3).
 *
 * `applyTurnCooldown` does two things when it refuses a turn: revert `dir` AND
 * set `moving = false` — the tank freezes for the rest of the 200 ms window.
 * Measured on hard (8 stages × 3 seeds): the God AI requests a turn it cannot
 * get on **17.7% of all ticks**, i.e. it stands still for no gain.
 *
 * Fix: `effectiveMoveDir()` keeps the branch intent in `_moveDir` but requests
 * the COMMITTED facing instead, so the tank keeps moving and the turn is
 * re-requested the moment the cooldown expires. Two pins:
 *
 *   1. the substitution happens exactly when `turnWillCommit` says the turn
 *      would be refused (otherwise the branch direction is returned untouched);
 *   2. a refused DODGE must stay a stop — if the committed facing walks into an
 *      enemy bullet, the effective request is null (mode 2). Mode 1 substitutes
 *      unconditionally and measurably loses on Steel Web.
 *   3. the shot set is unchanged: `isFiring()` suppresses the shot when the
 *      effective direction differs from the branch's aim.
 */

function godWorld(seed = 42, mode = 2) {
  const g = setupGodGame({ seed, params: { turnEconomyMode: mode, turnCommitFireGate: 1 } })
  return { world: g.world, input: g.input, p: g.world.player! }
}

/** Park the player at a cell, facing `dir`, and age the turn cooldown past cd. */
function park(p: Tank, dir: 'up' | 'down' | 'left' | 'right', ageMs: number) {
  p.x = 8 * CELL
  p.y = 8 * CELL
  p.dir = dir
  p.prevMoveDir = dir
  p.moving = true
  p.lastTurnMs = -9999
  return ageMs
}

describe('A3 转身经济 — 拿不到的转身不请求（别让引擎站桩）', () => {
  it('turn available ⇒ the branch direction is requested untouched', () => {
    const { world, input, p } = godWorld()
    park(p, 'up', 0)
    world.frame = 100
    p.lastTurnMs = world.frame * TICK_MS - 500 // cooldown long expired
    input._moveDir = 'left'
    expect(input.effectiveMoveDir()).toBe('left')
  })

  it('turn refused ⇒ request the committed facing instead of freezing', () => {
    const { world, input, p } = godWorld()
    park(p, 'up', 0)
    world.frame = 100
    // A turn accepted 2 ticks ago ⇒ 200 ms cooldown still active.
    p.lastTurnMs = world.frame * TICK_MS - 2 * TICK_MS
    input._moveDir = 'left'
    expect(input.effectiveMoveDir()).toBe('up') // keep moving, don't halt
  })

  it('mode 0 (A2 baseline) never substitutes', () => {
    const { world, input, p } = godWorld(42, 0)
    park(p, 'up', 0)
    world.frame = 100
    p.lastTurnMs = world.frame * TICK_MS - 2 * TICK_MS
    input._moveDir = 'left'
    expect(input.effectiveMoveDir()).toBe('left')
  })

  it('a refused DODGE stays a stop: committed facing crossed by a bullet ⇒ null', () => {
    const { world, input, p } = godWorld()
    park(p, 'up', 0)
    world.frame = 100
    p.lastTurnMs = world.frame * TICK_MS - 2 * TICK_MS
    input._moveDir = 'left'
    // Enemy bullet descending the player's own column, one cell above.
    world.bullets.push({
      id: 991,
      x: p.x + 4,
      y: p.y - CELL,
      w: 8,
      h: 8,
      dir: 'down',
      alive: true,
      ownerId: 991,
      ownerKind: 'basic',
      isPlayer: false,
      allegiance: 'enemy',
      speed: 4,
      power: 1,
      damage: 1,
    } as never)
    expect(input.effectiveMoveDir()).toBe(null) // explicit stop — halt IS the dodge
  })

  it('no shot is fired along the substituted direction (shot set unchanged)', () => {
    const { world, input, p } = godWorld()
    park(p, 'up', 0)
    world.frame = 100
    p.lastTurnMs = world.frame * TICK_MS - 2 * TICK_MS
    input._moveDir = 'left'
    input._fire = true
    expect(input.effectiveMoveDir()).toBe('up')
    expect(input.isFiring()).toBe(false) // aimed at 'left', requesting 'up' ⇒ hold fire
  })
})
