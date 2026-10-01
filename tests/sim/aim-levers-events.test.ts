/**
 * aim-levers-events.test.ts — aim-dodge-levers P2 事件扩展的行为单测（plan §7.1）。
 *
 * 钉住 5 个 additive 只读字段/事件（全部不回流 gameplay）：
 *   · `player_damage.bulletId`（非致死承伤归因口）；
 *   · `player_hit.bulletId`（致死 + 星盾消耗两条路径；P0-A：旧 `spendStarShield(tank)`
 *     拿不到弹 ⇒ 星盾承伤静默漏记）；
 *   · `bullet_cancelled{aId,bId}`（对消唯一归因口，替代几何近似）；
 *   · `terrain_destroyed.bulletId`（`aimBricks` 归因；一发 2×2 多格 ⇒ 多条同 id，
 *     settle-once 由导出器按弹去重）。
 *
 * 夹具：`startGame('hard')` + `clearArena` 清场 + 把待测弹直接注入 `world.bullets`
 * （speed 0 ⇒ 不位移，命中判定在注入位置发生）。
 */
import { describe, expect, it } from 'bun:test'
import { CELL, STAR_SHIELD_GRACE_MS } from '../../src/constants'
import { PLAYER_PROGRESSION } from '../../src/config/combat'
import { Input } from '../../src/game/Input'
import { Simulation } from '../../src/game/Simulation'
import type { World } from '../../src/game/World'
import type { GameEvent } from '../../src/types'
import { clearArena, makeBullet, positionPlayer, seedWorld } from '../helpers'

/** 可跑的「playing」世界 + Simulation（标准夹具；玩家固定在 (5,5)）。 */
function playing(seed = 1): { w: World; sim: Simulation } {
  const w = seedWorld(seed)
  w.startGame('hard', 'modern', 0)
  clearArena(w)
  positionPlayer(w, 5, 5)
  w.state = 'playing'
  return { w, sim: new Simulation(w, new Input()) }
}

/** 本 tick 某类事件（强类型收窄）。 */
function eventsOf<T extends GameEvent['type']>(
  w: World,
  type: T,
): Array<Extract<GameEvent, { type: T }>> {
  return w.events.items.filter((e) => e.type === type) as Array<Extract<GameEvent, { type: T }>>
}

describe('player_damage / player_hit 携带 bulletId', () => {
  it('非致死承伤：player_damage.bulletId === 该弹 id', () => {
    const { w, sim } = playing()
    const p = w.player!
    const hp0 = p.hp
    const b = makeBullet({ x: p.x, y: p.y, dir: 'down', speed: 0, damage: 5 })
    w.bullets.push(b)
    sim.tick()
    const dmg = eventsOf(w, 'player_damage')
    expect(dmg.length).toBe(1)
    expect(dmg[0]!.damage).toBe(5)
    expect(dmg[0]!.bulletId).toBe(b.id)
    expect(p.hp).toBe(hp0 - 5)
    expect(p.alive).toBe(true)
  })

  it('致死命中：player_hit.bulletId === 该弹 id', () => {
    const { w, sim } = playing()
    const p = w.player!
    p.hp = 3
    const b = makeBullet({ x: p.x, y: p.y, dir: 'down', speed: 0, damage: 5 })
    w.bullets.push(b)
    sim.tick()
    const hit = eventsOf(w, 'player_hit')
    expect(hit.length).toBe(1)
    expect(hit[0]!.bulletId).toBe(b.id)
    expect(p.alive).toBe(false)
  })

  it('星盾消耗（spendStarShield）：player_hit.bulletId 携带 + 星盾 grace 落地（P0-A）', () => {
    const { w, sim } = playing()
    const p = w.player!
    p.level = PLAYER_PROGRESSION.maximumLevel
    p.hp = 3
    const b = makeBullet({ x: p.x, y: p.y, dir: 'down', speed: 0, damage: 5 })
    w.bullets.push(b)
    sim.tick()
    const hit = eventsOf(w, 'player_hit')
    expect(hit.length).toBe(1)
    expect(hit[0]!.bulletId).toBe(b.id) // 旧签名下这里是 undefined（漏结算）
    expect(p.alive).toBe(true)
    expect(p.level).toBe(PLAYER_PROGRESSION.maximumLevel - 1)
    expect(p.shieldTimer).toBe(STAR_SHIELD_GRACE_MS)
  })

  it('非弹源路径不误报：无子弹推进 ⇒ 两类事件都空', () => {
    const { w, sim } = playing(2)
    sim.tick()
    expect(eventsOf(w, 'player_damage').length).toBe(0)
    expect(eventsOf(w, 'player_hit').length).toBe(0)
  })
})

describe('bullet_cancelled：对消真事件', () => {
  it('弹弹对消 ⇒ {aId,bId} 精确、双方 alive=false、只推一条', () => {
    const { w, sim } = playing()
    const p = w.player!
    const pb = makeBullet({
      x: p.x,
      y: p.y - 4 * CELL,
      dir: 'up',
      speed: 0,
      isPlayer: true,
      allegiance: 'player',
      ownerKind: 'player',
    })
    const eb = makeBullet({ x: p.x, y: p.y - 4 * CELL, dir: 'down', speed: 0 })
    w.bullets.push(pb, eb)
    sim.tick()
    const ev = eventsOf(w, 'bullet_cancelled')
    expect(ev.length).toBe(1)
    // 处理顺序：数组在前者被处理，a = 当前弹、b = 对侧弹。
    expect(ev[0]!.aId).toBe(pb.id)
    expect(ev[0]!.bId).toBe(eb.id)
    expect(pb.alive).toBe(false)
    expect(eb.alive).toBe(false)
  })

  it('同方两弹不推事件（对消只跨阵营）', () => {
    const { w, sim } = playing(3)
    const p = w.player!
    const b1 = makeBullet({ x: p.x, y: p.y - 4 * CELL, dir: 'up', speed: 0 })
    const b2 = makeBullet({ x: p.x, y: p.y - 4 * CELL, dir: 'down', speed: 0 })
    w.bullets.push(b1, b2)
    sim.tick()
    expect(eventsOf(w, 'bullet_cancelled').length).toBe(0)
    expect(b1.alive).toBe(true)
    expect(b2.alive).toBe(true)
  })
})

describe('terrain_destroyed：bulletId 归因（aimBricks 输入）', () => {
  it('一发拆 2×2 四格 ⇒ 四条事件、同 bulletId（消费侧 settle-once 去重）', () => {
    const { w, sim } = playing()
    for (const [c, r] of [
      [3, 3],
      [4, 3],
      [3, 4],
      [4, 4],
    ] as const) {
      w.tileMap.set(c, r, 'brick')
    }
    // 弹矩形 6×6 跨过 (3|4,3|4) 的 2×2 网格交点：x=59 ⇒ 列 3..4、行 3..4。
    const b = makeBullet({
      x: 3 * CELL + 11,
      y: 3 * CELL + 11,
      dir: 'down',
      speed: 0,
      isPlayer: true,
      allegiance: 'player',
      ownerKind: 'player',
    })
    w.bullets.push(b)
    sim.tick()
    const ev = eventsOf(w, 'terrain_destroyed')
    expect(ev.length).toBe(4)
    for (const e of ev) expect(e.bulletId).toBe(b.id)
    expect(b.alive).toBe(false)
    // 四格真的清掉了
    expect(w.tileMap.get(3, 3)).toBe('empty')
    expect(w.tileMap.get(4, 4)).toBe('empty')
  })

  it('打钢未破（power < 2）⇒ 无事件（该弹归 miss 的输入）', () => {
    const { w, sim } = playing(4)
    w.tileMap.set(3, 3, 'steel')
    const b = makeBullet({
      x: 3 * CELL + 11,
      y: 3 * CELL + 11,
      dir: 'down',
      speed: 0,
      isPlayer: true,
      allegiance: 'player',
      ownerKind: 'player',
      power: 1,
    })
    w.bullets.push(b)
    sim.tick()
    expect(eventsOf(w, 'terrain_destroyed').length).toBe(0)
    expect(w.tileMap.get(3, 3)).toBe('steel')
    expect(b.alive).toBe(false)
  })

  it('破钢（power ≥ 2）⇒ 一条事件带 bulletId', () => {
    const { w, sim } = playing(5)
    w.tileMap.set(3, 3, 'steel')
    const b = makeBullet({
      x: 3 * CELL + 11,
      y: 3 * CELL + 11,
      dir: 'down',
      speed: 0,
      isPlayer: true,
      allegiance: 'player',
      ownerKind: 'player',
      power: 2,
    })
    w.bullets.push(b)
    sim.tick()
    const ev = eventsOf(w, 'terrain_destroyed')
    expect(ev.length).toBe(1)
    expect(ev[0]!.bulletId).toBe(b.id)
    expect(w.tileMap.get(3, 3)).toBe('empty')
  })
})
