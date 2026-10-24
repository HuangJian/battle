import { describe, it, expect } from 'bun:test'
import { crossfireEscapeDirImpl } from '../src/ai/god/crossfire'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { World } from '../src/game/World'
import { STAGES } from '../src/config/stages'
import { BULLET, CELL } from '../src/constants'
import { genId } from '../src/game/World'
import { clearArena, positionPlayer } from './helpers'

/**
 * A12 — 多扇区交叉火逃逸：判据必须是「步后最早到弹的 TTI 最大」。
 *
 * 反例（本文件的核心理由）：一发**2 格外的垂直弹** + 一发**6 格外的水平弹**同时覆盖。
 * 「按轴计数」给两条轴各记 1 发 ⇒ 平手 ⇒ 取 ALL_DIRS 里第一个可走方向 `up`，
 * 而那正是**走近最急那发**的方向。正确判据比较「移动到新格后，最早到达的弹还有几拍」，
 * 会选左右（20 拍）而不是上下（8 拍）。
 */

function setup() {
  const w = new World()
  w.rng.reseed(3)
  w.loadStageData(STAGES[0], 0)
  clearArena(w)
  positionPlayer(w, 8, 8, 'up')
  const ai = new GodAIInput(w, { ...DEFAULT_GOD_AI_PARAMS, crossfireSectorMode: 1 })
  ai.reset()
  return { w, ai }
}

function addBullet(w: World, col: number, row: number, dir: 'up' | 'down' | 'left' | 'right') {
  const b = {
    id: genId(),
    x: col * CELL + CELL / 2 - BULLET / 2,
    y: row * CELL + CELL / 2 - BULLET / 2,
    w: BULLET,
    h: BULLET,
    dir,
    alive: true,
    ownerId: -1,
    ownerKind: 'basic' as const,
    isPlayer: false,
    allegiance: 'enemy' as const,
    speed: 4,
    power: 1,
    damage: 1,
  }
  w.bullets.push(b as never)
  return b
}

describe('A12 — 交叉火逃逸判据', () => {
  it('escapes the lane of the EARLIEST shell, not the lane with the fewest shells', () => {
    const { w, ai } = setup()
    // Vertical shell 2 cells above, travelling down: ~8 ticks to the player.
    addBullet(w, 8, 6, 'down')
    // Horizontal shell 6 cells left, travelling right: ~24 ticks away.
    addBullet(w, 2, 8, 'right')
    const pcx = 8 * CELL + CELL
    const pcy = 8 * CELL + CELL
    const dir = crossfireEscapeDirImpl(ai, pcx, pcy)
    // `up` walks INTO the imminent vertical shell — the axis-count metric picks
    // exactly that. The TTI metric must pick a horizontal step.
    expect(dir).not.toBe('up')
    expect(dir === 'left' || dir === 'right').toBe(true)
  })

  it('returns null when there is no crossfire (single inbound sector)', () => {
    const { w, ai } = setup()
    addBullet(w, 8, 6, 'down')
    const dir = crossfireEscapeDirImpl(ai, 8 * CELL + CELL, 8 * CELL + CELL)
    expect(dir).toBe(null)
  })
})
