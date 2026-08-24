import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { STAGES } from '../src/config/stages'
import type { Tank } from '../src/types'
import { CELL } from '../src/constants'
import { RNG } from '../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { travelFireDetourDir } from '../src/ai/god/ActionCandidates'
import { tankCenterCell } from '../src/ai/god/ThreatBudget'

/**
 * §217 travel-phase fire-line detour (open-test round 2).
 *
 * Same geometry convention as godai-candidates: tanks placed center-aligned
 * via (col−1)*CELL ⇒ tankCenterCell = (col,row). The base ring spans cols
 * 11..14 at row 23 and cols 11/14 at rows 24/25; the base eagle occupies
 * rows 24-25 / cols 12-13 (BASE_POS = {col: 12, row: 24}).
 */

function buildWorld(): World {
  const w = new World()
  w.seed = 7
  w.rng = new RNG(7)
  w.difficultyKey = 'hard'
  w.loadStageData(STAGES[0], 0)
  w.playerLevel = 3
  return w
}

function placePlayer(w: World, col: number, row: number, dir: 'up' | 'down' | 'left' | 'right') {
  const p = w.player!
  p.x = (col - 1) * CELL
  p.y = (row - 1) * CELL
  p.dir = dir
  p.lastTurnMs = -9999
  p.lastFire = -9999
  p.fireCooldown = 0
  return p
}

function addEnemy(
  w: World,
  col: number,
  row: number,
  kind: 'basic' | 'fast' | 'power' | 'armor' = 'basic',
): Tank {
  const e = w.createTank(kind, (col - 1) * CELL, (row - 1) * CELL, 'up')
  e.spawnTimer = 0
  e.alive = true
  w.tanks.push(e)
  return e
}

function enemies(w: World): Tank[] {
  return w.tanks.filter((t) => t.alive && t.spawnTimer <= 0 && !t.isPlayer)
}

/** 带内 fb 目标 (row ≥ 20, |col−12| ≤ 6) 值得 detour — csb/cbr 也全部落入此带。 */
function worthInBand(t: Tank): boolean {
  const tc = tankCenterCell(t)
  return tc.row >= 20 && Math.abs(tc.col - 12) <= 6
}

function detour(w: World, huntId = -1, worth: (t: Tank) => boolean = (t) => worthInBand(t)) {
  const p = w.player!
  return travelFireDetourDir(w, p, tankCenterCell(p), enemies(w), huntId, worth)
}

describe('travelFireDetourDir (§217)', () => {
  it('returns the turn direction for an aligned in-band target with positive slack', () => {
    const w = buildWorld()
    // 玩家 (10,21) 朝左, 带内 fast 敌 (10,20) — 同列相邻、走廊全空,
    // L3 一发击杀 (150HP vs 168dmg), slack 充裕 (deadline≈45 > 转弯+飞行≈17)。
    placePlayer(w, 10, 21, 'left')
    const e = addEnemy(w, 10, 20, 'fast')
    e.lastFire = 0
    const d = detour(w)
    expect(d).toBe('up')
    expect(e.alive).toBe(true)
  })

  it('returns null when already facing the target (baseline fires anyway)', () => {
    const w = buildWorld()
    placePlayer(w, 10, 21, 'up')
    addEnemy(w, 10, 20, 'fast')
    expect(detour(w)).toBeNull()
  })

  it('returns null when the target is out of the base approach band', () => {
    const w = buildWorld()
    // 同列但 row 8 — 带外 fb, 不值得为一个转弯窗偏离导航。
    placePlayer(w, 10, 21, 'left')
    addEnemy(w, 10, 8, 'fast')
    expect(detour(w)).toBeNull()
  })

  it('honors an explicit worth-gate override (cbr anywhere is worth it)', () => {
    const w = buildWorld()
    // 敌 (8,16) 对齐 col 8 — 带外; 模拟 cbr 语义 (worth=true) 时 detour 成立。
    placePlayer(w, 8, 21, 'left')
    addEnemy(w, 8, 16, 'fast')
    expect(detour(w, -1, () => true)).toBe('up')
  })

  it('returns null when terrain blocks the corridor; destroy clears it', () => {
    const w = buildWorld()
    // 玩家 (10,22) 朝左, 敌 (10,19): col 10 非环列 (环 = cols 11-14), 走廊含
    // (10,20) 砖 → 挡住。清砖后走廊全通且敌人仍是 fb (deadline 长) → detour。
    placePlayer(w, 10, 22, 'left')
    addEnemy(w, 10, 19, 'fast')
    expect(detour(w, -1, () => true)).toBeNull()
    w.tileMap.destroy(10, 20)
    expect(detour(w, -1, () => true)).toBe('up')
  })

  it('returns null when an intact ring brick blocks the ray (S30s27)', () => {
    const w = buildWorld()
    // 玩家 (12,21) 朝下, 敌 (12,17): 射线穿过完好环砖 (12,23)。
    placePlayer(w, 12, 21, 'left')
    addEnemy(w, 12, 17, 'fast')
    expect(detour(w)).toBeNull()
  })

  it('returns null when the ray crosses the base eagle (self-hit)', () => {
    const w = buildWorld()
    // 玩家 (11,24) 朝右, 敌 (14,24): 射线穿过 base 格 (12,24)。
    placePlayer(w, 11, 24, 'up')
    addEnemy(w, 14, 24, 'fast')
    expect(detour(w)).toBeNull()
  })

  it('returns null when killSlack does not beat the turn window', () => {
    const w = buildWorld()
    // basic 敌 (250HP) 需 2 发: L3 一发 168 + 重装 ~49.5t, 对齐即 csb 场景的
    // deadline (fireReady + 飞行 + 12t 余量) 装不下 — slack ≤ 13 → 不偏离。
    placePlayer(w, 10, 21, 'left')
    const e = addEnemy(w, 10, 19, 'basic')
    e.lastFire = 0
    expect(detour(w)).toBeNull()
  })

  it('prefers the last selectTarget hunt over a nearer band enemy', () => {
    const w = buildWorld()
    // 近敌 (11,21) (对齐行 21) 与 hunt (10,20) 距离同为 1 — huntId 决定目标:
    // 选 hunt → 转向 'up'; 若错选近敌 → 'right'。
    placePlayer(w, 10, 21, 'left')
    addEnemy(w, 11, 21, 'fast')
    const hunt = addEnemy(w, 10, 20, 'fast')
    hunt.lastFire = 0
    const d = detour(w, hunt.id)
    expect(d).toBe('up')
  })

  it('returns null when no enemy is aligned', () => {
    const w = buildWorld()
    placePlayer(w, 9, 21, 'left')
    addEnemy(w, 10, 19, 'fast')
    expect(detour(w)).toBeNull()
  })
})

describe('review P1: turn+fire semantics under the 200ms turn cooldown (§218)', () => {
  /**
   * The detour commit (think.ts §217) is exactly this input stream: set
   * `_moveDir` + `_fire` in the same tick. SimulationPlayer applies the
   * direction first (p.dir = dir), then tryFire spawns the bullet ALONG THE
   * NEW DIRECTION same-frame; SimulationCombat then defers the actual turn
   * when the 200ms cooldown is active (reverts p.dir + halts). A human
   * pressing turn+fire in one frame gets the identical processing — the AI
   * gains no extra capability. These tests pin that semantics.
   */

  function stageWorld(): { w: World; p: Tank; e: Tank } {
    const w = buildWorld()
    w.playerLevel = 3
    // The fairness test pins "one turn+fire frame = one shell"; use the
    // classic instant model so a single hit kills (pool-model damage at
    // level 3 leaves a fast enemy at 45/150 and muddies the assertion).
    w.rules.combatModel = 'instant'
    const e = addEnemy(w, 12, 20, 'fast')
    e.lastFire = 0
    // A brick between the enemy and the ring/base: the enemy is NOT csb/cbr,
    // so the defense branches decline and think reaches HUNT → the detour.
    w.tileMap.set(12, 22, 'brick')
    // The muzzle spawns the shell at the player's front edge (row 19) —
    // stage-1 tiles (5,9)/(6,9) are brick there; clear the strip so the
    // shell actually reaches the adjacent enemy.
    w.tileMap.destroy(11, 19)
    w.tileMap.destroy(12, 19)
    w.tileMap.destroy(13, 19)
    const p = placePlayer(w, 12, 21, 'down')
    p.prevMoveDir = 'down'
    p.spawnTimer = 0 // skip the stage-intro spawn countdown
    p.lastTurnMs = -100 // mid-cooldown: a turn request now is deferred (200ms rule)
    return { w, p, e }
  }

  it('real wiring: think lands the detour and the sim fires along the NEW dir, deferring the turn', () => {
    const { w, p } = stageWorld()
    const input = new GodAIInput(
      w,
      { ...DEFAULT_GOD_AI_PARAMS, fireLineDetourMode: 1, aimError: 0 },
      new RNG(123),
    )
    const sim = new Simulation(w, input)
    input.reset()
    // Force the stop-and-aim branches (ENGAGE/t2a, aggressive) off the table:
    // both suppressors decrement at the START of think (think.ts:3034/3036),
    // so set them to 2 — after the decrement they read 1 at the gate and the
    // branches decline. think then reaches HUNT and the detour claims the tick.
    input._antiCampSuppress = 2
    input._aggCampTrack.suppress = 2
    sim.tick()
    input.endFrame()
    expect(input._lastBranch).toBe('navigate') // the detour claims the tick
    // The detour turned RIGHT (think's playerCell() is the corner cell
    // (11,20) — the enemy at (12,20) is east of it). The shell spawned along
    // the NEW direction same-frame; the 200ms turn cooldown deferred the
    // visual turn (p.dir reverted to 'down', tank halted).
    const pb = w.bullets.filter((b) => b.alive && b.isPlayer)
    expect(pb.length).toBe(1)
    expect(pb[0].dir).toBe('right')
    expect(p.dir).toBe('down') // the 200ms turn is deferred — reverted
    expect(p.moving).toBe(false)
  })

  it('human turn+fire frame produces the byte-identical end state (no AI advantage)', () => {
    const { w, p, e } = stageWorld()
    const humanLike = {
      getMoveDirection: () => 'up' as const,
      isFiring: () => true,
      wasItemPressed: () => false,
      endFrame: () => {},
      reset: () => {},
    }
    const sim = new Simulation(w, humanLike)
    sim.tick()
    humanLike.endFrame()
    // The 'up' shell spawns INSIDE the adjacent enemy's hitbox (x 188-196,
    // y 312-320 vs enemy 176-208 × 304-336) and is consumed by the hit —
    // the fire went out along the NEW direction same-frame and landed.
    expect(e.alive).toBe(false)
    expect(w.bullets.filter((b) => b.alive && b.isPlayer).length).toBe(0)
    expect(p.dir).toBe('down') // the 200ms turn is deferred — reverted
    expect(p.moving).toBe(false)
  })

  it('the detour predicate commits during an active turn cooldown (killSlack>13 already covers the deferral)', () => {
    const w = buildWorld()
    w.playerLevel = 3
    const e = addEnemy(w, 12, 20, 'fast')
    e.lastFire = 0
    const p = placePlayer(w, 12, 21, 'left')
    p.lastTurnMs = -100 // mid-cooldown — the predicate must still commit
    expect(detour(w)).toBe('up')
  })
})
