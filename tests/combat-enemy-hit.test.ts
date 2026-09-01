/**
 * combat-enemy-hit.test.ts — enemy_hit 事件验证（plan/dodge-item-reward-v2.md §9.1）。
 *
 * 验证三种情况：
 * 1. 非致死命中（hp>0）→ 推 enemy_hit + 不推 tank_destroyed
 * 2. 致死命中（hp≤0）→ 推 enemy_hit + 推 tank_destroyed（by player）
 * 3. 盾弹开 → 两者都不推
 */
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { CELL } from '../src/constants'
import { seedWorld } from './helpers'

describe('enemy_hit 事件', () => {
  function setup(): { world: World; sim: Simulation; fire: () => void } {
    const world = seedWorld(42)
    const input = new Input()
    const sim = new Simulation(world, input)
    world.startGame('hard', 'modern', 0)
    // 清空所有敌人，只留玩家
    world.spawnQueue.length = 0
    world.tanks.length = 0
    const player = world.player!
    player.spawnTimer = 0
    // 清除障碍物，留出通道
    for (let r = 0; r <= 25; r++) {
      world.tileMap.set(8, r, 'empty')
      world.tileMap.set(9, r, 'empty')
    }
    // 玩家朝上（往敌人方向开火）
    player.y = 24 * CELL
    player.x = 8 * CELL
    player.dir = 'up'
    // 着火键
    const fire = (): void => {
      ;(
        input as unknown as {
          onKeyDown: (e: { code: string; preventDefault: () => void }) => void
        }
      ).onKeyDown({ code: input.keys.fire, preventDefault: () => {} })
    }
    return { world, sim, fire }
  }

  function spawnEnemy(world: World, kind: string, hp: number, shield = false): void {
    const enemy = world.createTank(kind as any, 8 * CELL, 2 * CELL, 'down')
    enemy.spawnTimer = 0
    enemy.hp = hp
    enemy.maxHp = hp
    if (shield) enemy.shieldTimer = 1e9
    world.tanks.push(enemy)
  }

  it('非致死命中：推 enemy_hit + 不推 tank_destroyed', () => {
    const { world, sim, fire } = setup()
    spawnEnemy(world, 'basic', 999) // 高 HP 确保非致死
    // 给玩家盾避免被敌人打死
    world.player!.shieldTimer = 1e9
    fire()
    let enemyHit = false
    let tankDestroyed = false
    for (let t = 0; t < 600; t++) {
      sim.tick()
      for (const e of world.consumeEvents()) {
        if (e.type === 'enemy_hit') enemyHit = true
        if (e.type === 'tank_destroyed') tankDestroyed = true
      }
      if (enemyHit) break
    }
    expect(enemyHit).toBe(true)
    expect(tankDestroyed).toBe(false)
  })

  it('致死命中：推 enemy_hit + 推 tank_destroyed（by player）', () => {
    const { world, sim, fire } = setup()
    spawnEnemy(world, 'basic', 1) // 1 HP，一枪必死
    world.player!.shieldTimer = 1e9
    fire()
    let enemyHit = false
    let tankDestroyedByPlayer = false
    for (let t = 0; t < 600; t++) {
      sim.tick()
      for (const e of world.consumeEvents()) {
        if (e.type === 'enemy_hit') enemyHit = true
        if (e.type === 'tank_destroyed' && e.by === 'player') tankDestroyedByPlayer = true
      }
      if (tankDestroyedByPlayer) break
    }
    expect(enemyHit).toBe(true)
    expect(tankDestroyedByPlayer).toBe(true)
  })

  it('盾弹开：两者都不推', () => {
    const { world, sim, fire } = setup()
    spawnEnemy(world, 'basic', 1, true) // 有盾
    world.player!.shieldTimer = 1e9
    fire()
    let enemyHit = false
    let tankDestroyed = false
    for (let t = 0; t < 600; t++) {
      sim.tick()
      for (const e of world.consumeEvents()) {
        if (e.type === 'enemy_hit') enemyHit = true
        if (e.type === 'tank_destroyed') tankDestroyed = true
      }
    }
    // 有盾的敌人，子弹应被弹开，不产生 enemy_hit 或 tank_destroyed
    expect(enemyHit).toBe(false)
    expect(tankDestroyed).toBe(false)
  })
})
