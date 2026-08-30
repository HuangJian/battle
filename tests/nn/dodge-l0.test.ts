import { describe, it, expect } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { World } from '../../src/game/World'
import { dodgeL0 } from '../../src/nn/dodge-l0'
import { makeArena } from '../../src/nn/arena-ladder'
import { CELL, type Direction } from '../../src/constants'
import type { Tank } from '../../src/types'

// ============================================================
// dodge-l0（plan/goal-nn-action.md 卡 A3，§3.5(e) 独立保底层）
// ============================================================

/** 最小敌方子弹 mock（dodgeL0 只读 alive/x/y/w/h/dir/allegiance）。 */
function bullet(dir: Direction, x: number, y: number, speed = 4): any {
  return { alive: true, x, y, w: 6, h: 6, dir, allegiance: 'enemy', speed }
}

/** 装载 size-12 开放 arena 并把玩家放到指定格。 */
function arenaWorld(pcol: number, prow: number): { world: World; player: Tank } {
  const world = new World()
  world.rng.reseed(42)
  world.difficultyKey = 'hard'
  world.loadStageData(makeArena({ size: 12, enemyCount: 1 }), 0)
  const p = world.player!
  p.x = pcol * CELL
  p.y = prow * CELL
  return { world, player: p }
}

describe('dodge-l0: 触发场景（卡 A3 验收）', () => {
  it('敌弹沿玩家所在列逼近（3 格）→ 横移出弹道（perp）', () => {
    // offset=7，开放区行 7..18；玩家放 (13, 13)，敌弹在其上方 3 格、同列、向下逼近
    const { world, player } = arenaWorld(13, 13)
    world.bullets.push(bullet('down', player.x + player.w / 2 - 3, player.y - 3 * CELL))
    const d = dodgeL0(world, 'up') // 采样动作 = 迎弹前进（最差情形）
    expect(d.triggered).toBe(true)
    expect(d.reason).toBe('perp')
    expect(d.dir === 'left' || d.dir === 'right').toBe(true)
  })

  it('采样动作已出弹道（横移）→ 不覆盖', () => {
    const { world, player } = arenaWorld(13, 13)
    world.bullets.push(bullet('down', player.x + player.w / 2 - 3, player.y - 3 * CELL))
    const d = dodgeL0(world, 'left')
    expect(d.triggered).toBe(false)
  })

  it('两侧均被钢墙封死 → 沿弹道后退（retreat，距离变大者胜，绝不迎弹）', () => {
    const { world, player } = arenaWorld(13, 13)
    // 左右封路（坦克占 2 格宽 + 一步 sweep ⇒ 墙放 col 11 / 16 才恰好挡住横移）
    const t = world.tileMap
    t.set(11, 13, 'steel')
    t.set(16, 13, 'steel')
    world.bullets.push(bullet('down', player.x + player.w / 2 - 3, player.y - 3 * CELL))
    const d = dodgeL0(world, 'up') // 采样 = 迎弹
    expect(d.triggered).toBe(true)
    expect(d.reason).toBe('retreat')
    expect(d.dir).toBe('down') // 后退 = 沿弹道方向远离子弹
  })

  it('无弹道威胁 → 绝不覆盖（覆盖率纪律）', () => {
    const { world } = arenaWorld(13, 13)
    expect(dodgeL0(world, 'up').triggered).toBe(false)
    // 远弹（>6 格）不触发
    const { world: w2, player: p2 } = arenaWorld(13, 13)
    w2.bullets.push(bullet('down', p2.x + p2.w / 2 - 3, p2.y - 8 * CELL))
    expect(dodgeL0(w2, 'up').triggered).toBe(false)
  })

  it('确定性：同状态同决策 → 同结果', () => {
    const build = (): DodgeLike => {
      const { world, player } = arenaWorld(13, 13)
      world.bullets.push(bullet('down', player.x + player.w / 2 - 3, player.y - 3 * CELL))
      return dodgeL0(world, 'up')
    }
    const a = build()
    const b = build()
    expect(a).toEqual(b)
  })
})

interface DodgeLike {
  triggered: boolean
  dir: string | null
  reason: string
}

describe('dodge-l0: 白名单哨兵（§3.5 F1 硬边界）', () => {
  it('模块源码（剥离注释后）不 import GodAIInput / ThreatAssessor / god 链', () => {
    const raw = readFileSync(join(import.meta.dir, '../../src/nn/dodge-l0.ts'), 'utf8')
    // 剥离块注释 + 行注释，只扫代码——注释里的白名单说明不算引用。
    const src = raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')
    expect(src).not.toMatch(/GodAIInput/)
    expect(src).not.toMatch(/ThreatAssessor/)
    expect(src).not.toMatch(/think\(/)
    expect(src).not.toMatch(/_lastBranch/)
    expect(src).not.toMatch(/_enemies|_threatCache|_scanCache/)
    // 只准 import perception 基元（canStep）——ai/ 下的其它模块一律不准
    const aiImports = [...src.matchAll(/from '(\.\.\/ai\/[^']+)'/g)].map((m) => m[1])
    expect(aiImports).toEqual(['../ai/perception'])
  })
})
