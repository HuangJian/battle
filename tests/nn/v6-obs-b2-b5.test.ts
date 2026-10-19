import { describe, expect, it } from 'bun:test'
import type { Direction } from '../../src/constants'
import type { World } from '../../src/game/World'
import { ObsEncoder } from '../../src/nn/obs-encoder'
import { POLICY_EXTRA_DIM, computePolicyExtra } from '../../src/nn/policy-extra'
import { createShieldWindow, SHIELD_CLASS_POWERUP } from '../../src/nn/shield-window'
import { windowKind } from '../../src/nn/danger-metrics'
import { POWERUP_DURATION_MS, RESPAWN_SHIELD_MS, STAR_SHIELD_GRACE_MS } from '../../src/constants'
import { Input } from '../../src/game/Input'
import { Simulation } from '../../src/game/Simulation'
import { cloneWorld, restoreWorld } from '../../src/snapshot/WorldSerializer'
import { clearArena, makeBullet, placeEnemy, positionPlayer, seedWorld } from '../helpers'

/**
 * B2/B3/B4/B5 acceptance (plan/nn-capability-upgrade.plan.md §4):
 *   · prevAction / lastHitDir / ticksSinceHit / nearestEnemyBeyond land in
 *     the exact scalar & extra slots the Python schema mirrors;
 *   · leadHitD predicts arrival-time hittability (straight-line extrapolation);
 *   · shield zeroes the threat families, freeze does not;
 *   · windowKind splits prop shield from the death-derived shields.
 */

function worldWithPlayer(): World {
  const w = seedWorld(11)
  w.startGame('classic', 'modern', 0)
  clearArena(w)
  w.tileMap.rebuildBaseCache()
  w.tanks.length = 0
  w.allies.length = 0
  w.bullets.length = 0
  w.spawnQueue.length = 0
  w.state = 'playing'
  positionPlayer(w, 12, 12, 'up')
  w.player!.prevMoveDir = 'up'
  return w
}

function enemyAt(w: World, col: number, row: number, dir: Direction) {
  return placeEnemy(w, col, row, 'basic', dir)
}

function pushEnemyBullet(w: World, col: number, row: number, dir: Direction): void {
  w.bullets.push(makeBullet({ x: col * 16, y: row * 16, dir, speed: 4 }))
}

function extra(w: World): Float32Array {
  const out = new Float32Array(POLICY_EXTRA_DIM)
  computePolicyExtra(w, out)
  return out
}

function scalars(w: World): Float32Array {
  const enc = new ObsEncoder()
  enc.encode(w)
  return enc.scalars
}

describe('B2 prevAction / lastHit / beyond', () => {
  it('prevAction lands in s40–s43 and extra 41–44', () => {
    const w = worldWithPlayer()
    const p = w.player!
    p.lastActionDir = 'left'
    p.lastActionHold = true
    p.lastActionFire = 1
    const s = scalars(w)
    expect(s[40]).toBe(1) // hold
    expect(s[41]).toBe(0) // fire none
    expect(s[42]).toBe(1) // fire before turn
    expect(s[43]).toBe(0)
    const ex = extra(w)
    expect(ex[41 + 2]).toBe(1) // prevActionLeft = 41 + DIR_INDEX('left'=2)
    expect(ex[41] + ex[42] + ex[43] + ex[44]).toBe(1)
  })

  it('no previous action = hold 0 / fire none 1 (zero-fill contract)', () => {
    const w = worldWithPlayer()
    const s = scalars(w)
    expect(s[40]).toBe(0)
    expect(s[41]).toBe(1)
    expect(s[42]).toBe(0)
    expect(s[43]).toBe(0)
    const ex = extra(w)
    expect(ex[41] + ex[42] + ex[43] + ex[44]).toBe(0)
  })

  it('lastHitDir + ticksSinceHit: same-frame hit = 0, never hit = saturated 1', () => {
    const w = worldWithPlayer()
    const p = w.player!
    const ex0 = extra(w)
    expect(ex0[45] + ex0[46] + ex0[47] + ex0[48]).toBe(0)
    expect(scalars(w)[44]).toBe(1) // never hit ⇒ 300/300

    p.lastHitDir = 'down'
    p.lastHitFrame = w.frame
    const ex = extra(w)
    expect(ex[45 + 1]).toBe(1) // lastHitDown = 45 + DIR_INDEX('down'=1)
    expect(scalars(w)[44]).toBe(0)
  })

  it('nearestEnemyBeyond > 0 when the nearest enemy faces away from me', () => {
    const w = worldWithPlayer()
    // Enemy two cells north of the player, facing north (up): I am behind it.
    enemyAt(w, 12, 10, 'up')
    const s = scalars(w)
    expect(s[45]).toBeGreaterThan(0.9)
  })

  it('nearestEnemyBeyond < 0 when the enemy faces me', () => {
    const w = worldWithPlayer()
    enemyAt(w, 12, 10, 'down') // faces south = at the player
    expect(scalars(w)[45]).toBeLessThan(-0.9)
  })

  it('snapshot round-trip keeps every B2 field (they are NOT in tickHash)', () => {
    const w = worldWithPlayer()
    const p = w.player!
    p.lastActionDir = 'right'
    p.lastActionHold = true
    p.lastActionFire = 1
    p.lastHitDir = 'left'
    p.lastHitFrame = 777
    p.lastHitDamage = 2
    const snap = cloneWorld(w)

    // 快照自己不能与 live World 共享引用：改 live 后快照值不动。
    p.lastActionDir = 'up'
    p.lastActionHold = false
    p.lastActionFire = 0
    p.lastHitDir = 'down'
    p.lastHitFrame = 888
    p.lastHitDamage = 1
    expect(snap.player!.lastActionDir).toBe('right')
    expect(snap.player!.lastActionHold).toBe(true)
    expect(snap.player!.lastActionFire).toBe(1)
    expect(snap.player!.lastHitDir).toBe('left')
    expect(snap.player!.lastHitFrame).toBe(777)
    expect(snap.player!.lastHitDamage).toBe(2)

    restoreWorld(w, snap)
    expect(w.player!.lastActionDir).toBe('right')
    expect(w.player!.lastActionHold).toBe(true)
    expect(w.player!.lastActionFire).toBe(1)
    expect(w.player!.lastHitDir).toBe('left')
    expect(w.player!.lastHitFrame).toBe(777)
    expect(w.player!.lastHitDamage).toBe(2)
  })
})

describe('B2 writers: the sim (not the caller) owns the fields', () => {
  class V6Input extends Input {
    private move: Direction | null = null
    private firing = false
    private hold = false
    private fireBeforeTurn = false

    set(move: Direction | null, firing = false, hold = false, fireBeforeTurn = false): void {
      this.move = move
      this.firing = firing
      this.hold = hold
      this.fireBeforeTurn = fireBeforeTurn
    }
    override getMoveDirection(): Direction | null {
      return this.move
    }
    override isFiring(): boolean {
      return this.firing
    }
    isHolding(): boolean {
      return this.hold
    }
    isFireBeforeTurn(): boolean {
      return this.fireBeforeTurn
    }
    usesHoldFire3(): boolean {
      return true
    }
  }

  it('a bullet that really damages the player writes lastHit* (max-damage source wins)', () => {
    const w = worldWithPlayer()
    const p = w.player!
    p.hp = 20
    // 同拍两个来源：左来的轻伤（2）与右来的重伤（5）⇒ 记重伤那一发。
    w.bullets.push(makeBullet({ x: p.x, y: p.y, dir: 'left', speed: 0, damage: 2 }))
    w.bullets.push(makeBullet({ x: p.x, y: p.y, dir: 'right', speed: 0, damage: 5 }))
    new Simulation(w, new Input()).tick()
    expect(p.lastHitDir).toBe('right')
    expect(p.lastHitDamage).toBe(5)
    expect(p.lastHitFrame).toBe(w.frame)
    expect(scalars(w)[44]).toBe(0) // 同帧 ⇒ ticksSinceHit = 0
  })

  it('prevAction = the REQUEST (hold / fire-before-turn), not the accepted turn', () => {
    const w = worldWithPlayer()
    const input = new V6Input()
    const sim = new Simulation(w, input)
    input.set('left', false, true)
    sim.tick()
    expect(w.player!.lastActionDir).toBe('left')
    expect(w.player!.lastActionHold).toBe(true)
    expect(w.player!.lastActionFire).toBe(0)

    input.set('right', true, false, true)
    sim.tick()
    expect(w.player!.lastActionDir).toBe('right')
    expect(w.player!.lastActionHold).toBe(false)
    expect(w.player!.lastActionFire).toBe(1)
  })
})

describe('B3 leadHitD: arrival-time hittability', () => {
  it('a laterally offset enemy that crosses the lane by arrival counts; too slow does not', () => {
    const w = worldWithPlayer()
    const p = w.player!
    // Enemy 6 cells north (96px ⇒ t* = 96/4 = 24 ticks), 30px left of the axis,
    // moving right at 1 px/tick ⇒ arrival offset -30+24 = -6px (inside 19px band).
    const e = enemyAt(w, 11, 6, 'right')
    e.x = p.x + p.w / 2 - 30 - 16 // center 30px left of the player's column
    e.vx = 1
    e.vy = 0
    expect(extra(w)[53]).toBe(1) // leadHitFront

    // Same geometry but stationary ⇒ never enters the band ⇒ 0.
    e.vx = 0
    expect(extra(w)[53]).toBe(0)
  })

  it('a brick on the shot line kills the lead prediction', () => {
    const w = worldWithPlayer()
    const p = w.player!
    const e = enemyAt(w, 11, 6, 'right')
    e.x = p.x + p.w / 2 - 30 - 16
    e.vx = 1
    // 炮口列 = 玩家中心格（x=208 ⇒ col 13）；砖必须落在这一列的弹道上。
    w.tileMap.set(13, 9, 'brick')
    expect(extra(w)[53]).toBe(0)
  })
})

describe('B4 shield zeroes the threat families; freeze does not', () => {
  it('prop shield ⇒ bullet/tank threat + tti + pincer = 0', () => {
    const w = worldWithPlayer()
    const p = w.player!
    pushEnemyBullet(w, 13, 9, 'down') // in front, approaching
    enemyAt(w, 13, 8, 'down') // muzzle on the player
    pushEnemyBullet(w, 9, 13, 'right') // left source
    pushEnemyBullet(w, 16, 13, 'left') // right source
    const before = extra(w)
    expect(before[0]).toBeGreaterThan(0)
    expect(before[37]).toBeGreaterThan(0)
    expect(before[8]).toBeGreaterThan(0)

    p.shieldTimer = POWERUP_DURATION_MS
    const after = extra(w)
    expect([after[0], after[1], after[2], after[3]]).toEqual([0, 0, 0, 0])
    expect([after[37], after[38], after[39], after[40]]).toEqual([0, 0, 0, 0])
    expect([after[13], after[14], after[15], after[16]]).toEqual([0, 0, 0, 0])
    expect(after[8]).toBe(0)
  })

  it('freeze does NOT zero (in-flight bullets still hit — plan §4-B4)', () => {
    const w = worldWithPlayer()
    pushEnemyBullet(w, 12, 9, 'down')
    w.freezeTimer = 1200
    expect(extra(w)[0]).toBeGreaterThan(0)
  })
})

describe('B5 windowKind splits propulsion shield from death-derived shields', () => {
  function windows(): ReturnType<typeof createShieldWindow>[] {
    return [createShieldWindow()]
  }

  it('none / freeze', () => {
    const w = worldWithPlayer()
    const [sw] = windows()
    sw.update(w)
    expect(windowKind(w, sw)).toBe('none')
    w.freezeTimer = 10
    expect(windowKind(w, sw)).toBe('freeze')
  })

  it('prop shield (the only reward-relevant shield) stays prop while decaying', () => {
    const w = worldWithPlayer()
    const p = w.player!
    const [sw] = windows()
    p.shieldTimer = POWERUP_DURATION_MS
    sw.update(w)
    expect(sw.classOf()).toBe(SHIELD_CLASS_POWERUP)
    expect(windowKind(w, sw)).toBe('propShield')
    p.shieldTimer = 2500 // decayed below the respawn threshold, class is sticky
    sw.update(w)
    expect(windowKind(w, sw)).toBe('propShield')
    p.shieldTimer = 0
    sw.update(w)
    expect(windowKind(w, sw)).toBe('none')
  })

  it('respawn shield and star grace are classified as death-derived', () => {
    const w = worldWithPlayer()
    const p = w.player!
    const sw = createShieldWindow()
    p.shieldTimer = RESPAWN_SHIELD_MS
    sw.update(w)
    expect(windowKind(w, sw)).toBe('respawnShield')
    p.shieldTimer = 0
    sw.update(w)
    p.shieldTimer = STAR_SHIELD_GRACE_MS
    sw.update(w)
    expect(windowKind(w, sw)).toBe('starShieldGrace')
  })
})
